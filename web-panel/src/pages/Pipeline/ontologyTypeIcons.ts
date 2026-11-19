import { uiIconLibrary } from '@/components/ui/icons';
import { ontologyIconColors } from './ontologyIconColors';

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

const accents = [
  ontologyIconColors.brightBlue, ontologyIconColors.green, ontologyIconColors.brightMagenta,
  ontologyIconColors.yellow, ontologyIconColors.cyan, ontologyIconColors.brightRed,
  ontologyIconColors.brightGreen, ontologyIconColors.brightCyan, ontologyIconColors.brightYellow,
];

// Derive from the stable identifier so adding or rearranging icons preserves existing colors.
function iconAccent(name: string) {
  let hash = 0;
  for (const char of name) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) >>> 0;
  return accents[hash % accents.length];
}

function luminance(color: string) {
  const channels = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255)
    .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
  return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
}

export function ontologyIconStyle(name: string, background: string) {
  const accent = iconAccent(name);
  if (!/^#[\da-f]{6}$/i.test(background)) return { backgroundColor: 'transparent', color: accent };
  const bg = luminance(background), fg = luminance(accent);
  const contrast = (Math.max(bg, fg) + .05) / (Math.min(bg, fg) + .05);
  const color = contrast >= 3 ? accent : (bg > .179 ? '#000' : '#fff');
  return { backgroundColor: background, color };
}
