import { CloseOutlined } from '@/components/ui/icons';

export default function ExplorerSearchCondition({ search = '', removeSearch }: {
  search?: string; removeSearch: () => void;
}) {
  if (!search.trim()) return null;
  return <span className="wb-applied-search" aria-label="Object search">
    <span title={search}>Search: {search}</span><button type="button" aria-label="Clear object search" title="Clear search" onClick={event => { event.preventDefault(); event.stopPropagation(); removeSearch(); }}><CloseOutlined /></button>
  </span>;
}
