/* Exercise real Leaflet geometry, including GPS separations below one display pixel. */
JSON.stringify((()=>{
    const center=map.getCenter(), zoom=map.getZoom(), results=[];
    const cases=[
        {name:'subpixel',point:[0,0],before:[0,0],after:[0.00000001,0.00000002],direction:'chord'},
        {name:'reversal',point:[0,0],before:[0.0001,-0.0001],after:[0.0001,-0.0001],direction:'outgoing'},
        {name:'endpoint',point:[0,0],before:[-0.0001,0.0001],after:[0,0],direction:'chord'},
        {name:'stationary',point:[0,0],before:[0,0],after:[0,0],direction:null}
    ];
    try {
        for(const scale of [0,2,8,14,19]) {
            map.setView([0,0],scale,{animate:false});
            for(const scenario of cases) {
                const mark={...scenario,row:0,time:'00:00:00'};
                setVisibleRange({start:mark,end:mark});
                const bars=rangeMarks.getLayers().filter(p=>p.getLatLngs);
                const heading=map.project(mark.after).subtract(map.project(
                    scenario.direction==='outgoing'?mark.point:mark.before));
                const geometry=bars.map(bar=>{
                    const ends=bar.getLatLngs().map(p=>map.project(p));
                    const vector=ends[1].subtract(ends[0]), length=Math.hypot(vector.x,vector.y);
                    const norm=Math.hypot(heading.x,heading.y);
                    return {
                        length,
                        perpendicular:norm?Math.abs(vector.x*heading.x+vector.y*heading.y)/(norm*length):null,
                        unknown:bar.getTooltip().getContent().includes('direction unavailable')
                    };
                });
                results.push({
                    name:scenario.name,zoom:scale,count:bars.length,geometry,
                    roundedSamePixel:map.latLngToLayerPoint(mark.before).equals(map.latLngToLayerPoint(mark.after)),
                    separate:bars.every(bar=>bar._renderer!==routeRenderer)
                });
            }
        }
        return {results};
    } catch(error) {
        return {error:String(error),results};
    } finally {
        setVisibleRange(null);
        map.setView(center,zoom,{animate:false});
    }
})())
