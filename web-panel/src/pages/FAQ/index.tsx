import React, { useState } from 'react';
import { Card, Collapse, Input, Space, Typography } from '@/components/ui';
import { ApiOutlined, BookOutlined, QuestionCircleOutlined, SearchOutlined, SafetyCertificateOutlined } from '@/components/ui/icons';
import './style.css';

const groups = [
  {
    title: 'Getting started', icon: <BookOutlined />, items: [
      ['How do I create my first collection task?', 'Open Data Collect from the sidebar, choose a source template, then follow the guided steps to configure the entry URL and fields.'],
      ['Which data sources are supported?', 'The platform supports web pages, APIs and structured feeds. Source-specific options are shown when you create a task.'],
      ['Where can I see a task run?', 'Use Automation → Runs for execution history, logs and the latest task status.'],
    ],
  },
  {
    title: 'Account & security', icon: <SafetyCertificateOutlined />, items: [
      ['How do I update my account details?', 'Open Account settings from your avatar menu to update your profile, password and notification preferences.'],
      ['How is access managed?', 'Organization administrators manage users, roles, teams and permissions from the Access area.'],
      ['What happens when a task fails?', 'The run is retained with its logs and failure reason. Fix the configuration and retry from the run details.'],
    ],
  },
  {
    title: 'Data & automation', icon: <ApiOutlined />, items: [
      ['How do I monitor collection health?', 'Open Real-time monitoring to review throughput, failures and current worker activity.'],
      ['Can I retry a failed run?', 'Open the run details in Automation and choose Retry after correcting the source or task configuration.'],
      ['Where are exported files stored?', 'Exports are published to the configured object storage location and linked from the task run.'],
    ],
  },
  {
    title: 'Billing & plans', icon: <BookOutlined />, items: [
      ['Where can I review my plan?', 'Open the billing area from the account menu to review plan and usage information.'],
      ['Who can change workspace settings?', 'Workspace administrators and owners can update plan and organization settings.'],
      ['How do I contact support?', 'Contact your workspace administrator, who can route requests to the appropriate support team.'],
    ],
  },
];

const FAQ: React.FC = () => {
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const filtered = groups.map(group => ({ ...group, items: group.items.filter(([question, answer]) => `${group.title} ${question} ${answer}`.toLowerCase().includes(query)) })).filter(group => group.items.length);
  const count = filtered.reduce((total, group) => total + group.items.length, 0);
  return <main className="faq-page">
    <header className="management-page-header"><span className="management-eyebrow">DOCUMENTATION & SUPPORT</span><Typography.Title level={2}>HELP CENTER</Typography.Title><Typography.Paragraph type="secondary">Find answers and guidance for your data workflows.</Typography.Paragraph></header>
    <section className="faq-search"><SearchOutlined /><Input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search questions, topics and answers…" aria-label="Search frequently asked questions" allowClear /><span>{groups.reduce((total, group) => total + group.items.length, 0)} ARTICLES</span></section>
    <div className="faq-heading"><Typography.Title level={3}>{query ? 'SEARCH RESULTS' : 'KNOWLEDGE BASE'}</Typography.Title><Typography.Text type="secondary" role="status">{query ? `${count} matching ${count === 1 ? 'article' : 'articles'}` : 'Browse by topic'}</Typography.Text></div>
    <div className="faq-grid">{filtered.map(group => <Card key={group.title} className="faq-card" title={<Space><span className="faq-icon">{group.icon}</span><span>{group.title.toUpperCase()}</span><span className="faq-count">{group.items.length}</span></Space>}><Collapse ghost key={query} defaultActiveKey={query ? group.items.map(([question]) => question) : []} items={group.items.map(([question, answer]) => ({ key: question, label: question, children: <Typography.Paragraph>{answer}</Typography.Paragraph> }))} /></Card>)}</div>
    {!filtered.length && <div className="faq-empty"><QuestionCircleOutlined /><Typography.Title level={4}>NO MATCHING ARTICLES</Typography.Title><Typography.Paragraph type="secondary">Try another keyword or browse all topics.</Typography.Paragraph><button type="button" onClick={() => setSearch('')}>CLEAR SEARCH</button></div>}
    <footer className="faq-contact"><QuestionCircleOutlined /><div><strong>NEED ASSISTANCE?</strong><p>Contact your workspace administrator for account, access or platform support.</p></div></footer>
  </main>;
};

export default FAQ;
