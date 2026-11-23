import type { DataSourceProvider } from '../../services/api';
import marks from './hosting-provider-marks.svg';

export default function HostingProviderIcon({ provider }: { provider: DataSourceProvider }) {
  return <svg className="hosting-provider-icon" data-provider={provider} width="18" height="18"
    viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <use href={`${marks}#${provider}`} />
  </svg>;
}
