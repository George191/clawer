import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Layout,
  Button,
  Space,
  Dropdown,
  Progress,
  type MenuProps,
} from '@/components/ui';
import {
  ApiOutlined,
  ApartmentOutlined,
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  BookOutlined,
  BranchesOutlined,
  CloseOutlined,
  CodeOutlined,
  ControlOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MailOutlined,
  MenuOutlined,
  PushpinOutlined,
  QuestionCircleOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
} from '@/components/ui/icons';
import { Link, matchPath, useLocation, useNavigate } from 'react-router-dom';
import { useThemeStore } from '@/stores/settings';
import { fetchCurrentUserContext } from '@/services/api';
import { clearSession } from '@/services/session';
import { getCurrentProjectDomain, resolveProjectDomain, setCurrentProjectDomain, type ProjectDomain } from '@/services/projectDomains';
import ManagementWorkspace, { managementSection } from '@/components/ManagementWorkspace';
import type { CurrentUserContext } from '@/services/types';

const { Header, Content } = Layout;

const HEADER_H = 52;
const SIDER_EXPANDED = 268;
const SIDER_COLLAPSED = 58;
const SIDER_PROJECT_H = 77;
const SIDER_FOOTER_H = 92;
const SIDER_TRANSITION_MS = 200;
const PROJECT_LOGO_SIZE = 26;
const PROJECT_LOGO_EXPANDED_X = 26;
const PROJECT_LOGO_COLLAPSED_X = (SIDER_COLLAPSED - PROJECT_LOGO_SIZE) / 2;
const PRODUCT_NAME = 'Astral';
type ProjectKey = 'ai-collect' | 'data-lake' | 'etl-pipeline' | 'data-cockpit' | 'knowledge-graph' | 'knowledge-rag';
type HeaderMenuKey = 'projects' | 'automation' | 'access' | 'settings' | 'help';

const headerMenus: { key: Exclude<HeaderMenuKey, 'projects'>; label: string; items: MenuProps['items'] }[] = [
  { key: 'automation', label: 'AUTOMATION', items: [
    { key: 'execution', type: 'group', label: 'Execution', children: [
      { key: '/automation/workflows', label: 'Workflows', icon: <BranchesOutlined /> },
      { key: '/automation/schedules', label: 'Schedules', icon: <ScheduleOutlined /> },
      { key: '/automation/runs', label: 'Runs', icon: <HistoryOutlined /> },
    ] },
  ] },
  { key: 'access', label: 'ACCESS', items: [
    { key: 'management', type: 'group', label: 'Administration', children: [
      { key: '/organization?view=users', label: 'Users', icon: <UserOutlined /> },
      { key: '/organization?view=roles', label: 'Roles', icon: <SafetyCertificateOutlined /> },
      { key: '/organization?view=permissions', label: 'Permissions', icon: <AuditOutlined /> },
      { key: '/organization?view=tenants', label: 'Organizations', icon: <ApartmentOutlined /> },
      { key: '/organization?view=teams', label: 'Teams', icon: <TeamOutlined /> },
    ] },
  ] },
  { key: 'settings', label: 'SETTINGS', items: [
    { key: 'preferences', type: 'group', label: 'Preferences', children: [
      { key: '/settings', label: 'System Settings', icon: <SettingOutlined /> },
      { key: '/agents', label: 'Agents', icon: <RobotOutlined /> },
      { key: '/account/settings', label: 'Account Settings', icon: <UserOutlined /> },
    ] },
  ] },
  { key: 'help', label: 'HELP', items: [
    { key: '/faq', label: 'Help Center', icon: <QuestionCircleOutlined /> },
    { key: 'legal', label: 'Legal', icon: <BookOutlined />, children: [
      { key: '/legal/privacy', label: 'Privacy Policy', icon: <SafetyCertificateOutlined /> },
      { key: '/legal/terms', label: 'Terms of Service', icon: <FileTextOutlined /> },
      { key: '/legal/cookies', label: 'Cookie Policy', icon: <ControlOutlined /> },
      { key: '/legal/licenses', label: 'Copyright & Licenses', icon: <BookOutlined /> },
    ] },
  ] },
];

const TemplateNavIcon: React.FC = () => (
  <span style={{ width: 21, height: 21, display: 'grid', placeItems: 'center' }} aria-hidden="true">
    <svg viewBox="0 0 480 511.65" width="16" height="17" xmlns="http://www.w3.org/2000/svg" shapeRendering="geometricPrecision" textRendering="geometricPrecision" imageRendering="optimizeQuality" fillRule="evenodd" clipRule="evenodd" aria-hidden="true">
      <path fill="currentColor" d="M84.68 237.33H375.8v-81.86h-86.02c-9.02 0-21.62-4.88-27.56-10.83-5.95-5.95-9.6-16.68-9.6-25.7V31.81H33.92c-.77 0-1.34.39-1.72.77-.58.38-.77.96-.77 1.73v443.23c0 .58.38 1.34.77 1.73.38.57 1.15.77 1.72.77h339.39c.76 0 .72-.39 1.1-.77.58-.39 1.39-1.15 1.39-1.73v-46.46H84.68c-17.25 0-31.47-14.16-31.47-31.47V268.79c0-17.31 14.16-31.46 31.47-31.46zm1.86 52.82h29.79l17.57 29.23 17.48-29.23h29.63l-33.71 50.47v36.36h-26.92v-36.36l-33.84-50.47zm143.04 72.52h-30.4l-4.36 14.31h-27.39l32.68-86.83h29.37l32.54 86.83h-28.09l-4.35-14.31zm-5.68-18.79-9.48-31.21-9.52 31.21h19zm44.32-53.73h35.4l13.48 52.84 13.52-52.84h35.23v86.83H343.9v-66.19l-16.94 66.19h-19.89l-16.9-66.19v66.19h-21.95v-86.83zm109.98 0H405v65.49h41.96v21.34H378.2v-86.83zm28.98-52.82h41.36c17.3 0 31.46 14.2 31.46 31.46v130.82c0 17.26-14.2 31.47-31.46 31.47h-41.36v56.4c0 6.72-2.69 12.66-7.1 17.08-4.41 4.41-10.36 7.09-17.07 7.09H24.17c-6.71 0-12.66-2.68-17.07-7.09C2.69 500.14 0 494.2 0 487.48V24.37C0 17.65 2.69 11.7 7.1 7.29 11.51 2.88 17.65.19 24.17.19h244.49c.58-.19 1.16-.19 1.73-.19 2.69 0 5.37 1.15 7.29 2.88h.38c.39.19.58.38.96.77l124.9 126.43c2.11 2.1 3.64 4.98 3.64 8.24 0 .96-.19 1.73-.38 2.69v96.32zM281.13 116.45V37.22l89.22 90.36h-78.09c-3.07 0-5.75-1.34-7.86-3.26-1.92-1.92-3.27-4.8-3.27-7.87z" />
    </svg>
  </span>
);

