import { useId, type ComponentProps, type CSSProperties, type ReactNode, type Ref } from 'react';
import { Drawer, Select } from '@/components/ui';
import { ArrowRightOutlined, CloseOutlined, EyeOutlined, ReloadOutlined, RightOutlined } from '@/components/ui/icons';
import type { EtlObjectVisibility } from '../../services/types';
import LinkedObjectTypes from './LinkedObjectTypes';
import PropertyTypeIcon from './PropertyTypeIcon';

export type ObjectTypePreviewProperty = {
  id: string; name: string; type: string; required: boolean;
  primaryKey: boolean; title: boolean; description?: string;
};

export type ObjectTypePreviewProps = {
  open: boolean; closing?: boolean; id: string; name: string; icon: ReactNode; style?: CSSProperties;
  loading: boolean; busy?: boolean; error?: string; onRetry?: () => void;
  errorAction?: { label: string; onClick: () => void };
  onClose: () => void; onExplore: () => void; exploreDisabled?: boolean;
  description?: string; summary?: { label: string; value: ReactNode }[];
  properties: ObjectTypePreviewProperty[]; allProperties: boolean; onPropertiesToggle: () => void; propertiesId?: string;
  visibility?: {
    value: EtlObjectVisibility; label: string; description: string; help: string;
    writable: boolean; draft: EtlObjectVisibility; options: { value: EtlObjectVisibility; label: string }[];
    disabled?: boolean; saving?: boolean; saveDisabled?: boolean; error?: string; feedback?: string;
    onChange: (value: EtlObjectVisibility) => void; onSave: () => void;
  };
  linkedTypes: ComponentProps<typeof LinkedObjectTypes>;
  asideRef?: Ref<HTMLElement>; testId?: string;
};

export default function ObjectTypePreview({
  open, closing, id, name, icon, style, loading, busy = loading, error, onRetry, errorAction,
  onClose, onExplore, exploreDisabled, description, summary, properties, allProperties, onPropertiesToggle,
  propertiesId, visibility, linkedTypes, asideRef, testId = 'ontology-inspector',
}: ObjectTypePreviewProps) {
  const generatedPropertiesId = useId();
  const propertyListId = propertiesId || generatedPropertiesId;
  const visibleProperties = allProperties ? properties : properties.slice(0, 6);
  return <Drawer open={open} onClose={closing ? undefined : onClose} width="100%" title={`${id} Object type preview`} closable={false} destroyOnHidden
    rootClassName={`flow-ontology flow-ontology__drawer${closing ? ' is-closing' : ''}`}
    styles={{ header: { display: 'none' }, body: { display: 'flex', padding: 0, overflow: 'hidden' } }}>
    <aside ref={asideRef} className="flow-ontology__inspector" data-testid={testId} data-object-id={id} style={style} {...(closing ? { inert: '' } : {})}>
      <header className="flow-ontology__inspector-toolbar">
        <span className="flow-ontology__asset-icon">{icon}</span>
        <div className="flow-ontology__preview-identity"><h2 title={id}>{name}</h2><span>Object Type</span></div>
        <button type="button" className="flow-ontology__icon-button" aria-label="Close asset preview" onClick={onClose}><CloseOutlined /></button>
      </header>
      <div className="flow-ontology__inspector-content" aria-busy={busy}>
        {loading ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>Loading object type</span></div>
          : error ? <div className="flow-ontology__notice" role="alert"><span>{error}</span>
            {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
            {errorAction && <button type="button" onClick={errorAction.onClick}>{errorAction.label}</button>}
          </div> : <>
            <section className="flow-ontology__preview-section"><h3>Description</h3><p className="flow-ontology__muted">{description || 'No description provided.'}</p></section>
            {summary && <section className="flow-ontology__preview-section" aria-label="Object type summary">
              {summary.map(item => <div key={item.label}><h3>{item.label}</h3><p>{item.value}</p></div>)}
            </section>}
            {visibility && <section className="flow-ontology__preview-section">
              <div className="flow-ontology__visibility"><h3>Visibility</h3><span className={`flow-ontology__visibility-badge is-${visibility.value}`} title={visibility.description}><EyeOutlined />{visibility.label}</span></div>
              {visibility.writable && <div className="flow-ontology__visibility-editor">
                <Select className="flow-ontology__select" popupClassName="flow-ontology__select-popup" aria-label="Object type visibility" value={visibility.draft}
                  disabled={visibility.disabled || closing} onChange={visibility.onChange} options={visibility.options} />
                <button type="button" className="flow-ontology__button" aria-label="Save visibility" disabled={visibility.disabled || visibility.saveDisabled || closing}
                  aria-busy={visibility.saving} onClick={visibility.onSave}>{visibility.saving ? <><ReloadOutlined spin />Saving…</> : 'Save'}</button>
              </div>}
              <p className="flow-ontology__visibility-help">{visibility.help}</p>
              {visibility.error && <p className="flow-ontology__visibility-feedback is-error" role="alert">{visibility.error}</p>}
              {visibility.feedback && <p className="flow-ontology__visibility-feedback" role="status">{visibility.feedback}</p>}
            </section>}
            <section className="flow-ontology__preview-section">
              <header><h3>Properties <small>({properties.length.toLocaleString('en-US')})</small></h3>
                {properties.length > 6 && <button type="button" className="flow-ontology__properties-toggle" aria-expanded={allProperties} aria-controls={propertyListId} onClick={onPropertiesToggle}>
                  {allProperties ? <><RightOutlined rotate={180} /><span>Back</span></> : <><span>View</span><RightOutlined /></>}</button>}
              </header>
              <div id={propertyListId} className="flow-ontology__property-list" role="region" aria-label="Properties" tabIndex={allProperties && properties.length > 6 ? 0 : undefined}>
                {visibleProperties.map(property => <div className="flow-ontology__property-row" key={property.id}
                  title={`${property.type}${property.required ? ' · Required' : ' · Nullable'}${property.description ? ` · ${property.description}` : ''}`}>
                  <PropertyTypeIcon type={property.type} /><span className="flow-ontology__property-name">{property.name}</span>
                  {property.title && <span className="flow-ontology__property-badge is-title">Title</span>}
                  {property.primaryKey && <span className="flow-ontology__property-badge is-primary">Primary key</span>}
                </div>)}
              </div>
              {!properties.length && <p className="flow-ontology__muted">No properties defined.</p>}
            </section>
            <LinkedObjectTypes {...linkedTypes} />
          </>}
      </div>
      <footer className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" disabled={exploreDisabled} onClick={onExplore}>Start exploring <ArrowRightOutlined /></button></footer>
    </aside>
  </Drawer>;
}
