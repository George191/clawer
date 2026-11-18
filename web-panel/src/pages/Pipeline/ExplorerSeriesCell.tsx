export type Sample = [time: number, value: number | null];

export function readSeries(value: unknown): Sample[] | null {
    if (!Array.isArray(value) || !value.length || value.length > 4096) return null;
    const samples: Sample[] = [];
    for (const observation of value) {
        let stamp: unknown;
        let reading: unknown;
        if (Array.isArray(observation) && observation.length === 2) {
            [stamp, reading] = observation;
        } else if (observation && typeof observation === 'object') {
            ({ timestamp: stamp, value: reading } = observation);
        } else return null;
        const time = typeof stamp === 'number' ? stamp :
            typeof stamp === 'string' && /^\d{4}-\d\d-\d\d(?:T.+(?:Z|[+-]\d\d:\d\d))?$/.test(stamp) ? Date.parse(stamp) : NaN;
        if (!Number.isFinite(new Date(time).getTime()) ||
            !(reading === null || (typeof reading === 'number' && Number.isFinite(reading)))) return null;
        samples.push([time, reading]);
    }
    return samples.sort((a,b) => a[0]-b[0]);
}

export function ExplorerSeriesCell({ value }: { value: unknown }) {
    const samples=readSeries(value);
    if (!samples) return <span>Invalid time series</span>;
    const values=samples.flatMap(([,v])=>v===null?[]:[v]);
    const low=Math.min(...values), high=Math.max(...values);
    const start=samples[0][0], end=samples[samples.length-1][0];
    let move=true;
    const path=samples.map(([time,v])=>{
        if(v===null){move=true;return '';}
        const command=move?'M':'L';move=false;
        return command+(2+92*(time-start)/(end-start||1)).toFixed(1)+' '+(high===low?11:20-18*(v-low)/(high-low)).toFixed(1);
    }).join(' ');
    const latest=samples[samples.length-1][1];
    return <span style={{display:'inline-flex',alignItems:'center',gap:6}}><span>{latest??'Null'}</span>
        <svg aria-label="Time series trend" role="img" width="64" height="20" viewBox="0 0 96 22"><path d={path} fill="none" stroke="currentColor" strokeWidth="2"/></svg>
    </span>;
}
