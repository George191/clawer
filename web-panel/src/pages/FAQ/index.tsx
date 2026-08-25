import React from 'react';
import { Card, Collapse, Input, Space, Tag, Typography, theme } from 'antd';
import { ApiOutlined, BookOutlined, QuestionCircleOutlined, SearchOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
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
  const { token } = theme.useToken();
  return <main className="faq-page" style={{ '--faq-primary': token.colorPrimary } as React.CSSProperties}>
    <section className="faq-hero">
      <div className="faq-hero-copy"><Tag color="cyan">HELP CENTER</Tag><Typography.Title>Frequently Asked Questions</Typography.Title><Typography.Paragraph>Find quick answers about Asiral Helio and keep your data workflows moving.</Typography.Paragraph><Input size="large" prefix={<SearchOutlined />} placeholder="Search your question" aria-label="Search frequently asked questions" /></div>
      <div className="faq-hero-art"><QuestionCircleOutlined /><ApiOutlined /></div>
    </section>
    <div className="faq-heading"><Typography.Title level={3}>Knowledge base</Typography.Title><Typography.Text type="secondary">Browse common questions by topic</Typography.Text></div>
    <div className="faq-grid">{groups.map((group) => <Card key={group.title} className="faq-card" title={<Space><span className="faq-icon">{group.icon}</span><span>{group.title}</span></Space>}><Collapse ghost items={group.items.map(([question, answer]) => ({ key: question, label: question, children: <Typography.Paragraph>{answer}</Typography.Paragraph> }))} /></Card>)}</div>
    <div className="faq-contact"><Typography.Text strong>Still need help?</Typography.Text><Typography.Text type="secondary"> Contact your workspace administrator for assistance.</Typography.Text></div>
  </main>;
};

export default FAQ;
