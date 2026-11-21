import { CloseOutlined } from '@/components/ui/icons';

export default function ExplorerSearchCondition({ search = '', removeSearch }: {
  search?: string; removeSearch: () => void;
}) {
  if (!search.trim()) return null;
  return <div className="flow-schema-applied flow-query-chips" aria-label="Object search">
    <span className="flow-query-chip"><span title={search}>Search: {search}</span><button type="button" aria-label="Clear object search" title="Clear search" onClick={removeSearch}><CloseOutlined /></button></span>
  </div>;
}
