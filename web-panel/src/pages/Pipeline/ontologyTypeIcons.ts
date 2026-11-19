import { uiIconLibrary } from '@/components/ui/icons';

// These are persisted ontology identifiers for glyphs whose library keys differ.
const ontologyNames: Partial<Record<keyof typeof uiIconLibrary, string>> = {
  appstore: 'box', global: 'globe', pin: 'map-pin', team: 'users', user: 'person',
  file: 'document', safety: 'security',
};
const typeIcons = new Map(Object.entries(uiIconLibrary).map(([key, icon]) =>
  [ontologyNames[key as keyof typeof uiIconLibrary] ?? key, icon] as const));

export const ontologyIconChoices = Array.from(typeIcons.keys(), value => ({
  value, label: value === 'api' ? 'API' : value[0].toUpperCase() + value.slice(1).replace(/-/g, ' '),
})).sort((a, b) => a.label.localeCompare(b.label, 'en'));

export function ontologyTypeIcon(name: string) {
  return typeIcons.get(name);
}