const TaskNavIcon: React.FC = () => (
  <span style={{ width: 21, height: 21, display: 'grid', placeItems: 'center' }} aria-hidden="true">
    <svg viewBox="0 0 415 512.161" width="15" height="18" xmlns="http://www.w3.org/2000/svg" shapeRendering="geometricPrecision" textRendering="geometricPrecision" imageRendering="optimizeQuality" fillRule="evenodd" clipRule="evenodd" aria-hidden="true">
      <path fill="currentColor" d="M329.42 341.001c47.265 0 85.58 38.316 85.58 85.58 0 47.265-38.315 85.58-85.58 85.58s-85.58-38.315-85.58-85.58c0-47.264 38.315-85.58 85.58-85.58zm-254.44-80.12c-4.851 0-8.793-3.94-8.793-8.791 0-4.852 3.942-8.792 8.793-8.792h226.159c4.851 0 8.792 3.94 8.792 8.792 0 4.851-3.941 8.791-8.792 8.791H74.98zm0 53.161c-4.851 0-8.793-3.941-8.793-8.793 0-4.851 3.942-8.792 8.793-8.792h179.142c4.852 0 8.793 3.941 8.793 8.792 0 4.852-3.941 8.793-8.793 8.793H74.98zm0 53.153c-4.851 0-8.793-3.941-8.793-8.792s3.942-8.793 8.793-8.793h172.265a113.04 113.04 0 00-13.497 17.585H74.98zm0 53.16c-4.851 0-8.793-3.941-8.793-8.793 0-4.851 3.942-8.791 8.793-8.791h144.366a111.824 111.824 0 00-2.355 17.584H74.98zm38.241-221.877H74.322v-60.783h19.453v45.224h19.446v15.559zm5.351-30.342c0-11.089 2.079-19.177 6.226-24.269 4.152-5.086 11.64-7.635 22.465-7.635 10.83 0 18.319 2.549 22.471 7.635 4.147 5.092 6.22 13.18 6.22 24.269 0 5.509-.434 10.144-1.31 13.908-.881 3.76-2.384 7.037-4.522 9.82-2.143 2.79-5.092 4.834-8.857 6.126-3.759 1.299-8.422 1.95-14.002 1.95-5.574 0-10.243-.651-14.002-1.95-3.764-1.292-6.713-3.336-8.852-6.126-2.137-2.783-3.647-6.06-4.521-9.82-.876-3.764-1.316-8.399-1.316-13.908zm20.915-10.114v25.285h8.07c2.655 0 4.586-.312 5.785-.929 1.204-.61 1.797-2.026 1.797-4.228v-25.285h-8.169c-2.59 0-4.488.312-5.686.923-1.198.616-1.797 2.026-1.797 4.234zm60.689 40.456h-20.521l15.751-60.783h30.055l15.758 60.783h-20.522l-2.237-9.626h-16.052l-2.232 9.626zm7.641-43.417l-3.207 19.63h11.554l-3.107-19.63h-5.24zm75.708 43.417l-14.884-21.591c-.516-.71-.846-2.266-.974-4.669h-.388v26.26h-19.453v-60.783h18.291l14.877 21.59c.516.71.839 2.268.968 4.669h.394v-26.259h19.453v60.783h-18.284zM25.027 47.339h60.008l-2.608 24.38H42.071c-11.299 0-21.126 9.832-21.126 21.121v354.362c0 11.23 9.52 21.132 21.126 21.132h182.75a112.453 112.453 0 0011.364 21.385H25.027C11.354 489.719 0 478.471 0 464.693V72.366c0-13.767 11.259-25.027 25.027-25.027zm330.088 269.609V92.84c0-11.595-9.827-21.121-21.126-21.121h-42.9v-24.38h60.009c13.766 0 25.02 11.377 25.02 25.027v251.777a111.454 111.454 0 00-21.003-7.195zM119.694 34.953h28.361C150.447 15.241 166.358 0 185.651 0c19.159 0 34.988 15.036 37.548 34.542l33.196.411a4.164 4.164 0 014.177 4.169v44.819a4.165 4.165 0 01-4.177 4.171H119.728c-2.266 0-4.169-1.862-4.169-4.171V39.122c-.042-2.308 1.82-4.169 4.135-4.169zm50.422 17.137c2.561 3.513 6.232 6.984 10.126 8.764 3.171.951 6.643 1.039 9.861.158 5.046-2.308 9.674-7.882 11.817-12.552.247-1.32.423-2.684.423-4.175 0-9.873-7.565-17.885-16.903-17.885-9.333 0-16.904 8.012-16.904 17.885.053 2.972.587 5.615 1.58 7.805zM319.36 393.449c0-13.13 20.01-13.15 20.01.026v35.861l22.614 11.963c.114.06.222.127.322.202l.2.134c10.495 6.885.733 23.421-10.493 16.926l-.045-.027-27.351-14.636c-3.209-1.715-5.283-5.11-5.283-8.754l.009-.001.017-41.694z" />
    </svg>
  </span>
);

interface SidebarItem {
  key: string;
  icon: React.ReactNode;
  label: string;
  badge?: number | string;
  muted?: boolean;
}

interface SidebarSection {
  key: string;
  label: string;
  children: SidebarItem[];
}

interface ProjectConfig {
  key: ProjectKey;
  domain: ProjectDomain;
  label: string;
  shortLabel: string;
  enabled?: boolean;
  icon: React.ReactNode;
  defaultPath: string;
  accent: string;
  sections: SidebarSection[];
}

const projectOrder: ProjectKey[] = ['ai-collect', 'data-lake', 'etl-pipeline', 'data-cockpit', 'knowledge-graph', 'knowledge-rag'];

