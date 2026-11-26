import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ApartmentOutlined, AuditOutlined, BookOutlined, QuestionCircleOutlined, SafetyCertificateOutlined, SettingOutlined, TeamOutlined, UserOutlined } from '@/components/ui/icons';
import './style.css';

const sections = {
  access: { title: 'ACCESS', description: 'IDENTITY & GOVERNANCE', icon: <SafetyCertificateOutlined />, groups: [{ title: 'ADMINISTRATION', links: [
    { path: '/organization?view=users', label: 'USERS', icon: <UserOutlined /> },
    { path: '/organization?view=roles', label: 'ROLES', icon: <SafetyCertificateOutlined /> },
    { path: '/organization?view=permissions', label: 'PERMISSIONS', icon: <AuditOutlined /> },
    { path: '/organization?view=tenants', label: 'ORGANIZATIONS', icon: <ApartmentOutlined /> },
    { path: '/organization?view=teams', label: 'TEAMS', icon: <TeamOutlined /> },
  ] }] },
  settings: { title: 'SETTINGS', description: 'PLATFORM & PREFERENCES', icon: <SettingOutlined />, groups: [{ title: 'CONFIGURATION', links: [
    { path: '/settings', label: 'SYSTEM SETTINGS', icon: <SettingOutlined /> },
    { path: '/account/settings', label: 'ACCOUNT SETTINGS', icon: <UserOutlined /> },
  ] }] },
  help: { title: 'HELP', description: 'DOCUMENTATION & SUPPORT', icon: <QuestionCircleOutlined />, groups: [
    { title: 'RESOURCES', links: [{ path: '/faq', label: 'HELP CENTER', icon: <QuestionCircleOutlined /> }] },
    { title: 'LEGAL', links: [
      { path: '/legal/privacy', label: 'PRIVACY POLICY', icon: <SafetyCertificateOutlined /> },
      { path: '/legal/terms', label: 'TERMS OF SERVICE', icon: <BookOutlined /> },
      { path: '/legal/cookies', label: 'COOKIE POLICY', icon: <BookOutlined /> },
      { path: '/legal/licenses', label: 'COPYRIGHT & LICENSES', icon: <BookOutlined /> },
    ] },
  ] },
};

export function managementSection(path: string) {
  if (path === '/organization' || path.startsWith('/organization/')) return 'access';
  if (path === '/settings' || path === '/account/settings') return 'settings';
  if (path === '/faq' || path.startsWith('/legal/')) return 'help';
  return null;
}

const ManagementWorkspace: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { pathname, search } = useLocation();
  const section = managementSection(pathname);
  if (!section) return <>{children}</>;
  const config = sections[section];
  const requestedView = new URLSearchParams(search).get('view');
  const accessView = ['users', 'roles', 'permissions', 'tenants', 'teams'].includes(requestedView || '') ? requestedView : 'users';
  const activePath = section === 'access' ? `/organization?view=${pathname.startsWith('/organization/users/') ? 'users' : accessView}` : pathname;
  const activeLink = config.groups.flatMap(group => group.links).find(link => link.path === activePath);
  return <div className={`management-workspace management-${section}`}>
    <aside className="management-sidebar" aria-label={`${config.title} navigation`}>
      <div className="management-identity"><span>{config.icon}</span><div><strong>{config.title}</strong><small>{config.description}</small></div></div>
      <nav>{config.groups.map(group => <div className="management-nav-group" key={group.title}>
        <h2>{group.title}</h2>
        {group.links.map(link => <Link key={link.path} to={link.path} className={link.path === activePath ? 'is-active' : ''} aria-current={link.path === activePath ? 'page' : undefined}>{link.icon}<span>{link.label}</span></Link>)}
      </div>)}</nav>
      <div className="management-sidebar-footer">ASTRAL <span>/</span> {config.title}</div>
    </aside>
    <div className="management-main">
      <div className="management-breadcrumb"><span>{config.title}</span><span>/</span><strong>{activeLink?.label}</strong></div>
      <div className="management-content">{children}</div>
    </div>
  </div>;
};

export default ManagementWorkspace;
