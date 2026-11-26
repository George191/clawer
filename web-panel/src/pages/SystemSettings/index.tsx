import React, { useState } from 'react';
import { Button, Card, Form, Radio, Select, Space, Typography, message } from '@/components/ui';
import { BgColorsOutlined, GlobalOutlined } from '@/components/ui/icons';
import { useThemeStore, type SystemLanguage, type ThemeMode } from '@/stores/settings';
import CollectionAgentSettings from './CollectionAgentSettings';
import './style.css';

type SettingsSection = 'appearance' | 'language' | 'collection';

const SystemSettings: React.FC = () => {
  const { mode, language, setMode, setLanguage } = useThemeStore();
  const [section, setSection] = useState<SettingsSection>('appearance');
  const [savedMode, setSavedMode] = useState(mode);
  const [savedLanguage, setSavedLanguage] = useState(language);

  const save = () => {
    setSavedMode(mode);
    setSavedLanguage(language);
    message.success('System settings saved');
  };
  const discard = () => {
    setMode(savedMode);
    setLanguage(savedLanguage);
    message.info('Unsaved changes discarded');
  };

  return <main className="system-settings-page">
    <header className="system-settings-header"><Typography.Title level={2}>SYSTEM SETTINGS</Typography.Title><Typography.Text type="secondary">Configure the appearance and language of this system.</Typography.Text></header>
    <div className="system-settings-layout">
      <aside className="system-settings-nav" aria-label="System settings sections">
        <Typography.Title level={5}>PREFERENCES</Typography.Title>
        <button type="button" aria-pressed={section === 'appearance'} className={section === 'appearance' ? 'is-active' : ''} onClick={() => setSection('appearance')}><BgColorsOutlined /><span>APPEARANCE</span></button>
        <button type="button" aria-pressed={section === 'language'} className={section === 'language' ? 'is-active' : ''} onClick={() => setSection('language')}><GlobalOutlined /><span>LANGUAGE</span></button>
        <button type="button" aria-pressed={section === 'collection'} className={section === 'collection' ? 'is-active' : ''} onClick={() => setSection('collection')}><GlobalOutlined /><span>COLLECTION AGENT</span></button>
      </aside>
      <Card className="system-settings-card" styles={{ body: { padding: 0 } }}>
        {section === 'collection' ? <CollectionAgentSettings /> : <>
        {section === 'appearance' ? <Form layout="vertical" className="system-settings-form">
          <section className="system-settings-section"><Typography.Title level={5}>APPEARANCE</Typography.Title><Typography.Paragraph type="secondary">Choose how Astral Helio looks across the current browser.</Typography.Paragraph><Form.Item label="Theme"><Radio.Group value={mode} onChange={(event) => setMode(event.target.value as ThemeMode)} className="theme-choice-grid"><Radio.Button value="dark"><span className="theme-preview is-dark"><i /><i /><i /></span><strong>DARK</strong></Radio.Button><Radio.Button value="light"><span className="theme-preview is-light"><i /><i /><i /></span><strong>LIGHT</strong></Radio.Button></Radio.Group></Form.Item></section>
          <section className="system-settings-section"><Typography.Title level={5}>DISPLAY BEHAVIOR</Typography.Title><Typography.Paragraph type="secondary">Theme changes are previewed immediately and remain local to this system.</Typography.Paragraph></section>
        </Form> : <Form layout="vertical" className="system-settings-form">
          <section className="system-settings-section"><Typography.Title level={5}>LANGUAGE</Typography.Title><Typography.Paragraph type="secondary">Select the language used by system components and built-in controls.</Typography.Paragraph><Form.Item label="System language"><Select aria-label="System language" value={language} onChange={(value) => setLanguage(value as SystemLanguage)} options={[{ value: 'zh-CN', label: '简体中文' }, { value: 'en-US', label: 'English' }]} /></Form.Item></section>
          <section className="system-settings-section"><Typography.Title level={5}>REGIONAL PREFERENCE</Typography.Title><Typography.Paragraph type="secondary">The selected language controls dates, pagination and standard interface messages.</Typography.Paragraph></section>
        </Form>}
        <footer className="system-settings-actions"><Space><Button onClick={discard}>Discard</Button><Button type="primary" onClick={save}>Save Changes</Button></Space></footer>
        </>}
      </Card>
    </div>
  </main>;
};
export default SystemSettings;
