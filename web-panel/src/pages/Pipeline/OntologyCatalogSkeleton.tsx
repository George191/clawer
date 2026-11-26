import { ReloadOutlined } from '@/components/ui/icons';

export default function OntologyCatalogSkeleton({ view, label }: { view: 'grid' | 'list'; label: string }) {
  return <div className="flow-catalog-skeleton" role="status" aria-label={label}>
    <div className="flow-catalog-skeleton__status"><ReloadOutlined spin aria-hidden="true" /><div><strong>{label}</strong><p>Fetching table names and details…</p></div></div>
    <div className={`flow-ontology__assets is-${view}`} aria-hidden="true">
      {Array.from({ length: 12 }, (_, index) => <div className="flow-ontology__asset" key={index}><i /><div className="flow-catalog-skeleton__lines"><span /><span /></div></div>)}
    </div>
  </div>;
}
