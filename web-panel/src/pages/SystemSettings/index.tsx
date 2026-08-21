import React, { useState } from 'react';
import { Button, Card, Form, Radio, Select, Space, Typography, message } from 'antd';
import { BgColorsOutlined, GlobalOutlined } from '@ant-design/icons';
import { useThemeStore, type SystemLanguage, type ThemeMode } from '@/stores/settings';
import './style.css';

type SettingsSection = 'appearance' | 'language';

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
    <header className="system-settings-header"><Typography.Title level={2}>System Settings</Typography.Title><Typography.Text type="secondary">Configure the appearance and language of this system.</Typography.Text></header>
    <div className="system-settings-layout">
      <aside className="system-settings-nav" aria-label="System settings sections">
        <Typography.Title level={5}>Getting Started</Typography.Title>
        <button type="button" className={section === 'appearance' ? 'is-active' : ''} onClick={() => setSection('appearance')}><BgColorsOutlined /><span>Appearance</span></button>
        <button type="button" className={section === 'language' ? 'is-active' : ''} onClick={() => setSection('language')}><GlobalOutlined /><span>Language</span></button>
      </aside>
      <Card className="system-settings-card" styles={{ body: { padding: 0 } }}>
        {section === 'appearance' ? <Form layout="vertical" className="system-settings-form">
          <section className="system-settings-section"><Typography.Title level={5}>Appearance</Typography.Title><Typography.Paragraph type="secondary">Choose how Asiral Helio looks across the current browser.</Typography.Paragraph><Form.Item label="Theme"><Radio.Group value={mode} onChange={(event) => setMode(event.target.value as ThemeMode)} className="theme-choice-grid"><Radio.Button value="dark"><span className="theme-preview is-dark"><i /><i /><i /></span><strong>Dark</strong></Radio.Button><Radio.Button value="light"><span className="theme-preview is-light"><i /><i /><i /></span><strong>Light</strong></Radio.Button></Radio.Group></Form.Item></section>
          <section className="system-settings-section"><Typography.Title level={5}>Display behavior</Typography.Title><Typography.Paragraph type="secondary">Theme changes are previewed immediately and remain local to this system.</Typography.Paragraph></section>
        </Form> : <Form layout="vertical" className="system-settings-form">
          <section className="system-settings-section"><Typography.Title level={5}>Language</Typography.Title><Typography.Paragraph type="secondary">Select the language used by system components and built-in controls.</Typography.Paragraph><Form.Item label="System language"><Select aria-label="System language" value={language} onChange={(value) => setLanguage(value as SystemLanguage)} options={[{ value: 'zh-CN', label: '简体中文' }, { value: 'en-US', label: 'English' }]} /></Form.Item></section>
          <section className="system-settings-section"><Typography.Title level={5}>Regional preference</Typography.Title><Typography.Paragraph type="secondary">The selected language controls dates, pagination and standard interface messages.</Typography.Paragraph></section>
        </Form>}
        <footer className="system-settings-actions"><Space><Button onClick={discard}>Discard</Button><Button type="primary" onClick={save}>Save Changes</Button></Space></footer>
      </Card>
    </div>
  </main>;
};
export default SystemSettings;
