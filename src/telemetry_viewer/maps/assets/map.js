/* Session coordinates stay inside this local WebEngine page. Only tile requests leave it. */
'use strict';
const map = L.map('map', {preferCanvas:true}).setView([0,0],2);
// A moving marker must not repaint the entire static route canvas on each hover.
const routeRenderer = L.canvas({padding:0.1});
const markerRenderer = L.svg({padding:0.1});
let bridge = null, route = null, marker = null, tiles = null;
let markerRow = -1, ready = false;
const notice = document.getElementById('notice');
function selectLocation(event) {
    if (bridge) bridge.locationClicked(event.latlng.lat, event.latlng.lng);
}
map.on('click', selectLocation);
window.setRoute = function(segments) {
    if(route) map.removeLayer(route);
    if(marker) { map.removeLayer(marker); marker=null; }
    route=L.featureGroup();
    for(const segment of segments) {
        if(segment.length === 1) {
            L.circleMarker(segment[0],{radius:4,color:'#39c9bc',renderer:routeRenderer}).addTo(route);
        } else {
            L.polyline(segment,{color:'#39c9bc',weight:4,renderer:routeRenderer}).addTo(route);
        }
    }
    route.addTo(map);
    const bounds=route.getBounds();
    if(bounds.isValid()) map.fitBounds(bounds.pad(0.12),{maxZoom:17});
    else notice.textContent='No valid GPS coordinates in this session';
};
window.setMarker = function(lat, lon, row) {
    markerRow = row;
    if(!marker) marker=L.circleMarker([lat,lon],{radius:7,color:'#ffffff',weight:2,fillColor:'#ffb65a',fillOpacity:1,renderer:markerRenderer}).addTo(map);
    else marker.setLatLng([lat,lon]);
    marker.bindTooltip('Nearest recorded GPS change · row '+row);
};
window.setLayer = function(mode, url, attribution) {
    if(tiles) { map.removeLayer(tiles); tiles=null; }
    if(mode==='Offline') {notice.textContent='Offline route · no map tiles requested'; return;}
    const template=mode==='Road'?'https://tile.openstreetmap.org/{z}/{x}/{y}.png':url;
    const credit=mode==='Road'?'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors':attribution;
    tiles=L.tileLayer(template,{attribution:credit,maxZoom:19,detectRetina:false});
    tiles.on('tileerror',()=>{notice.textContent='Map tiles unavailable · route and cursor remain usable';});
    tiles.on('load',()=>{notice.textContent=mode+' · marker uses nearest recorded GPS change';});
    tiles.addTo(map);
    notice.textContent='Loading '+mode.toLowerCase()+' tiles · route available offline';
};
new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge=channel.objects.bridge;
    ready=true;
    bridge.ready();
});
