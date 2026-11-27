import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { Tooltip } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';

type LinkedType = {
  id: string; name: string; tooltip: string; icon: ReactNode;
  style?: CSSProperties; iconStyle?: CSSProperties; onSelect: () => void;
};

export default function LinkedObjectTypes({ items, loading, pendingId, disabled, error, onRetry }: {
  items?: LinkedType[]; loading?: boolean; pendingId?: string; disabled?: boolean;
  error?: string; onRetry?: () => void;
}) {
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 1150px)').matches);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1150px)');
    const resize = () => setMobile(query.matches);
    query.addEventListener('change', resize);
    return () => query.removeEventListener('change', resize);
  }, []);
  return <section className="flow-ontology__preview-section">
    <header><h3>Linked object types {items && <small>({items.length.toLocaleString('en-US')})</small>}</h3></header>
    <div className="flow-ontology__linked-list">{items?.map(item => <Tooltip key={item.id} placement={mobile ? 'top' : 'left'}
      overlayClassName="flow-ontology__relation-tooltip" title={item.tooltip}>
      <button type="button" className="flow-ontology__linked-row" style={item.style} aria-busy={pendingId === item.id}
        disabled={disabled || pendingId === item.id} onClick={item.onSelect}>
        <span className="flow-ontology__linked-icon" style={item.iconStyle}>{pendingId === item.id ? <ReloadOutlined spin /> : item.icon}</span>
        <span>{item.name}</span>
      </button>
    </Tooltip>)}</div>
    {loading && <p role="status"><LoadingIndicator size={20}>Loading linked object types…</LoadingIndicator></p>}
    {!loading && !error && !items?.length && <p className="flow-ontology__muted">No relationships defined.</p>}
    {error && <p className="flow-ontology__visibility-feedback is-error" role="alert">{error}{onRetry && <> <button type="button" onClick={onRetry}>Retry linked types</button></>}</p>}
  </section>;
}
