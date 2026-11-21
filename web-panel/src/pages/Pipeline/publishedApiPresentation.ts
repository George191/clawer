import type { EtlPublishedApi } from '../../services/api';
import { explorerFilterTerms } from './explorerFilters';

export function publicationState(api: EtlPublishedApi, now = Date.now()) {
  if (api.keyStatus === 'revoked') return { label: 'Key revoked', tone: 'muted' };
  if (api.keyStatus === 'unissued') return { label: 'Key not issued', tone: 'warning' };
  const start = Date.parse(api.validFrom), end = Date.parse(api.validUntil);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return { label: 'Invalid validity', tone: 'warning' };
  if (now < start) return { label: 'Scheduled', tone: 'muted' };
  if (now >= end) return { label: 'Expired', tone: 'muted' };
  return { label: 'Active', tone: 'active' };
}

export function publicationPredicate(api: EtlPublishedApi): string {
  const render = (terms: ReturnType<typeof explorerFilterTerms>): string => terms.map((term, index) => {
    const rule = term.rule;
    const condition = term.children ? '(' + render(term.children) + ')' :
      rule ? rule.field + ' ' + rule.operator.replace(/\s+/g, ' ').trim() +
        (/^(是\s+null|不是\s+null|是空的|不是空的)$/.test(rule.operator) ? '' : ' ' + JSON.stringify(rule.value)) : '';
    return condition + (index < terms.length - 1 ? ' ' + term.join.toUpperCase() + ' ' : '');
  }).join('');
  return render(explorerFilterTerms(api.filters || []));
}

export function publicationDateInput(value: string): string {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
}

export function publicationDateValue(value: string): string {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? date.toISOString() : '';
}

export function publicationRequest(endpoint: string, language: 'curl' | 'http'): string {
  if (language === 'http') {
    const url = new URL(endpoint);
    url.searchParams.set('limit', '50'); url.searchParams.set('offset', '0');
    return `GET ${url.pathname}${url.search} HTTP/1.1\nHost: ${url.host}\nX-API-Key: <PUBLICATION_KEY>\nAccept: application/json`;
  }
  const quoted = "'" + endpoint.replace(/'/g, "'\\''") + "'";
  return `curl --get ${quoted} \\\n  --header "X-API-Key: $API_KEY" \\\n  --header 'Accept: application/json' \\\n  --data-urlencode 'limit=50' \\\n  --data-urlencode 'offset=0'`;
}
