import type { CdcStatus } from '../../services/types';

export function cdcNeedsAttention(status: CdcStatus): boolean {
  const required = [status.connect, status.mongo, status.kafka];
  if (status.outputMode === 'debezium') required.push(status.postgres);
  return required.some((probe) => probe.state !== 'available');
}

export function formatWalBytes(value: number | null): string {
  if (value === null) return '未知';
  if (value < 1024) return `${value} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length);
  return `${(value / 1024 ** exponent).toFixed(1)} ${units[exponent - 1]}`;
}
