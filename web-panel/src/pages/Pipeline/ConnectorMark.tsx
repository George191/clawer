import type { DataConnectorEngine } from '../../services/api';
import PostgresGlyph from './PostgresGlyph';
import amazonS3Mark from './amazon-s3.svg';

// Original Amazon S3 service icon from the AWS Architecture Icons 2026-07-31 package.
function StorageGlyph() {
  return <img src={amazonS3Mark} width="17" height="17" alt="" />;
}

function RedisGlyph() {
  return <svg viewBox="0 0 24 24" fill="none"><path d="M4 7C9 2 21 2 20 8c-.6 4-7 5-10 3m3-5L4 19m7-8c1 9 9 9 11 1" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function DamengGlyph() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 5h6a7 7 0 0 1 0 14H3Zm4 4h10v10M12 5h9v10" /></svg>;
}

function ClickHouseGlyph() {
  // Official logomark: https://clickhouse.com/static/brand-assets/clickhouse-logomark.zip
  return <svg viewBox="0 0 150 150"><path fill="currentColor" d="M27 25.7c0-.6.5-1.2 1.2-1.2H37c.6 0 1.2.5 1.2 1.2v97.7c0 .6-.5 1.2-1.2 1.2h-8.8c-.6 0-1.2-.5-1.2-1.2zm22.2 0c0-.6.5-1.2 1.2-1.2h8.8c.6 0 1.2.5 1.2 1.2v97.7c0 .6-.5 1.2-1.2 1.2h-8.8c-.6 0-1.2-.5-1.2-1.2zm22.2 0c0-.6.5-1.2 1.2-1.2h8.8c.6 0 1.2.5 1.2 1.2v97.7c0 .6-.5 1.2-1.2 1.2h-8.8c-.6 0-1.2-.5-1.2-1.2zm22.2 0c0-.6.5-1.2 1.2-1.2h8.8c.6 0 1.2.5 1.2 1.2v97.7c0 .6-.5 1.2-1.2 1.2h-8.8c-.6 0-1.2-.5-1.2-1.2zm22.3 38.9c0-.6.5-1.2 1.2-1.2h8.8c.6 0 1.2.5 1.2 1.2v19.9c0 .6-.5 1.2-1.2 1.2h-8.8c-.6 0-1.2-.5-1.2-1.2z" /></svg>;
}

function MySqlGlyph() {
  return <svg viewBox="0 0 24 24" fill="currentColor">
    <path fillRule="evenodd" d="M2.2 2.2c.5-.5 1.7-.4 2.9.4 3.6-.2 6.7 1.9 8.8 5.4 1.3 2.1 1.9 4.1 3.4 5.9 1.7.2 3.2 1 4 2.3-1.2-.2-2.2 0-2.9.4 1.5 1 2.6 2.6 3.2 4.8-4-1.7-7.8-1.7-11-4.9l-2.4-3c-.7.7-.8 1.7-.7 2.5-1.8-.8-2.6-3.7-2.2-5.7.2-1 .4-2.1-.1-3.6C4 5.8 3.5 4.5 2.5 3.7c-.6-.4-.6-1.1-.3-1.5Zm4.2 2.5a.7.7 0 1 0 0 1.4.7.7 0 0 0 0-1.4Z" />
  </svg>;
}

function MongoGlyph() {
  return <svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 1c1.7 3.3 5.6 6.2 5.6 11 0 3.5-1.9 6.2-4.7 8v2.5h-1.5V20c-3-1.7-5-4.3-5-8C6.4 7.2 10.3 4.3 12 1Z" />
    <path d="M12.1 9v12.4" stroke="#ba5001" strokeOpacity=".45" strokeWidth="1" />
  </svg>;
}

export default function ConnectorMark({ engine }: { engine: DataConnectorEngine }) {
  return <span className={`ontology-connectors__mark is-${engine}`} aria-hidden="true">{engine === 'postgresql' ? <PostgresGlyph /> : engine === 'mysql' ? <MySqlGlyph /> : engine === 'mongodb' ? <MongoGlyph /> : engine === 's3' ? <StorageGlyph /> : engine === 'redis' ? <RedisGlyph /> : engine === 'dameng' ? <DamengGlyph /> : engine === 'clickhouse' ? <ClickHouseGlyph /> : engine === 'oracle' ? <span className="ontology-connectors__oracle" /> : engine === 'elasticsearch' ? <svg viewBox="0 0 24 24"><path fill="#FEC514" d="M3 8a9 9 0 0 1 15-3L11 9Z" /><path fill="#00BFB3" d="m12 10 9-3a9 9 0 0 1-1 11l-8-4Z" /><path fill="#F04E98" d="m3 10 7 1v4l-5 4a9 9 0 0 1-2-9Z" /><path fill="#009EBB" d="m11 16 7 4a9 9 0 0 1-11 1Z" /></svg> : engine === 'milvus' ? <svg viewBox="0 0 24 24" fill="currentColor"><path d="m3 8 5-4v12l-5 4Zm7-3 5 4v11l-5-4Zm7 4 5-4v11l-5 4Z" /></svg> : null}</span>;
}