const projectConfigs: Record<ProjectKey, ProjectConfig> = {
  'ai-collect': {
    key: 'ai-collect',
    domain: 'crawler',
    label: 'Data Collection',
    shortLabel: 'Scout',
    enabled: true,
    icon: <RobotOutlined />,
    defaultPath: '/scout',
    accent: '#7C3AED',
    sections: [
      {
        key: 'capture',
        label: 'Collection',
        children: [
          { key: '/scout', icon: <RobotOutlined />, label: 'Scout' },
          { key: '/agents', icon: <ApiOutlined />, label: 'Agents' },
        ],
      },
      {
        key: 'runtime',
        label: 'Operations',
        children: [
          { key: '/monitor', icon: <LineChartOutlined />, label: 'Monitoring' },
          { key: '/logs', icon: <FileTextOutlined />, label: 'Logs' },
        ],
      },
      {
        key: 'strategy',
        label: 'Governance',
        children: [
          { key: '/source-strategy', icon: <ControlOutlined />, label: 'Source Strategy' },
          { key: '/anti-crawl', icon: <SafetyCertificateOutlined />, label: 'Anti-crawl Identity' },
          { key: '/field-mapping', icon: <FileSearchOutlined />, label: 'Field Discovery' },
        ],
      },
    ],
  },
  'data-lake': {
    key: 'data-lake',
    domain: 'vault',
    label: 'Data Lake',
    shortLabel: 'Vault',
    enabled: false,
    icon: <DatabaseOutlined />,
    defaultPath: '/vault/catalog',
    accent: '#059669',
    sections: [
      {
        key: 'catalog',
        label: 'Catalog',
        children: [
          { key: '/vault/catalog', icon: <DatabaseOutlined />, label: 'Data Catalog' },
          { key: '/explorer', icon: <SearchOutlined />, label: 'Layer Explorer' },
          { key: '/vault/metadata', icon: <BookOutlined />, label: 'Metadata' },
        ],
      },
      {
        key: 'governance',
        label: 'Governance',
        children: [
          { key: '/vault/quality', icon: <ExperimentOutlined />, label: 'Quality Rules' },
          { key: '/vault/lineage', icon: <BranchesOutlined />, label: 'Lineage' },
          { key: '/vault/security', icon: <AuditOutlined />, label: 'Access Audit' },
        ],
      },
      {
        key: 'serving',
        label: 'Serving',
        children: [
          { key: '/data-api', icon: <ApiOutlined />, label: 'Data APIs' },
          { key: '/vault/market', icon: <BarChartOutlined />, label: 'Metrics Market' },
        ],
      },
    ],
  },
  'etl-pipeline': {
    key: 'etl-pipeline',
    domain: 'ontology',
    label: 'Helio',
    shortLabel: 'Helio',
    enabled: true,
    icon: <ApartmentOutlined />,
    defaultPath: '/ontology',
    accent: '#0EA5E9',
    sections: [],
  },
  'data-cockpit': {
    key: 'data-cockpit',
    domain: 'atlas',
    label: 'Data Cockpit',
    shortLabel: 'Atlas',
    enabled: true,
    icon: <BarChartOutlined />,
    defaultPath: '/atlas',
    accent: '#F59E0B',
    sections: [
      { key: 'overview', label: 'Overview', children: [{ key: '/atlas', icon: <BarChartOutlined />, label: 'Cockpit Overview' }] },
      { key: 'insights', label: 'Insights', children: [
        { key: '/atlas/metrics', icon: <LineChartOutlined />, label: 'Metrics' },
        { key: '/atlas/quality', icon: <AuditOutlined />, label: 'Data Quality' },
      ] },
    ],
  },
  'knowledge-graph': {
    key: 'knowledge-graph',
    domain: 'graph',
    label: 'Knowledge Graph',
    shortLabel: 'Graph',
    enabled: true,
    icon: <DeploymentUnitOutlined />,
    defaultPath: '/graph',
    accent: '#8B5CF6',
    sections: [],
  },
  'knowledge-rag': {
    key: 'knowledge-rag',
    domain: 'rag',
    label: 'Knowledge RAG',
    shortLabel: 'RAG',
    enabled: true,
    icon: <BookOutlined />,
    defaultPath: '/rag',
    accent: '#0D9488',
    sections: [],
  },
};

const legacyRouteToSidebarKey: Record<string, string> = {
  '/': '/',
  '/instances': '/vault/catalog',
  '/import': '/scout',
  '/tasks': '/tasks',
  '/graph-analytics': '/vault/lineage',
  '/explore': '/explorer',
  '/dashboards': '/',
  '/query': '/explorer',
  '/metrics': '/monitor',
  '/project-users': '/project-users',
  '/billing': '/billing',
  '/project-settings': '/project-settings',
  '/learning': '/learning',
};

