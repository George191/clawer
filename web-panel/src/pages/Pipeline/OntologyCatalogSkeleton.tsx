export default function OntologyCatalogSkeleton({ view, label }: { view: 'grid' | 'list'; label: string }) {
  return <div className={`flow-ontology__assets flow-catalog-skeleton is-${view}`} role="status" aria-label={label}>
    {Array.from({ length: 4 }, (_, index) => <div className="flow-ontology__asset" key={index} aria-hidden="true"><i /><span /></div>)}
  </div>;
}
