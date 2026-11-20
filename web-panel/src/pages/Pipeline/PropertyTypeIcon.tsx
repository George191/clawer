import type { ReactNode } from 'react';
import { explorerPropertyType } from '../../services/explorerProperties';
import './property-type-icon.css';

function propertyGlyph(type: string) {
  switch (type) {
    case 'date':
    case 'datetime':
      return <><circle cx="8" cy="8" r="6" /><path d="M8 4v4l2.5 1.5" /></>;
    case 'integer':
    case 'number':
      return <text x="8" y="11" textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="9.5" fill="currentColor" stroke="none">{type === 'integer' ? '123' : '1.0'}</text>;
    case 'boolean':
      return <path d="m3 8 3.2 3.2L13 4.5" />;
    case 'json':
      return <path d="M6 2.5H5v3L3.5 8 5 10.5v3h1m4-11h1v3L12.5 8 11 10.5v3h-1" />;
    default:
      return <path fill="currentColor" stroke="none" d="M2 4h5v4c0 2.1-1.4 3.5-4 4l-.5-1.3C4 10.2 4.7 9.4 4.7 8H2V4Zm7 0h5v4c0 2.1-1.4 3.5-4 4l-.5-1.3c1.5-.5 2.2-1.3 2.2-2.7H9V4Z" />;
  }
}

export default function PropertyTypeIcon({ type }: { type: string }) {
  return <span className="flow-ontology__property-icon" title={type} aria-hidden="true">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" focusable="false">{propertyGlyph(['string', 'integer', 'number', 'boolean', 'date', 'datetime', 'json'].includes(type) ? type : explorerPropertyType(type))}</svg>
  </span>;
}

export function PropertyLabel({ type, children }: { type: string; children: ReactNode }) {
  return <span className="flow-property-label"><PropertyTypeIcon type={type} /><span>{children}</span></span>;
}
