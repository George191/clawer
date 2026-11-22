import type { ReactNode } from 'react';
import { Button, NativeSelect } from '@/components/ui';

export function ExplorerResultsFooter({ offset, rowCount, pageSize, loading, locked = false, canPageFurther, onPageSize, onPrevious, onNext, notices, children }: {
  offset: number; rowCount: number; pageSize: number; loading: boolean; locked?: boolean; canPageFurther: boolean;
  onPageSize: (size: number) => void; onPrevious: () => void; onNext: () => void; notices?: ReactNode; children?: ReactNode;
}) {
  return <footer className="flow-preview-pager">
    {notices && <div className="flow-preview-pager-notices" role="status">{notices}</div>}
    <nav aria-label="Preview pagination"><span className="flow-preview-range" aria-live="polite">{loading ? 'Loading preview…' : rowCount
      ? `Rows ${(offset + 1).toLocaleString('en-US')}–${(offset + rowCount).toLocaleString('en-US')}` : 'No rows on this page'}</span>
      <div className="flow-preview-page-controls"><label>Rows per page <NativeSelect aria-label="Rows per page" value={pageSize} disabled={loading || locked} onChange={event => { if (!loading && !locked) onPageSize(Number(event.target.value)); }}>
        {[10, 20, 50].map(count => <option key={count} value={count}>{count}</option>)}
      </NativeSelect></label><Button size="small" disabled={loading || locked || !offset} onClick={onPrevious}>Previous</Button>
        <Button size="small" disabled={loading || locked || !canPageFurther} onClick={onNext}>Next</Button></div>
      {children && <div className="flow-preview-page-actions">{children}</div>}
    </nav>
  </footer>;
}
