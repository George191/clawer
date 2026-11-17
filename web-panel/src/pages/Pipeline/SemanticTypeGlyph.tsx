import { AppstoreOutlined, DatabaseOutlined, FileTextOutlined, GlobalOutlined, PushpinOutlined, TeamOutlined } from '@/components/ui/icons';
import type { SemanticDefinition } from '../../services/ontology';
import { inferSiteKind, TemplateGlyph } from '../AICollect/WorkspaceDock';

const configuredIcons = {
  box: AppstoreOutlined,
  database: DatabaseOutlined,
  globe: GlobalOutlined,
  'map-pin': PushpinOutlined,
  users: TeamOutlined,
  document: FileTextOutlined,
} as const;

type Props = {
  definition: Pick<SemanticDefinition, 'id' | 'name' | 'source' | 'icon' | 'color'>;
  size?: 'row' | 'preview';
};

function glyphForeground(color: string) {
  const channels = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255)
    .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
  return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2] > .179 ? '#000' : '#fff';
}

export default function SemanticTypeGlyph({ definition, size = 'row' }: Props) {
  const ExplicitIcon = Object.prototype.hasOwnProperty.call(configuredIcons, definition.icon)
    ? configuredIcons[definition.icon as keyof typeof configuredIcons]
    : undefined;
  const names = [definition.id, definition.source.table || '', definition.name];
  const kind = names.map(name => inferSiteKind(name)).find(item => item !== 'generic');
  const flight = !kind && names.some(name => /(?:^|[\s_-])(?:flights?|airports?|aviation)(?:$|[\s_-])/i.test(name));
  const color = /^#[\da-f]{6}$/i.test(definition.color) ? definition.color : undefined;

  return <span
    className={`flow-semantic-type-glyph flow-semantic-type-glyph--${size}`}
    aria-hidden="true"
    style={color ? { backgroundColor: color, color: glyphForeground(color) } : undefined}
  >
    {ExplicitIcon ? <ExplicitIcon /> : flight ? <svg viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor" focusable="false">
      <path d="M21 3.2c-.6-.6-1.7-.5-2.3.1l-4 4-9.1-2-2 2 7.1 4-3.6 3.6-3.1-.5-1.5 1.5 4.4 2.2 2.2 4.4 1.5-1.5-.5-3.1 3.6-3.6 4 7.1 2-2-2-9.1 4-4c.6-.6.7-1.7.1-2.3Z" />
    </svg> : kind ? <TemplateGlyph kind={kind} /> : <AppstoreOutlined />}
  </span>;
}
