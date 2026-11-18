import type { EtlStreamState } from '../../services/types';

export function topicRate(previous: EtlStreamState | undefined, next: EtlStreamState) {
    if (!previous?.checkedAt || !next.checkedAt || previous.topic !== next.topic ||
        !previous.offsets?.length || previous.offsets.length !== next.offsets?.length) return null;
    const seconds = (Date.parse(next.checkedAt) - Date.parse(previous.checkedAt)) / 1000;
    if (!(seconds > 0)) return null;
    let delta = 0;
    for (let i = 0; i < next.offsets.length; i++) {
        const part = next.offsets[i], before = previous.offsets[i];
        if (before.partition !== part.partition || part.endOffset < before.endOffset) return null;
        delta += part.endOffset - before.endOffset;
    }
    return delta / seconds;
}

export default function ExplorerStreamPositions({ stream }: { stream: EtlStreamState }) {
    const format = (value: number | null) => value == null ? 'No commit' : value.toLocaleString();
    return <>
        <dl className="flow-explorer-stream-meta">
            <div><dt>Consumer group</dt><dd>{stream.consumerGroup}</dd></div>
            <div><dt>Topic growth</dt><dd>{stream.throughput == null ? 'Sampling…' : `${stream.throughput.toLocaleString(undefined, { maximumFractionDigits: 1 })} offsets/s`}</dd></div>
            {stream.checkedAt && <div><dt>Checked at</dt><dd>{new Date(stream.checkedAt).toLocaleTimeString()}</dd></div>}
        </dl>
        {stream.reason && <p role="status">{stream.reason}</p>}
        {!!stream.offsets?.length && <table><thead><tr>{['Partition', 'First retained', 'Log end', 'Group commit', 'Group lag', 'Resume position'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
            <tbody>{stream.offsets.map(item => <tr key={item.partition}>
                <td>{item.partition}</td><td>{format(item.beginningOffset)}</td><td>{format(item.endOffset)}</td>
                <td>{format(item.committedOffset)}</td><td>{format(item.lag)}</td>
                <td>{stream.resumeAvailable ? `${format(item.offset)} (${item.source.replace('_', ' ')})` : 'Unchecked'}</td>
            </tr>)}</tbody></table>}
    </>;
}
