import React from 'react';
import { Card, Divider, Typography } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';
import './style.css';

const sections = {
  privacy: { title: 'Privacy Policy', intro: 'How we collect, use and protect your information.', headings: ['Information we collect', 'How we use information', 'Data retention and security'] },
  terms: { title: 'Terms of Service', intro: 'The terms that govern your use of Asiral Helio.', headings: ['Using the service', 'Accounts and responsibilities', 'Service availability'] },
  cookies: { title: 'Cookie Policy', intro: 'How cookies and similar technologies support the platform.', headings: ['What cookies are', 'How we use cookies', 'Your choices'] },
  licenses: { title: 'Copyright & Licenses', intro: 'Copyright notices and third-party license information.', headings: ['Copyright', 'Open-source software', 'Third-party notices'] },
} as const;

const Legal: React.FC = () => {
  const { pathname } = useLocation(); const navigate = useNavigate();
  const key = pathname.split('/').pop() as keyof typeof sections; const page = sections[key] ?? sections.privacy;
  return <main className="legal-page"><div className="legal-breadcrumb"><button onClick={() => navigate('/faq')}>Help Center</button><span>/</span><span>{page.title}</span></div><header className="legal-header"><Typography.Title>{page.title}</Typography.Title><Typography.Paragraph>{page.intro}</Typography.Paragraph><Typography.Text type="secondary">Last updated August 24, 2026</Typography.Text></header><div className="legal-layout"><nav className="legal-nav" aria-label="Legal pages">{Object.entries(sections).map(([itemKey, item]) => <button key={itemKey} className={itemKey === key ? 'is-active' : ''} onClick={() => navigate(`/legal/${itemKey}`)}>{item.title}</button>)}</nav><Card className="legal-card"><Typography.Paragraph>Welcome to Asiral Helio. This document explains the policies and expectations that apply when you use our unified data intelligence platform.</Typography.Paragraph>{page.headings.map((heading) => <section key={heading}><Typography.Title level={4}>{heading}</Typography.Title><Typography.Paragraph>We provide clear, purpose-limited practices for this area. Information is handled with appropriate access controls, retained only as needed, and used to operate, secure and improve the service. Contact your workspace administrator if you have questions about this policy.</Typography.Paragraph></section>)}<Divider /><Typography.Paragraph type="secondary">Questions about this document can be directed to your workspace administrator.</Typography.Paragraph></Card></div></main>;
};
export default Legal;
