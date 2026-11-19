import { AppstoreOutlined, PlaneFilled } from '@/components/ui/icons';
import { ontologyIconStyle, ontologyTypeIcon } from './ontologyTypeIcons';
import type { SemanticDefinition } from '../../services/ontology';
import { inferSiteKind, TemplateGlyph } from '../AICollect/WorkspaceDock';

type Props = {
  definition: Pick<SemanticDefinition, 'id' | 'name' | 'source' | 'icon' | 'color'>;
  size?: 'row' | 'preview';
  className?: string;
};

function automaticIcon(definition: Props['definition']) {
  const names = [definition.source.mode === 'existing' ? definition.source.table || '' : definition.id, definition.name];
  const kind = names.map(name => inferSiteKind(name)).find(item => item !== 'generic');
  if (kind) return kind;
  return names.some(name => /(?:^|[\s_-])(?:flights?|airports?|aviation)(?:$|[\s_-])/i.test(name)) ? 'plane' : 'box';
}

export default function SemanticTypeGlyph({ definition, size = 'row', className = '' }: Props) {
  const ExplicitIcon = ontologyTypeIcon(definition.icon);
  let glyph;
  let key = definition.icon;
  if (ExplicitIcon) {
    glyph = <ExplicitIcon />;
  } else {
    const inferred = automaticIcon(definition);
    key = inferred;
    if (inferred === 'plane') glyph = <PlaneFilled />;
    else if (inferred === 'box') glyph = <AppstoreOutlined />;
    else glyph = <TemplateGlyph kind={inferred} />;
  }
  return <span className={`flow-semantic-type-glyph flow-semantic-type-glyph--${size} ${className}`} aria-hidden="true"
    style={ontologyIconStyle(key, definition.color)}>
    {glyph}
  </span>;
}
