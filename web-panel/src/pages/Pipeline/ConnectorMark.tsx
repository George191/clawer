import type { DataConnectorEngine } from '../../services/api';
import PostgresGlyph from './PostgresGlyph';

export default function ConnectorMark({ engine }: { engine: DataConnectorEngine }) {
  return <span className={`ontology-connectors__mark is-${engine}`} aria-hidden="true">{engine === 'postgresql' ? <PostgresGlyph /> : engine === 'oracle' ? <span className="ontology-connectors__oracle" /> : engine === 'elasticsearch' ? <svg viewBox="0 0 24 24"><path fill="#FEC514" d="M3 8a9 9 0 0 1 15-3L11 9Z" /><path fill="#00BFB3" d="m12 10 9-3a9 9 0 0 1-1 11l-8-4Z" /><path fill="#F04E98" d="m3 10 7 1v4l-5 4a9 9 0 0 1-2-9Z" /><path fill="#009EBB" d="m11 16 7 4a9 9 0 0 1-11 1Z" /></svg> : engine === 'milvus' ? <svg viewBox="0 0 24 24" fill="currentColor"><path d="m3 8 5-4v12l-5 4Zm7-3 5 4v11l-5-4Zm7 4 5-4v11l-5 4Z" /></svg> : <span className="ontology-connectors__wordmark">{engine === 'mysql' ? 'MySQL' : 'DM'}</span>}</span>;
}
