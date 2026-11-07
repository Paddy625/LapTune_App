/* Session coordinates stay inside this local WebEngine page. Only tile requests leave it. */
'use strict';
const map = L.map('map', {preferCanvas:true}).setView([0,0],2);
// A moving marker must not repaint the entire static route canvas on each hover.
const routeRenderer = L.canvas({padding:0.1});
const markerRenderer = L.svg({padding:0.1});
const rangeRenderer = L.svg({padding:0.1});
let bridge = null, route = null, marker = null, tiles = null;
let visibleRange = null;
const rangeMarks = L.featureGroup().addTo(map);
let markerRow = -1, ready = false;
let estimatedRoute = null, estimatedMarker = null, estimatedUncertainty = null;
let estimatedRow = -1, trackMode = 'GPS + CALC';
const estimatedRenderer = L.canvas({padding:0.1});
let estimatedLegend = null;
const notice = document.getElementById('notice');
function selectLocation(event) {
    if (bridge) bridge.locationClicked(event.latlng.lat, event.latlng.lng);
}
map.on('click', selectLocation);
window.setRoute = function(segments) {
    if(estimatedRoute) {map.removeLayer(estimatedRoute);estimatedRoute=null;}
    if(estimatedMarker) {map.removeLayer(estimatedMarker);estimatedMarker=null;}
    if(estimatedUncertainty) {map.removeLayer(estimatedUncertainty);estimatedUncertainty=null;}
    if(estimatedLegend) {map.removeControl(estimatedLegend);estimatedLegend=null;}
    estimatedRow=-1;
    if(route) map.removeLayer(route);
    if(marker) { map.removeLayer(marker); marker=null; }
    rangeMarks.clearLayers();
    visibleRange=null;
    route=L.featureGroup();
    for(const segment of segments) {
        if(segment.length === 1) {
            L.circleMarker(segment[0],{radius:4,color:'#ff1631',renderer:routeRenderer}).addTo(route);
        } else {
            L.polyline(segment,{color:'#ff1631',weight:4,renderer:routeRenderer}).addTo(route);
        }
    }
    route.addTo(map);
    const bounds=route.getBounds();
    if(bounds.isValid()) map.fitBounds(bounds.pad(0.12),{maxZoom:17});
    else notice.textContent='No valid GPS coordinates in this session';
    setTrackMode(trackMode);
};
window.setMarker = function(lat, lon, row) {
    markerRow = row;
    if(!marker) marker=L.circleMarker([lat,lon],{radius:7,color:'#ff1631',weight:3,fillColor:'#ffffff',fillOpacity:1,renderer:markerRenderer}).addTo(map);
    else marker.setLatLng([lat,lon]);
    marker.bindTooltip('Nearest recorded GPS change · row '+row);
    setTrackMode(trackMode);
};
window.setTrackMode = function(mode) {
    trackMode=mode;
    for(const layer of [route,marker]) {
        if(layer) {if(mode==='CALC') map.removeLayer(layer);else if(!map.hasLayer(layer)) layer.addTo(map);}
    }
    for(const layer of [estimatedRoute,estimatedMarker,estimatedUncertainty]) {
        if(layer) {if(mode==='GPS') map.removeLayer(layer);else if(!map.hasLayer(layer)) layer.addTo(map);}
    }
    if(estimatedLegend) estimatedLegend.getContainer().style.display=mode==='GPS'?'none':'';
};
window.setEstimatedRoute = function(parts) {
    if(estimatedRoute) map.removeLayer(estimatedRoute);
    estimatedRoute=L.featureGroup();
    for(const part of parts) {
        const options={color:part.status===3?'#ffb347':'#54d7ef',weight:3,
            dashArray:part.status===2?'7 5':part.status===3?'2 5':null,renderer:estimatedRenderer};
        if(part.points.length===1) L.circleMarker(part.points[0],{...options,radius:3}).addTo(estimatedRoute);
        else if(part.points.length>1) L.polyline(part.points,options).addTo(estimatedRoute);
    }
    if(!estimatedLegend) {
        estimatedLegend=L.control({position:'bottomright'});
        estimatedLegend.onAdd=()=>{const div=L.DomUtil.create('div');div.style.cssText='background:#111214df;color:white;padding:6px;border-radius:4px;font:12px sans-serif;max-width:210px;margin-bottom:42px';
            div.innerHTML='<span style="color:#ff1631">━ GPS</span> · <span style="color:#54d7ef">━ CALC</span><br>Dashed: GPS loss<br><span style="color:#ffb347">Orange: low confidence</span>';L.DomEvent.disableClickPropagation(div);return div;};
        estimatedLegend.addTo(map);
    }
    setTrackMode(trackMode);
};
window.setEstimatedMarker = function(lat,lon,radius,status,row) {
    if(lat===null) {
        if(estimatedMarker) map.removeLayer(estimatedMarker);
        if(estimatedUncertainty) map.removeLayer(estimatedUncertainty);
        estimatedMarker=estimatedUncertainty=null;estimatedRow=-1;return;
    }
    estimatedRow=row;
    const point=[lat,lon],color=status===3?'#ffb347':'#54d7ef';
    if(!estimatedMarker) estimatedMarker=L.circleMarker(point,{radius:5,color:'#ffffff',weight:2,fillColor:color,fillOpacity:1,renderer:markerRenderer});
    else estimatedMarker.setLatLng(point).setStyle({fillColor:color});
    estimatedMarker.bindTooltip('CALC · row '+row+' · model 95% radius '+radius.toFixed(1)+' m · status '+status);
    if(!estimatedUncertainty) estimatedUncertainty=L.circle(point,{radius:radius,color:color,weight:1,fillOpacity:0.05,renderer:markerRenderer,interactive:false});
    else estimatedUncertainty.setLatLng(point).setRadius(radius).setStyle({color:color});
    setTrackMode(trackMode);
};
function drawVisibleRange() {
    rangeMarks.clearLayers();
    if(!visibleRange) return;
    for(const [edge, color] of [['start','#8fdf75'],['end','#ff7897']]) {
        const mark=visibleRange[edge];
        if(!mark) continue;
        const p=map.latLngToLayerPoint(mark.point);
        // Layer points are rounded to integer pixels. Project without rounding so
        // nearby GPS fixes still provide a direction when the map is zoomed out.
        const center=map.project(mark.point), before=map.project(mark.before), after=map.project(mark.after);
        let direction=after.subtract(before);
        if(direction.x===0 && direction.y===0) direction=after.subtract(center);
        if(direction.x===0 && direction.y===0) direction=center.subtract(before);
        const norm=Math.hypot(direction.x,direction.y);
        const hasDirection=Number.isFinite(norm) && norm>0;
        const offset=hasDirection?L.point(-direction.y/norm*14,direction.x/norm*14):L.point(0,14);
        const label='Visible '+edge+' · nearest recorded GPS · '+mark.time+' · row '+mark.row+
            (hasDirection?'':' · direction unavailable (stationary/isolated fix)');
        L.circleMarker(mark.point,{radius:3,color,fillColor:color,fillOpacity:1,weight:2,renderer:rangeRenderer})
            .bindTooltip(label).addTo(rangeMarks);
        const ends=[map.layerPointToLatLng(p.subtract(offset)),map.layerPointToLatLng(p.add(offset))];
        L.polyline(ends,{color,weight:3,renderer:rangeRenderer}).bindTooltip(label).addTo(rangeMarks);
    }
}
window.setVisibleRange = function(bounds) { visibleRange=bounds; drawVisibleRange(); };
map.on('zoomend moveend',drawVisibleRange);
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
