import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, Dropdown, Modal, NativeSelect, Select, theme } from '../../src/components/ui';
import '../../src/styles/global.css';
import '../../src/pages/ProductWorkspace/workspace.css';
import '../../src/pages/Pipeline/ontology-frame.css';
import '../../src/pages/Pipeline/semantic-object-wizard.css';
import '../../src/pages/Pipeline/data-connections.css';

function Fixture() {
  const light = new URLSearchParams(location.search).get('theme') === 'light';
  const [value, setValue] = useState(''), [changes, setChanges] = useState(0), [submitted, setSubmitted] = useState(''), [dialog, setDialog] = useState(false);
  const options = [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }, { value: 'c', label: 'Disabled', disabled: true }];
  return <ConfigProvider theme={{ algorithm: light ? theme.defaultAlgorithm : theme.darkAlgorithm }}>
    <main style={{ padding: 24, color: 'var(--ui-text)', background: 'var(--ui-bg)', minHeight: '100vh', boxSizing: 'border-box', fontFamily: 'Arial' }}>
      <form aria-label="Selection form" onSubmit={event => { event.preventDefault(); setSubmitted(JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))); }} style={{ display: 'grid', gap: 16, maxWidth: 380 }}>
        <label>Connection<NativeSelect id="connection" name="connection" required value={value} onChange={event => { setValue(event.target.value); setChanges(count => count + 1); }} style={{ width: '100%' }}>
          <option value="">Select a connection</option><optgroup label="Available"><option value="a">Alpha</option><option value="b">Beta</option><option value="c" disabled>Disabled</option></optgroup>
        </NativeSelect></label>
        <label>Default value<NativeSelect name="default" defaultValue="b" style={{ width: '100%' }}><option value="a">Alpha</option><option value="b">Beta</option></NativeSelect></label>
        <NativeSelect aria-label="Numeric value" name="numeric" value={10} onChange={() => {}}><option value={10}>10 / page</option><option value={20}>20 / page</option></NativeSelect>
        <NativeSelect aria-label="Disabled control" disabled value="a"><option value="a">Alpha</option></NativeSelect>
        <fieldset disabled style={{ border: 0, margin: 0, padding: 0 }}><NativeSelect aria-label="Disabled fieldset control" name="locked" value="a"><option value="a">Alpha</option></NativeSelect></fieldset>
        <Select aria-label="Searchable connection" showSearch allowClear options={options} placeholder="Search connections" style={{ width: '100%' }} />
        <Select aria-label="Multiple connections" mode="multiple" options={options} placeholder="Choose connections" style={{ width: '100%' }} />
        <button type="submit">Submit</button><button type="reset">Reset defaults</button>
      </form>
      <output aria-label="Change count">{changes}</output><output aria-label="Submitted values">{submitted}</output>
      <section className="ontology-connectors ontology-connections-workspace" style={{ maxWidth: 380, marginTop: 16 }}><div className="ontology-connectors__picker-tools"><label>Category <NativeSelect aria-label="Filter connector category" value="all"><option value="all">All connectors</option><option value="relational">Relational databases</option><option value="non_relational">Non-relational databases</option><option value="vector">Vector databases</option></NativeSelect></label></div></section>
      <div style={{ marginTop: 24 }}><Dropdown trigger={['click']} overlayClassName="flow-menu-popup" menu={{ items: [{ key: 'open', label: 'Open dialog' }, { key: 'disabled', label: 'Disabled action', disabled: true }], onClick: () => setDialog(true) }}><button type="button" style={{ width: 220 }}>Actions</button></Dropdown></div>
      <Modal open={dialog} title="Selection dialog" onCancel={() => setDialog(false)} footer={null} className="flow-surface-modal flow-semantic-wizard"><Select popupClassName="flow-select-popup" aria-label="Dialog connection" options={options} style={{ width: '100%' }} /></Modal>
    </main>
  </ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
