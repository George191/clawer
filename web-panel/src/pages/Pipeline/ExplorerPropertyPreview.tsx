import { useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { landPolygons } from '../Login/earthLand';
import { readSeries } from './ExplorerSeriesCell';
import './explorer-property-preview.css';

type Position = [number, number];
type Shape = { lines: Position[][]; kind: 'points'|'line'|'polygon' };
const coastline = landPolygons.map(ring => ring.map(([longitude, latitude], index) =>
    (index ? 'L' : 'M') + longitude + ' ' + -latitude).join(' ') + 'Z').join(' ');

function shapesForGeoJSON(input: unknown): Shape[] | null {
    const shapes: Shape[]=[];
    let vertices=0, objects=0;
    function point(value: unknown): Position {
        if (!Array.isArray(value) || value.length < 2 || ++vertices>4096) throw new Error();
        const [lon,lat]=value;
        if (typeof lon!=='number' || typeof lat!=='number' || !Number.isFinite(lon) || !Number.isFinite(lat) ||
            Math.abs(lon)>180 || Math.abs(lat)>90) throw new Error();
        return [lon,lat];
    }
    function line(value: unknown): Position[] {
        if (!Array.isArray(value) || value.length<2 || value.length>4096) throw new Error();
        return value.map(point);
    }
    function polygon(value: unknown) {
        if (!Array.isArray(value) || !value.length || value.length>1000) throw new Error();
        const rings=value.map(line);
        if (rings.some(r=>r.length<4 || r[0][0]!==r[r.length-1][0] || r[0][1]!==r[r.length-1][1])) throw new Error();
        shapes.push({kind:'polygon',lines:rings});
    }
    function visit(value: unknown,depth: number) {
        if (++objects>2048 || depth>12 || !value || typeof value!=='object') throw new Error();
        const g=value as Record<string,unknown>, c=g.coordinates;
        switch (g.type) {
            case 'Feature': if (g.geometry) visit(g.geometry,depth+1); break;
            case 'FeatureCollection': case 'GeometryCollection': {
                const children=g.type==='FeatureCollection'?g.features:g.geometries;
                if (!Array.isArray(children)) throw new Error();
                for (const child of children) visit(child,depth+1);
                break;
            }
            case 'Point': shapes.push({kind:'points',lines:[[point(c)]]}); break;
            case 'MultiPoint':
                if (!Array.isArray(c)) throw new Error();
                shapes.push({kind:'points',lines:[c.map(point)]}); break;
            case 'LineString': shapes.push({kind:'line',lines:[line(c)]}); break;
            case 'MultiLineString':
                if (!Array.isArray(c)) throw new Error();
                shapes.push({kind:'line',lines:c.map(line)}); break;
            case 'Polygon': polygon(c);break;
            case 'MultiPolygon':
                if (!Array.isArray(c)) throw new Error();
                c.forEach(polygon); break;
            default: throw new Error();
        }
    }
    try { visit(input,0);return vertices ? shapes : null; } catch {return null;}
}

function Geometry({ value, label }: {value: unknown; label: string}) {
    const [world,setWorld]=useState(false);
    const shapes=shapesForGeoJSON(value);
    if (!shapes) return <p role="status">Expected WGS84 GeoJSON with up to 4,096 coordinates.</p>;
    const points=shapes.flatMap(s=>s.lines.flat());
    const east=points.map(p=>p[0]), north=points.map(p=>p[1]);
    const minX=world?-180:Math.min(...east), maxX=world?180:Math.max(...east);
    const minY=world?-90:Math.min(...north), maxY=world?90:Math.max(...north);
    const scale=Math.min(340/Math.max(maxX-minX,1),145/Math.max(maxY-minY,1));
    const xy=([lon,lat]:Position)=>[180+(lon-(minX+maxX)/2)*scale,85-(lat-(minY+maxY)/2)*scale];
    return <figure className="explorer-geometry"><svg role="img" aria-label={label+' geometry'} viewBox="0 0 360 170">
        <path className="geo-land" d={coastline} transform={`translate(180 85) scale(${scale}) translate(${-(minX+maxX)/2} ${(minY+maxY)/2})`}/>
        {[60,120,180,240,300].map(x=><path key={x} className="geo-grid" d={`M${x} 0v170`}/>)}
        {[42,85,128].map(y=><path key={y} className="geo-grid" d={`M0 ${y}h360`}/>)}
        {shapes.map((shape,i)=>shape.kind==='points'?shape.lines.flat().map((p,j)=>{
            const [x,y]=xy(p);return <circle key={i+'-'+j} cx={x} cy={y} r="3"><title>{p.join(', ')}</title></circle>;
        }):<path key={i} fillRule="evenodd" className={shape.kind} d={shape.lines.map(ring=>
            ring.map((p,j)=>(j?'L':'M')+xy(p).map(n=>n.toFixed(2)).join(' ')).join(' ')+(shape.kind==='polygon'?'Z':'')).join(' ')}/>)}
        </svg><figcaption><span>Longitude {minX.toFixed(3)}° to {maxX.toFixed(3)}° · Latitude {minY.toFixed(3)}° to {maxY.toFixed(3)}°</span>
        <button type="button" onClick={()=>setWorld(v=>!v)}>{world?'Fit geometry':'World extent'}</button></figcaption></figure>;
}

function TimeSeries({value,label}:{value:unknown;label:string}) {
    const [expanded,setExpanded]=useState(false);
    const [page,setPage]=useState(0);
    const samples=readSeries(value);
    if(!samples) return <p role="status">Expected 1–4,096 timestamp/value pairs. Use ISO dates or Unix milliseconds and numeric or null values.</p>;
    const pages=Math.ceil(samples.length/100), current=Math.min(page,pages-1);
    return <div className="explorer-property-chart" role="group" aria-label={label+' time series'}>
        <ReactECharts style={{height:185,width:'100%'}} notMerge opts={{renderer:'svg'}} option={{
            animation:false,grid:{left:45,right:14,top:15,bottom:52},
            tooltip:{trigger:'axis',renderMode:'richText'},xAxis:{type:'time'},yAxis:{type:'value',scale:true},
            dataZoom:[{type:'inside',filterMode:'none'},{type:'slider',height:14,bottom:7,filterMode:'none'}],
            series:[{type:'line',data:samples,showSymbol:samples.length<8,connectNulls:false,itemStyle:{color:'#398fea'}}]
        }}/><details onToggle={event=>setExpanded(event.currentTarget.open)}><summary>View {samples.length} observations</summary>
            {expanded && <><table><thead><tr><th>Timestamp (UTC)</th><th>Value</th></tr></thead><tbody>
                {samples.slice(current*100,(current+1)*100).map(([time,v],i)=><tr key={i}><td>{new Date(time).toISOString()}</td><td>{v??'Null'}</td></tr>)}
            </tbody></table>{pages>1 && <div aria-label="Observation pages">
                <button disabled={!current} onClick={()=>setPage(current-1)}>Previous</button>
                <span>{current+1} / {pages}</span><button disabled={current+1===pages} onClick={()=>setPage(current+1)}>Next</button>
            </div>}</>}</details></div>;
}

export default function ExplorerPropertyPreview({kind,value,label}:{kind:'timeSeries'|'geoJson';value:unknown;label:string}) {
    if(value==null) return <span>Null</span>;
    if(kind==='geoJson') return <Geometry value={value} label={label}/>;
    return <TimeSeries value={value} label={label}/>;
}
