import type { TableExportWriteMode } from '../../services/api';

export const exportMode = (record?: { writeMode?: TableExportWriteMode } | null) => record?.writeMode ?? 'upsert';
export const exportModeLabel = (mode: TableExportWriteMode) => mode === 'append' ? 'Append only' : 'Upsert';
export const exportModeDescription = (mode: TableExportWriteMode) => mode === 'append'
  ? 'Insert new keys only. If any key already exists, the entire batch is rejected and existing rows are unchanged.'
  : 'Insert new keys and update matching keys. No target rows are deleted.';