const projectKeyForDomain = (domain: ProjectDomain): ProjectKey =>
  projectOrder.find(key => projectConfigs[key].domain === domain)!;

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const location = useLocation();
  const isFlowSurface = !!matchPath('/ontology/*', location.pathname);
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [pinned, setPinned] = useState(true);
  const [isMobile, setIsMobile] = useState(false);
  const [settledCollapsed, setSettledCollapsed] = useState(false);
  const [projectTextReady, setProjectTextReady] = useState(true);
  const [currentUserContext, setCurrentUserContext] = useState<CurrentUserContext | null>(null);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [openHeaderMenu, setOpenHeaderMenu] = useState<HeaderMenuKey | null>(null);

  const navigate = useNavigate();
  const { mode } = useThemeStore();
  const isDark = mode === 'dark';
  const [currentProject, setCurrentProject] = useState<ProjectKey>(() => projectKeyForDomain(getCurrentProjectDomain()));
  const routedDomain = resolveProjectDomain(location.pathname);
  const routedProject = routedDomain ? projectKeyForDomain(routedDomain) : null;
  const activeProjectKey = routedProject ?? currentProject;
  const activeProject = projectConfigs[activeProjectKey];
  const isAutomationSurface = location.pathname.startsWith('/automation');
  const isPlatformManagementSurface = location.pathname.startsWith('/organization');
  const isSystemSettingsSurface = location.pathname.startsWith('/settings');
  const activeHeaderMenus = {
    automation: isAutomationSurface,
    access: isPlatformManagementSurface,
    settings: isSystemSettingsSurface || location.pathname === '/account/settings',
    help: location.pathname === '/faq' || location.pathname.startsWith('/legal/'),
  };
  const activeHeaderPath = isPlatformManagementSurface
    ? `/organization?view=${new URLSearchParams(location.search).get('view') || 'users'}`
    : location.pathname;
  const isAtlasSurface = activeProjectKey === 'data-cockpit';
  const isGraphSurface = activeProjectKey === 'knowledge-graph';
  const isRagSurface = activeProjectKey === 'knowledge-rag';
  useEffect(() => {
    if (location.pathname.startsWith('/automation') || !new URLSearchParams(location.search).has('domain')) return;
    const next = new URLSearchParams(location.search);
    next.delete('domain');
    navigate(`${location.pathname}${next.toString() ? `?${next.toString()}` : ''}`, { replace: true });
  }, [location.pathname, location.search, navigate]);
  const isManagementSurface = managementSection(location.pathname) !== null;
  const hideSidebar = isManagementSurface || activeProjectKey === 'etl-pipeline' || activeProjectKey === 'ai-collect' || isAtlasSurface || isGraphSurface || isAutomationSurface || isPlatformManagementSurface || isSystemSettingsSurface;
  const palette = {
    appBg: isDark ? '#171A22' : '#F6F8FB',
    surface: isDark ? '#22262F' : '#FFFFFF',
    sidebar: isDark ? '#20242C' : '#FFFFFF',
    border: isDark ? 'rgba(255, 255, 255, 0.08)' : '#E2E8F0',
    borderSoft: isDark ? 'rgba(255, 255, 255, 0.05)' : '#EEF2F7',
    text: isDark ? '#F1F5F9' : '#0F172A',
    secondary: isDark ? '#A8B0BD' : '#64748B',
    muted: isDark ? '#6B7280' : '#94A3B8',
    hover: isDark ? 'rgba(255, 255, 255, 0.06)' : '#F1F5F9',
  };

  const handleResize = useCallback(() => {
    const nextIsMobile = window.innerWidth < 768;
    setIsMobile(nextIsMobile);
    if (nextIsMobile) {
      setManualCollapsed(true);
    } else if (pinned) {
      setManualCollapsed(false);
    }
  }, [pinned]);

  useEffect(() => {
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [handleResize]);

  useEffect(() => {
    setOpenHeaderMenu(null);
  }, [location.pathname, location.search, isMobile]);

  useEffect(() => {
    if (routedProject && routedProject !== currentProject) {
      setCurrentProject(routedProject);
    }
  }, [currentProject, routedProject]);

  useEffect(() => {
    document.title = `${managementSection(location.pathname)?.toUpperCase() || activeProject.shortLabel} - ${PRODUCT_NAME}`;
  }, [activeProject.shortLabel, location.pathname]);

  useEffect(() => {
    let cancelled = false;
    fetchCurrentUserContext()
      .then((context) => {
        if (cancelled) return;
        setCurrentUserContext(context);
      })
      .catch(() => {
        if (!cancelled) {
          setCurrentUserContext(null);
        }
      });
    return () => { cancelled = true; };
  }, []);

  const currentUserName = currentUserContext?.user.full_name?.trim()
    || currentUserContext?.user.email.split('@')[0]
    || 'Account';
  const currentUserEmail = currentUserContext?.user.email ?? '';
  const currentUserRole = currentUserContext
    ? currentUserContext.user.is_superuser ? 'Super administrator' : currentUserContext.user.is_tenant_admin ? 'Administrator' : 'Member'
    : '';

  const collapsed = isMobile ? manualCollapsed : !pinned && manualCollapsed;
  const projectTextVisible = !collapsed && projectTextReady;
  const projectLogoX = collapsed ? PROJECT_LOGO_COLLAPSED_X : PROJECT_LOGO_EXPANDED_X;
  const siderWidth = hideSidebar ? 0 : isMobile ? (collapsed ? 0 : SIDER_EXPANDED) : collapsed ? SIDER_COLLAPSED : SIDER_EXPANDED;
  const contentMarginLeft = hideSidebar || isMobile ? 0 : siderWidth;
  const normalizedPath = legacyRouteToSidebarKey[location.pathname] ?? location.pathname;

  useEffect(() => {
    if (!collapsed) {
      setSettledCollapsed(false);
      setProjectTextReady(false);
      const timer = window.setTimeout(() => {
        setProjectTextReady(true);
      }, SIDER_TRANSITION_MS);

      return () => window.clearTimeout(timer);
    }

    setProjectTextReady(false);
    const timer = window.setTimeout(() => {
      setSettledCollapsed(true);
    }, SIDER_TRANSITION_MS);

    return () => window.clearTimeout(timer);
  }, [collapsed]);

  const selectedKey = useMemo(() => {
    const items = activeProject.sections
      .flatMap((section) => section.children)
      .sort((a, b) => b.key.length - a.key.length);
    return items.find((item) => normalizedPath === item.key || normalizedPath.startsWith(`${item.key}/`))?.key;
  }, [activeProject.sections, normalizedPath]);

  const handleProjectChange = (key: string) => {
    setOpenHeaderMenu(null);
    const nextProject = key as ProjectKey;
    setCurrentProject(nextProject);
    const targetPath = projectConfigs[nextProject].defaultPath;
    if (location.pathname !== targetPath) {
      navigate(targetPath);
    }
    setCurrentProjectDomain(projectConfigs[nextProject].domain);
    if (isMobile) {
      setManualCollapsed(true);
    }
  };

  const handlePinToggle = () => {
    if (pinned) {
      setPinned(false);
      setManualCollapsed(true);
    } else {
      setPinned(true);
      setManualCollapsed(false);
    }
  };

  const logout = () => {
    clearSession();
    navigate('/login', { replace: true });
  };

  const changeHeaderMenu = (key: HeaderMenuKey, open: boolean) => {
    setOpenHeaderMenu(current => open ? key : current === key ? null : current);
    if (open) setAccountMenuOpen(false);
  };

  const followHeaderMenu = (key: HeaderMenuKey, event: React.PointerEvent<HTMLButtonElement>) => {
    if (openHeaderMenu && event.pointerType === 'mouse') setOpenHeaderMenu(key);
  };

  const moveHeaderMenu = (key: HeaderMenuKey, event: React.KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const keys: HeaderMenuKey[] = ['projects', ...headerMenus.map(menu => menu.key)];
    const next = (keys.indexOf(key) + (event.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length;
    if (openHeaderMenu) changeHeaderMenu(keys[next], true);
    document.querySelectorAll<HTMLButtonElement>('.platform-header-nav .platform-header-menu')[next]?.focus();
  };

  const overlayShellStyle: React.CSSProperties = {
    padding: '6px 0',
    borderRadius: 18,
    background: isDark ? '#202326' : '#FFFFFF',
    border: `1px solid ${isDark ? 'rgba(255, 255, 255, 0.12)' : '#E2E8F0'}`,
    boxShadow: isDark ? '0 18px 44px rgba(0, 0, 0, 0.45)' : '0 18px 38px rgba(15, 23, 42, 0.14)',
  };

  const overlayRowStyle: React.CSSProperties = {
    width: '100%',
    minHeight: 40,
    border: 'none',
    background: 'transparent',
    color: palette.text,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 12px',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 500,
    textAlign: 'left',
  };

  const bindOverlayHover = (
    event: React.MouseEvent<HTMLButtonElement>,
    options?: { accent?: string },
  ) => {
    event.currentTarget.style.background = options?.accent ?? palette.hover;
  };

  const resetOverlayHover = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.currentTarget.style.background = 'transparent';
    event.currentTarget.style.boxShadow = 'none';
  };

  const renderProjectPanel = () => (
    <div style={{ padding: 6 }} onKeyDown={event => {
      moveHeaderMenu('projects', event);
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const rows = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
      const index = rows.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
      rows[next]?.focus();
    }}>
      {projectOrder.map((key) => {
        const project = projectConfigs[key];
        const isActive = key === activeProjectKey;
        const isDisabled = project.enabled === false;
        return (
          <React.Fragment key={key}>
            <button
              type="button"
              disabled={isDisabled}
              aria-disabled={isDisabled}
              onClick={() => { if (!isDisabled) handleProjectChange(key); }}
              style={{
                ...overlayRowStyle,
                minHeight: 34,
                padding: '6px 10px',
                gap: 8,
                fontSize: 12,
                color: isDisabled ? '#8f99a8' : '#d3d8de',
                opacity: isDisabled ? 0.62 : 1,
                cursor: isDisabled ? 'not-allowed' : 'pointer',
              }}
              onMouseEnter={(event) => { if (!isDisabled) bindOverlayHover(event, {
                accent: '#2f343c',
              }); }}
              onMouseLeave={resetOverlayHover}
            >
              <span
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: 5,
                  background: isDisabled ? '#37424f' : `linear-gradient(135deg, ${project.accent}, #1D4ED8)`,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: 9,
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                {project.shortLabel.slice(0, 1)}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 12, fontWeight: isActive ? 600 : 400 }}>{project.shortLabel}</span>
              </span>
              {isDisabled ? (
                <span style={{ color: '#8f99a8', fontSize: 11, fontWeight: 400 }}>Not enabled yet</span>
              ) : isActive ? (
                <span style={{ color: '#8f99a8', fontSize: 11, fontWeight: 400 }}>Current</span>
              ) : (
                <RightOutlined style={{ color: '#8f99a8', fontSize: 9 }} />
              )}
            </button>
          </React.Fragment>
        );
      })}
    </div>
  );

  const renderNotificationPanel = () => (
    <div
      style={{
        ...overlayShellStyle,
        width: 300,
        padding: '6px 0',
        overflow: 'hidden',
      }}
    >
      <div style={{ minHeight: 44, padding: '6px 14px 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ flex: 1, color: palette.text, fontSize: 13, fontWeight: 600 }}>Notifications</span>
        <span style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(13, 147, 148, 0.12)', color: '#38C4C4', fontSize: 9, fontWeight: 600 }}>3 New</span>
        <button type="button" aria-label="Mark all as read" title="Mark all as read" style={{ width: 28, height: 28, padding: 0, display: 'grid', placeItems: 'center', border: 0, borderRadius: 5, background: 'transparent', color: palette.secondary, cursor: 'pointer' }}>
          <MailOutlined />
        </button>
      </div>
      <div style={{ height: 1, margin: '0 14px 4px', background: palette.border }} />
      {[
        { title: 'Collection complete', desc: 'Google Patent data was written to the ODS layer', time: '1h ago', color: '#28C76F', tint: 'rgba(40, 199, 111, 0.14)', icon: <RobotOutlined /> },
        { title: 'Data quality alert', desc: 'navwarn.content missing values exceed the threshold', time: '4h ago', color: '#FF9F43', tint: 'rgba(255, 159, 67, 0.14)', icon: <AuditOutlined /> },
        { title: 'ETL schedule notice', desc: 'DWD aggregation is scheduled for 18:30', time: 'Yesterday', color: '#0D9394', tint: 'rgba(13, 147, 148, 0.14)', icon: <ScheduleOutlined /> },
      ].map(({ title, desc, time, color, tint, icon }) => (
        <div
          key={title}
          style={{
            minHeight: 84,
            width: 'calc(100% - 14px)',
            margin: '0 7px',
            padding: '10px 8px',
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            background: 'transparent',
            borderRadius: 6,
            cursor: 'pointer',
            boxSizing: 'border-box',
          }}
          onMouseEnter={(event) => {
            event.currentTarget.style.background = palette.hover;
            const dismissButton = event.currentTarget.querySelector<HTMLButtonElement>('[data-notification-dismiss]');
            if (dismissButton) dismissButton.style.opacity = '1';
          }}
          onMouseLeave={(event) => {
            event.currentTarget.style.background = 'transparent';
            const dismissButton = event.currentTarget.querySelector<HTMLButtonElement>('[data-notification-dismiss]');
            if (dismissButton) dismissButton.style.opacity = '0.35';
          }}
        >
          <span style={{ width: 34, height: 34, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: '50%', background: tint, color, fontSize: 14, flexShrink: 0 }}>
            {icon}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', overflow: 'hidden', color: palette.text, fontSize: 12, fontWeight: 600, lineHeight: '18px', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
            <span style={{ display: 'block', overflow: 'hidden', color: palette.secondary, fontSize: 10, lineHeight: '17px', marginTop: 4, textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{desc}</span>
            <span style={{ display: 'block', overflow: 'hidden', color: palette.muted, fontSize: 9, lineHeight: '15px', marginTop: 4, textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{time}</span>
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, paddingTop: 3, flexShrink: 0 }}>
            <span aria-label="Unread" style={{ width: 6, height: 6, borderRadius: '50%', background: '#0D9394' }} />
            <button data-notification-dismiss type="button" aria-label={`Dismiss ${title}`} style={{ width: 18, height: 18, padding: 0, border: 0, background: 'transparent', color: palette.muted, cursor: 'pointer', fontSize: 10, opacity: 0.35, transition: 'opacity 120ms ease' }}><CloseOutlined /></button>
          </span>
        </div>
      ))}
      <div style={{ height: 1, margin: '4px 14px 6px', background: palette.border }} />
      <div style={{ padding: '0 7px' }}>
        <Button type="primary" block style={{ height: 32, borderRadius: 5, fontSize: 11, boxShadow: 'none' }}>View all notifications</Button>
      </div>
    </div>
  );

  const renderAccountPanel = () => {
    const accountRows = [
      { key: 'account', icon: <SettingOutlined />, label: 'Account Settings', onClick: () => { setAccountMenuOpen(false); navigate('/account/settings'); } },
      { key: 'faq', icon: <QuestionCircleOutlined />, label: 'FAQ', onClick: () => { setAccountMenuOpen(false); navigate('/faq'); } },
      { key: 'legal', icon: <SafetyCertificateOutlined />, label: 'Legal', onClick: () => { setAccountMenuOpen(false); navigate('/legal/privacy'); } },
    ];

    return (
      <div
        style={{
          width: 252,
        }}
      >
        <div style={{ padding: '7px 12px 8px' }}>
          <div style={{ color: palette.text, fontSize: 12, fontWeight: 600 }}>{currentUserName}</div>
          {currentUserEmail ? <div style={{ color: palette.secondary, fontSize: 11, marginTop: 2 }}>{currentUserEmail}</div> : null}
        </div>
        {accountRows.map((item, index) => (
          <React.Fragment key={item.key}>
            <button
              type="button"
              onClick={item.onClick}
              style={{
                ...overlayRowStyle,
                minHeight: 32,
                padding: '0 8px',
                gap: 7,
                color: overlayRowStyle.color,
                fontSize: 12,
              }}
              onMouseEnter={(event) => {
                bindOverlayHover(event, item.key === 'account' ? {
                  accent: isDark ? 'rgba(143, 227, 232, 0.12)' : '#E0F2FE',
                } : undefined);
              }}
              onMouseLeave={resetOverlayHover}
            >
              <span style={{ width: 24, display: 'inline-flex', justifyContent: 'center', color: palette.secondary, fontSize: 14 }}>
                {item.icon}
              </span>
              <span style={{ flex: 1 }}>{item.label}</span>
            </button>
          </React.Fragment>
        ))}
        <div style={{ height: 1, background: palette.border, margin: '6px 14px' }} />
        <button
          type="button"
          onClick={() => { setAccountMenuOpen(false); logout(); }}
          style={{
            ...overlayRowStyle,
            minHeight: 34,
            margin: '0 7px',
            width: 'calc(100% - 14px)',
            boxSizing: 'border-box',
            padding: '0 8px',
            gap: 7,
            borderRadius: 9,
            color: isDark ? '#ff9b9b' : '#DC2626',
            background: isDark ? 'rgba(239, 68, 68, 0.08)' : '#FEF2F2',
          }}
          onMouseEnter={(event) => bindOverlayHover(event, {
            accent: isDark ? 'rgba(239, 68, 68, 0.16)' : '#FEE2E2',
          })}
          onMouseLeave={resetOverlayHover}
        >
          <span style={{ width: 24, display: 'inline-flex', justifyContent: 'center', color: 'inherit', fontSize: 14 }}><LogoutOutlined /></span>
          <span style={{ flex: 1, textAlign: 'left', fontWeight: 600, fontSize: 12 }}>Log out</span>
        </button>
      </div>
    );
  };

  return (
    <div className={isFlowSurface ? 'flow-platform-shell' : undefined} style={{ height: '100vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: palette.appBg, ...(isFlowSurface ? { '--flow-header-height': `${HEADER_H}px` } : {}) } as React.CSSProperties}>
      <Header
        className="platform-header"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          height: HEADER_H,
          zIndex: 101,
        }}
      >
        {isMobile && !hideSidebar && (
          <button
            type="button"
            className="platform-header-icon"
            aria-label="Open project sidebar"
            onClick={() => setManualCollapsed(false)}
          ><MenuOutlined /></button>
        )}
        <button
          type="button"
          className="platform-header-brand"
          aria-label={`${PRODUCT_NAME} home`}
          title={PRODUCT_NAME}
          onClick={() => navigate('/')}
        >
          <img src="/project-network-mark.svg" width="36" height="36" alt="" />
          <span className="platform-header-wordmark">{PRODUCT_NAME}</span>
        </button>
        <nav className="platform-header-nav" aria-label="Main navigation">
          <Dropdown
            trigger={['click']}
            open={openHeaderMenu === 'projects'}
            onOpenChange={open => changeHeaderMenu('projects', open)}
            overlayClassName="platform-header-dropdown"
            overlayStyle={{ minWidth: 220, marginTop: 5 }}
            menu={{ items: [], submenuMode: 'popup' }}
            popupRender={renderProjectPanel}
          >
            <button
              type="button"
              className="platform-header-menu"
              aria-label={`Switch project domain: ${activeProject.shortLabel}`}
              onPointerEnter={event => followHeaderMenu('projects', event)}
              onKeyDown={event => moveHeaderMenu('projects', event)}
            >
              PROJECTS
            </button>
          </Dropdown>
          {headerMenus.map(menu => (
            <Dropdown
              key={menu.key}
              trigger={['click']}
              open={openHeaderMenu === menu.key}
              onOpenChange={open => changeHeaderMenu(menu.key, open)}
              overlayClassName="platform-header-dropdown"
              overlayStyle={{ minWidth: 220, marginTop: 5 }}
              menu={{ items: menu.items, submenuMode: 'popup', selectedKeys: [activeHeaderPath], onClick: ({ key }) => navigate(key), onKeyDown: (event: React.KeyboardEvent) => moveHeaderMenu(menu.key, event) }}
            >
              <button
                type="button"
                className="platform-header-menu"
                aria-label={menu.label}
                aria-current={activeHeaderMenus[menu.key] ? 'page' : undefined}
                onPointerEnter={event => followHeaderMenu(menu.key, event)}
                onKeyDown={event => moveHeaderMenu(menu.key, event)}
              >{menu.label}</button>
            </Dropdown>
          ))}
        </nav>

        <div className="platform-header-status">
          <div className="platform-header-project">
            <b>PROJECT:</b><span>{activeProject.shortLabel}</span>
          </div>
          <span className="platform-header-message" role="img" aria-label="Messages" title="Messages">
            <svg viewBox="0 0 1024 1024" fill="currentColor" aria-hidden="true" focusable="false">
              <path d="M396.359111 970.666667a28.444444 28.444444 0 0 1-28.444444-29.184l2.773333-105.912889h-71.324444c-54.357333 0-93.169778-44.8-93.169778-99.854222V363.989333c0-53.660444 38.812444-97.322667 93.169778-97.322666h570.837333c54.357333 0 104.007111 43.648 104.007111 97.322666v371.712c0 55.054222-49.635556 99.854222-104.007111 99.854223H593.948444l-181.020444 129.777777a28.344889 28.344889 0 0 1-16.568889 5.333334z m-96.967111-647.111111c-23.381333 0-36.280889 17.763556-36.280889 40.433777v371.712c0 23.310222 13.681778 42.965333 36.280889 42.965334h100.522667a28.401778 28.401778 0 0 1 28.430222 29.198222l-2.048 77.895111 141.923555-101.745778c4.835556-3.470222 10.624-5.333333 16.568889-5.333333h285.411556c22.599111 0 47.118222-19.669333 47.118222-42.965333V363.989333c0-22.670222-23.736889-40.433778-47.118222-40.433777H299.392z" />
              <path d="M95.004444 599.480889c-6.712889 0-10.723556-2.360889-16.142222-7.168C58.055111 573.895111 49.777778 549.703111 49.777778 522.368V150.656c0-53.660444 38.812444-97.322667 93.169778-97.322667h570.837333c54.357333 0 104.007111 43.648 104.007111 97.322667v16.455111a28.444444 28.444444 0 0 1-56.888889 0v-16.455111c0-22.670222-23.736889-40.433778-47.118222-40.433778H142.947556C119.566222 110.222222 106.666667 127.985778 106.666667 150.656v371.712c0 10.894222 1.422222 19.84 9.912889 27.363556 11.761778 10.410667 11.505778 28.416 1.080888 40.163555-5.632 6.328889-14.805333 9.585778-22.656 9.585778z" />
              <path d="M485.248 451.555556h-113.777778a14.222222 14.222222 0 1 1 0-28.444445h113.777778a14.222222 14.222222 0 1 1 0 28.444445zM684.359111 451.555556h-142.222222a14.222222 14.222222 0 1 1 0-28.444445h142.222222a14.222222 14.222222 0 1 1 0 28.444445zM798.136889 451.555556h-56.888889a14.222222 14.222222 0 1 1 0-28.444445h56.888889a14.222222 14.222222 0 1 1 0 28.444445zM428.359111 522.666667h-56.888889a14.222222 14.222222 0 1 1 0-28.444445h56.888889a14.222222 14.222222 0 1 1 0 28.444445zM599.025778 522.666667h-113.777778a14.222222 14.222222 0 1 1 0-28.444445h113.777778a14.222222 14.222222 0 1 1 0 28.444445zM798.136889 522.666667h-142.222222a14.222222 14.222222 0 1 1 0-28.444445h142.222222a14.222222 14.222222 0 1 1 0 28.444445z" />
              <path d="M513.692444 608h-142.222222a14.222222 14.222222 0 1 1 0-28.444444h142.222222a14.222222 14.222222 0 1 1 0 28.444444zM712.803556 608h-142.222223a14.222222 14.222222 0 1 1 0-28.444444h142.222223a14.222222 14.222222 0 1 1 0 28.444444zM798.136889 608h-28.444445a14.222222 14.222222 0 1 1 0-28.444444h28.444445a14.222222 14.222222 0 1 1 0 28.444444z" />
              <path d="M428.359111 679.111111h-56.888889a14.222222 14.222222 0 1 1 0-28.444444h56.888889a14.222222 14.222222 0 1 1 0 28.444444zM584.803556 679.111111h-99.555556a14.222222 14.222222 0 1 1 0-28.444444h99.555556a14.222222 14.222222 0 1 1 0 28.444444z" />
            </svg>
          </span>
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            open={accountMenuOpen}
            onOpenChange={open => { setAccountMenuOpen(open); if (open) setOpenHeaderMenu(null); }}
            menu={{ items: [] }}
            popupRender={renderAccountPanel}
          >
            <button
              type="button"
              className="platform-header-account"
              aria-label="Open account menu"
            >
              <span className="platform-header-avatar" aria-hidden="true">
                <svg className="platform-header-avatar-warn" viewBox="0 0 1024 1024" focusable="false">
                  <path fill="currentColor" d="M1019.20384 901.632L546.62784 82.944c-15.36-26.112-52.736-26.112-68.096 0L5.44384 901.632c-15.36 26.112 3.584 58.88 34.304 58.88h945.152c30.208 0 49.152-32.768 34.304-58.88z" />
                  <path fill="#FFFFFF" d="M462.14784 353.792c0.512-26.112 21.504-47.104 47.616-47.104h5.632c26.112 0 47.104 20.992 47.104 47.104v254.464c0 26.112-21.504 47.616-48.128 47.104h-4.608c-26.112 0-47.616-20.992-47.616-47.104V353.792zM569.66784 775.68c-5.12 26.112-20.48 41.984-46.592 47.616-6.144 1.536-12.288 1.536-18.432 0-27.136-5.632-43.52-20.992-49.152-46.592-1.536-6.656-1.536-13.312 0-19.456 5.632-25.6 22.016-40.96 49.152-46.08 6.144-1.024 12.288-1.024 18.432 0 26.112 5.632 41.472 20.992 46.592 47.104 1.024 5.632 1.024 11.776 0 17.408z" />
                </svg>
                <svg className="platform-header-avatar-icon" viewBox="0 0 1024 1024" focusable="false">
                  <path fill="currentColor" d="M512 74.666667C270.933333 74.666667 74.666667 270.933333 74.666667 512S270.933333 949.333333 512 949.333333 949.333333 753.066667 949.333333 512 753.066667 74.666667 512 74.666667z m0 160c70.4 0 128 57.6 128 128s-57.6 128-128 128-128-57.6-128-128 57.6-128 128-128z m236.8 507.733333c-23.466667 32-117.333333 100.266667-236.8 100.266667s-213.333333-68.266667-236.8-100.266667c-8.533333-10.666667-10.666667-21.333333-8.533333-32 29.866667-110.933333 130.133333-187.733333 245.333333-187.733333s215.466667 76.8 245.333333 187.733333c2.133333 10.666667 0 21.333333-8.533333 32z" />
                </svg>
              </span>
              <span className="platform-header-user">
                <strong>{currentUserName}</strong>
                {currentUserRole && <small>{currentUserRole}</small>}
              </span>
            </button>
          </Dropdown>
        </div>
      </Header>

      <div style={{ height: HEADER_H, flexShrink: 0 }} />
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {isMobile && !collapsed && !hideSidebar && (
          <button
            type="button"
            aria-label="Close sidebar overlay"
            onClick={() => setManualCollapsed(true)}
            style={{
              position: 'fixed',
              inset: `${HEADER_H}px 0 0 0`,
              background: 'rgba(0, 0, 0, 0.42)',
              border: 'none',
              zIndex: 99,
            }}
          />
        )}

        {!hideSidebar && (
        <aside
          data-testid="project-sidebar"
          style={{
            position: 'fixed',
            left: 0,
            top: HEADER_H,
            bottom: 0,
            width: siderWidth,
            background: palette.sidebar,
            borderRight: `1px solid ${palette.border}`,
            zIndex: 100,
            overflow: 'hidden',
            transition: 'width 0.2s ease',
            display: isMobile && collapsed ? 'none' : 'flex',
            flexDirection: 'column',
            boxShadow: isMobile && !collapsed ? '16px 0 34px rgba(0, 0, 0, 0.28)' : 'none',
          }}
          onMouseEnter={() => {
            if (!pinned && !isMobile) setManualCollapsed(false);
          }}
          onMouseLeave={() => {
            if (!pinned && !isMobile) setManualCollapsed(true);
          }}
        >
          <div
            style={{
              height: SIDER_PROJECT_H,
              padding: '14px 0',
              borderBottom: `1px solid ${palette.border}`,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <div
                style={{
                  width: '100%',
                  height: 48,
                  border: 'none',
                  borderRadius: 8,
                  color: palette.text,
                  display: 'block',
                  padding: 0,
                  cursor: 'default',
                  textAlign: 'left',
                  overflow: 'hidden',
                  position: 'relative',
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    left: projectLogoX,
                    top: 10,
                    width: PROJECT_LOGO_SIZE,
                    height: PROJECT_LOGO_SIZE,
                    borderRadius: 7,
                    background: `linear-gradient(135deg, ${activeProject.accent}, #1D4ED8)`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  color: '#fff',
                    fontWeight: 800,
                    transition: `left ${SIDER_TRANSITION_MS}ms ease`,
                  }}
                >
                  {activeProject.shortLabel.slice(0, 1)}
                </span>
                {!collapsed && (
                  <>
                    <span
                      style={{
                        position: 'absolute',
                        left: 64,
                        right: 28,
                        top: 17,
                        opacity: projectTextVisible ? 1 : 0,
                        transform: projectTextVisible ? 'translateX(0)' : 'translateX(-6px)',
                        transition: 'opacity 0.12s ease, transform 0.12s ease',
                        pointerEvents: projectTextVisible ? 'auto' : 'none',
                      }}
                    >
                      <span style={{ display: 'block', fontSize: 13, fontWeight: 700 }}>
                        {activeProject.shortLabel}
                      </span>
                    </span>
                  </>
                )}
            </div>
          </div>

          <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px 0' }} className="neo4j-sidebar-scroll">
            <div style={{ minWidth: SIDER_EXPANDED }}>
              {activeProject.sections.map((section, sectionIndex) => (
                <div
                  key={section.key}
                  style={{
                    paddingTop: sectionIndex === 0 ? 0 : 10,
                    marginTop: sectionIndex === 0 ? 0 : 8,
                    borderTop: sectionIndex === 0 ? 'none' : `1px solid ${palette.borderSoft}`,
                  }}
                >
                  <div
                    style={{
                      height: 28,
                      display: 'flex',
                      alignItems: 'center',
                      padding: '0 18px',
                      color: palette.muted,
                      fontSize: 11,
                      fontWeight: 700,
                      userSelect: 'none',
                      visibility: collapsed ? 'hidden' : 'visible',
                    }}
                  >
                    {section.label}
                  </div>

                  {section.children.map((item) => {
                    const isActive = selectedKey === item.key;
                    const itemColor = item.muted ? palette.muted : isActive ? activeProject.accent : palette.secondary;
                    const itemBg = isActive ? `${activeProject.accent}1F` : 'transparent';

                    return (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => {
                          navigate(item.key);
                          if (isMobile) setManualCollapsed(true);
                        }}
                        style={{
                          width: 'calc(100% - 12px)',
                          height: 38,
                          margin: '1px 12px 1px 0',
                          border: 'none',
                          borderRadius: '0 8px 8px 0',
                          background: itemBg,
                          color: itemColor,
                          display: 'flex',
                          alignItems: 'center',
                          position: 'relative',
                          cursor: 'pointer',
                          paddingLeft: 18,
                          fontSize: 13,
                          fontWeight: isActive ? 700 : 500,
                          textAlign: 'left',
                          transition: 'background 0.15s ease, color 0.15s ease',
                        }}
                        onMouseEnter={(event) => {
                          if (!isActive) {
                            event.currentTarget.style.background = palette.hover;
                            event.currentTarget.style.color = palette.text;
                          }
                        }}
                        onMouseLeave={(event) => {
                          if (!isActive) {
                            event.currentTarget.style.background = 'transparent';
                            event.currentTarget.style.color = itemColor;
                          }
                        }}
                      >
                        <span
                          style={{
                            position: 'absolute',
                            left: 0,
                            top: 7,
                            bottom: 7,
                            width: 3,
                            borderRadius: '0 2px 2px 0',
                            background: isActive ? activeProject.accent : 'transparent',
                          }}
                        />
                        <span
                          style={{
                            marginRight: 11,
                            fontSize: 16,
                            display: 'flex',
                            alignItems: 'center',
                            width: 20,
                            justifyContent: 'center',
                            flexShrink: 0,
                          }}
                        >
                          {item.icon}
                        </span>
                        <span
                          style={{
                            flex: 1,
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            visibility: collapsed ? 'hidden' : 'visible',
                          }}
                        >
                          {item.label}
                        </span>
                        {!collapsed && item.badge !== undefined && (
                          <span
                            style={{
                              minWidth: 22,
                              height: 18,
                              padding: '0 7px',
                              borderRadius: 9,
                              background: isActive ? `${activeProject.accent}33` : isDark ? 'rgba(255, 255, 255, 0.06)' : '#E2E8F0',
                              color: isActive ? activeProject.accent : palette.secondary,
                              fontSize: 11,
                              lineHeight: '18px',
                              textAlign: 'center',
                              marginRight: 8,
                            }}
                          >
                            {item.badge}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </nav>

          <div
            style={{
              flexShrink: 0,
              height: SIDER_FOOTER_H,
              borderTop: `1px solid ${palette.border}`,
              padding: '12px 14px',
              display: 'grid',
              gridTemplateRows: '38px 32px',
              rowGap: 10,
              alignItems: 'center',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: SIDER_EXPANDED - 28,
                maxWidth: SIDER_EXPANDED - 28,
                height: 38,
                overflow: 'hidden',
                opacity: collapsed ? 0 : 1,
                transition: 'opacity 0.1s ease',
                pointerEvents: collapsed ? 'none' : 'auto',
              }}
            >
              {!collapsed && (
                <>
                <div style={{ display: 'flex', justifyContent: 'space-between', color: palette.secondary, fontSize: 12, marginBottom: 6 }}>
                  <span>Pipeline health today</span>
                  <span>92%</span>
                </div>
                <Progress percent={92} showInfo={false} strokeColor={activeProject.accent} trailColor={isDark ? 'rgba(255,255,255,0.08)' : '#E2E8F0'} size="small" />
                </>
              )}
            </div>
            {!isMobile && (
              <div style={{ height: 32, display: 'flex', alignItems: 'center', justifyContent: collapsed && settledCollapsed ? 'center' : 'flex-end' }}>
                <Button
                  type="text"
                  icon={<PushpinOutlined />}
                  onClick={handlePinToggle}
                  aria-label={pinned ? 'Unpin sidebar' : 'Pin sidebar'}
                  style={{
                    width: 32,
                    height: 32,
                    color: pinned ? activeProject.accent : palette.secondary,
                    border: 'none',
                    fontSize: 14,
                  }}
                />
              </div>
            )}
          </div>
        </aside>
        )}

        <main
          style={{
            marginLeft: contentMarginLeft,
            transition: 'margin-left 0.2s ease',
            width: '100%',
            flex: 1,
            minHeight: 0,
            background: palette.appBg,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Content style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div className={isDark ? '' : 'light-mode'} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {routedProject && (isAtlasSurface || isGraphSurface || isRagSurface) ? null : <ManagementWorkspace>{children}</ManagementWorkspace>}
            </div>
          </Content>
        </main>
      </div>
    </div>
  );
};

export default MainLayout;
