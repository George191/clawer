import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Layout,
  Button,
  Space,
  Dropdown,
  Badge,
  Progress,
} from 'antd';
import {
  ApiOutlined,
  ApartmentOutlined,
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  ControlOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  DownOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MenuOutlined,
  PushpinOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SearchOutlined,
  SettingOutlined,
  SkinOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useThemeStore } from '@/stores/settings';

const { Header, Content } = Layout;

const HEADER_H = 52;
const SIDER_EXPANDED = 268;
const SIDER_COLLAPSED = 58;
const SIDER_PROJECT_H = 77;
const SIDER_FOOTER_H = 92;
const SIDER_TRANSITION_MS = 200;
const PROJECT_LOGO_SIZE = 28;
const PROJECT_LOGO_EXPANDED_X = 26;
const PROJECT_LOGO_COLLAPSED_X = (SIDER_COLLAPSED - PROJECT_LOGO_SIZE) / 2;
const PRODUCT_NAME_ZH = '星穹智数中台';
type ProjectKey = 'ai-collect' | 'data-lake' | 'etl-pipeline';

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
  label: string;
  shortLabel: string;
  defaultPath: string;
  accent: string;
  sections: SidebarSection[];
}

const projectOrder: ProjectKey[] = ['ai-collect', 'data-lake', 'etl-pipeline'];

const projectConfigs: Record<ProjectKey, ProjectConfig> = {
  'ai-collect': {
    key: 'ai-collect',
    label: 'AI 智能采集',
    shortLabel: 'AI Collect',
    defaultPath: '/ai-collect',
    accent: '#7C3AED',
    sections: [
      {
        key: 'capture',
        label: '采集编排',
        children: [
          { key: '/ai-collect', icon: <RobotOutlined />, label: '智能采集' },
        ],
      },
      {
        key: 'runtime',
        label: '运行观测',
        children: [
          { key: '/monitor', icon: <LineChartOutlined />, label: '实时监控' },
          { key: '/logs', icon: <FileTextOutlined />, label: '日志追踪' },
        ],
      },
      {
        key: 'strategy',
        label: '策略治理',
        children: [
          { key: '/source-strategy', icon: <ControlOutlined />, label: '源站策略' },
          { key: '/anti-crawl', icon: <SafetyCertificateOutlined />, label: '反爬身份' },
          { key: '/field-mapping', icon: <FileSearchOutlined />, label: '字段识别' },
        ],
      },
    ],
  },
  'data-lake': {
    key: 'data-lake',
    label: '数据湖',
    shortLabel: 'Data Lake',
    defaultPath: '/lake/catalog',
    accent: '#059669',
    sections: [
      {
        key: 'catalog',
        label: '湖仓目录',
        children: [
          { key: '/lake/catalog', icon: <DatabaseOutlined />, label: '数据目录' },
          { key: '/explorer', icon: <SearchOutlined />, label: '分层浏览' },
          { key: '/lake/metadata', icon: <BookOutlined />, label: '元数据' },
        ],
      },
      {
        key: 'governance',
        label: '治理质量',
        children: [
          { key: '/lake/quality', icon: <ExperimentOutlined />, label: '质量规则' },
          { key: '/lake/lineage', icon: <BranchesOutlined />, label: '血缘关系' },
          { key: '/lake/security', icon: <AuditOutlined />, label: '权限审计' },
        ],
      },
      {
        key: 'serving',
        label: '服务输出',
        children: [
          { key: '/data-api', icon: <ApiOutlined />, label: '数据 API' },
          { key: '/lake/market', icon: <BarChartOutlined />, label: '指标集市' },
        ],
      },
    ],
  },
  'etl-pipeline': {
    key: 'etl-pipeline',
    label: 'ETL 管道',
    shortLabel: 'ETL Pipeline',
    defaultPath: '/pipeline',
    accent: '#0EA5E9',
    sections: [
      {
        key: 'development',
        label: '管道开发',
        children: [
          { key: '/pipeline', icon: <ApartmentOutlined />, label: '管道画布' },
          { key: '/pipeline/tasks', icon: <ScheduleOutlined />, label: '任务中心', badge: 12 },
          { key: '/pipeline/templates', icon: <CodeOutlined />, label: '处理器模板' },
        ],
      },
      {
        key: 'orchestration',
        label: '调度运行',
        children: [
          { key: '/pipeline/schedule', icon: <FieldTimeOutlined />, label: '编排调度' },
          { key: '/monitor', icon: <LineChartOutlined />, label: '监控指标' },
          { key: '/logs', icon: <HistoryOutlined />, label: '运行日志' },
        ],
      },
      {
        key: 'delivery',
        label: '发布交付',
        children: [
          { key: '/pipeline/releases', icon: <DeploymentUnitOutlined />, label: '版本发布' },
          { key: '/pipeline/alerts', icon: <BellOutlined />, label: '告警规则' },
        ],
      },
    ],
  },
};

const explicitRouteProject: Record<string, ProjectKey> = {
  '/': 'ai-collect',
  '/ai-collect': 'ai-collect',
  '/source-strategy': 'ai-collect',
  '/anti-crawl': 'ai-collect',
  '/field-mapping': 'ai-collect',
  '/lake/catalog': 'data-lake',
  '/lake/metadata': 'data-lake',
  '/lake/quality': 'data-lake',
  '/lake/lineage': 'data-lake',
  '/lake/security': 'data-lake',
  '/lake/market': 'data-lake',
  '/explorer': 'data-lake',
  '/data-api': 'data-lake',
  '/pipeline': 'etl-pipeline',
  '/pipeline/tasks': 'etl-pipeline',
  '/pipeline/templates': 'etl-pipeline',
  '/pipeline/schedule': 'etl-pipeline',
  '/pipeline/releases': 'etl-pipeline',
  '/pipeline/alerts': 'etl-pipeline',
};

const legacyRouteToSidebarKey: Record<string, string> = {
  '/': '/',
  '/instances': '/lake/catalog',
  '/import': '/ai-collect',
  '/tasks': '/ai-collect',
  '/templates': '/ai-collect',
  '/graph-analytics': '/lake/lineage',
  '/explore': '/explorer',
  '/dashboards': '/',
  '/query': '/explorer',
  '/metrics': '/monitor',
  '/project-users': '/project-users',
  '/billing': '/billing',
  '/project-settings': '/project-settings',
  '/learning': '/learning',
};

const resolveProjectByPath = (pathname: string): ProjectKey | null => {
  const match = Object.entries(explicitRouteProject)
    .sort(([a], [b]) => b.length - a.length)
    .find(([route]) => pathname === route || pathname.startsWith(`${route}/`));

  return match?.[1] ?? null;
};

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [pinned, setPinned] = useState(true);
  const [isMobile, setIsMobile] = useState(false);
  const [settledCollapsed, setSettledCollapsed] = useState(false);
  const [projectTextReady, setProjectTextReady] = useState(true);

  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggle } = useThemeStore();
  const isDark = mode === 'dark';
  const [currentProject, setCurrentProject] = useState<ProjectKey>(() => resolveProjectByPath(location.pathname) ?? 'etl-pipeline');
  const routedProject = useMemo(() => resolveProjectByPath(location.pathname), [location.pathname]);
  const activeProjectKey = routedProject ?? currentProject;
  const activeProject = projectConfigs[activeProjectKey];
  const activeAiWorkspacePanel = activeProjectKey === 'ai-collect'
    ? new URLSearchParams(location.search).get('panel')
    : null;
  const hideSidebar = activeProjectKey === 'ai-collect';
  const palette = {
    appBg: isDark ? '#171A22' : '#F6F8FB',
    surface: isDark ? '#22262F' : '#FFFFFF',
    header: isDark ? '#252932' : '#FFFFFF',
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
    if (routedProject && routedProject !== currentProject) {
      setCurrentProject(routedProject);
    }
  }, [currentProject, routedProject]);

  useEffect(() => {
    document.title = `${PRODUCT_NAME_ZH} · ${activeProject.label}`;
  }, [activeProject.label]);

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
    const nextProject = key as ProjectKey;
    setCurrentProject(nextProject);
    const targetPath = projectConfigs[nextProject].defaultPath;
    if (location.pathname !== targetPath) {
      navigate(targetPath);
    }
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
    options?: { accent?: string; outline?: string },
  ) => {
    event.currentTarget.style.background = options?.accent ?? palette.hover;
    if (options?.outline) {
      event.currentTarget.style.boxShadow = `inset 0 0 0 1px ${options.outline}`;
    }
  };

  const resetOverlayHover = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.currentTarget.style.background = 'transparent';
    event.currentTarget.style.boxShadow = 'none';
  };

  const renderProjectPanel = () => (
    <div style={{ ...overlayShellStyle, width: 296 }}>
      <div style={{ padding: '8px 14px 10px' }}>
        <div style={{ color: palette.text, fontSize: 13, fontWeight: 600 }}>项目域切换</div>
        <div style={{ color: palette.secondary, fontSize: 12, marginTop: 3 }}>选择当前工作域，主界面会同步切换到对应产品模块。</div>
      </div>
      {projectOrder.map((key, index) => {
        const project = projectConfigs[key];
        const isActive = key === activeProjectKey;
        return (
          <React.Fragment key={key}>
            {index > 0 ? <div style={{ height: 1, background: palette.border, margin: '4px 14px' }} /> : null}
            <button
              type="button"
              onClick={() => handleProjectChange(key)}
              style={{
                ...overlayRowStyle,
                color: isActive ? palette.text : palette.text,
              }}
              onMouseEnter={(event) => bindOverlayHover(event, {
                accent: isActive ? (isDark ? 'rgba(143, 227, 232, 0.12)' : '#E0F2FE') : palette.hover,
                outline: isActive ? (isDark ? '#8FE3E8' : '#0EA5E9') : undefined,
              })}
              onMouseLeave={resetOverlayHover}
            >
              <span
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: 7,
                  background: `linear-gradient(135deg, ${project.accent}, #1D4ED8)`,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: 12,
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                {project.shortLabel.slice(0, 1)}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 13, fontWeight: isActive ? 600 : 500 }}>{project.label}</span>
                <span style={{ display: 'block', fontSize: 12, color: palette.secondary, marginTop: 2 }}>{project.shortLabel}</span>
              </span>
              {isActive ? (
                <span style={{ color: activeProject.accent, fontSize: 12, fontWeight: 600 }}>当前</span>
              ) : (
                <RightOutlined style={{ color: palette.secondary, fontSize: 12 }} />
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
        width: 320,
      }}
    >
      <div style={{ padding: '8px 14px 10px' }}>
        <div style={{ color: palette.text, fontSize: 13, fontWeight: 600 }}>通知中心</div>
        <div style={{ color: palette.secondary, fontSize: 12, marginTop: 3 }}>3 条未读，按产品运行事件实时聚合。</div>
      </div>
      {[
        { title: '采集任务完成', desc: 'Google Patent 模板已写入 ODS 层', color: '#10B981', icon: <RobotOutlined /> },
        { title: '质量规则告警', desc: 'navwarn.content 缺失率超过阈值', color: '#F59E0B', icon: <AuditOutlined /> },
        { title: 'ETL 调度提示', desc: 'DWD 聚合任务将在 18:30 执行', color: '#0EA5E9', icon: <ScheduleOutlined /> },
      ].map(({ title, desc, color, icon }, index) => (
        <button
          key={title}
          type="button"
          style={{
            ...overlayRowStyle,
            marginTop: index === 0 ? 0 : 4,
            background: 'transparent',
          }}
          onMouseEnter={(event) => bindOverlayHover(event)}
          onMouseLeave={resetOverlayHover}
        >
          <span style={{ width: 26, display: 'inline-flex', justifyContent: 'center', color, fontSize: 15, flexShrink: 0 }}>
            {icon}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', color: palette.text, fontSize: 13, fontWeight: 600 }}>{title}</span>
            <span style={{ display: 'block', color: palette.secondary, fontSize: 12, marginTop: 2 }}>{desc}</span>
          </span>
        </button>
      ))}
    </div>
  );

  const renderAccountPanel = () => {
    const accountRows = [
      { key: 'account', icon: <SettingOutlined />, label: 'Account settings', onClick: undefined },
      { key: 'theme', icon: <SkinOutlined />, label: 'Theme', onClick: toggle },
      { key: 'legal', icon: <SafetyCertificateOutlined />, label: 'Legal', onClick: undefined },
      { key: 'logout', icon: <LogoutOutlined />, label: 'Log out', onClick: undefined },
    ];

    return (
      <div
        style={{
          ...overlayShellStyle,
          width: 276,
        }}
      >
        <div style={{ padding: '8px 14px 9px' }}>
          <div style={{ color: palette.text, fontSize: 13, fontWeight: 600 }}>Blank George</div>
          <div style={{ color: palette.secondary, fontSize: 12, marginTop: 3 }}>zhouy674896488@gmail.com</div>
        </div>
        {accountRows.map((item, index) => (
          <React.Fragment key={item.key}>
            {index === 1 || index === 3 ? <div style={{ height: 1, background: palette.border, margin: '6px 14px' }} /> : null}
            <button
              type="button"
              onClick={item.onClick}
              style={{
                ...overlayRowStyle,
                minHeight: 36,
                padding: '0 8px',
                gap: 8,
                color: item.key === 'logout' ? palette.text : overlayRowStyle.color,
              }}
              onMouseEnter={(event) => {
                bindOverlayHover(event, item.key === 'account' ? {
                  accent: isDark ? 'rgba(143, 227, 232, 0.12)' : '#E0F2FE',
                  outline: isDark ? '#8FE3E8' : '#0EA5E9',
                } : undefined);
              }}
              onMouseLeave={resetOverlayHover}
            >
              <span style={{ width: 26, display: 'inline-flex', justifyContent: 'center', color: palette.secondary, fontSize: 15 }}>
                {item.icon}
              </span>
              <span style={{ flex: 1 }}>{item.label}</span>
              {item.key === 'theme' || item.key === 'legal' ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', color: palette.secondary, fontSize: 12 }}>
                  <RightOutlined style={{ fontSize: 12 }} />
                </span>
              ) : null}
            </button>
          </React.Fragment>
        ))}
      </div>
    );
  };

  return (
    <div style={{ minHeight: '100vh', background: palette.appBg }}>
      <Header
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          height: HEADER_H,
          padding: '0 18px',
          background: palette.header,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          lineHeight: `${HEADER_H}px`,
          borderBottom: `1px solid ${palette.border}`,
          zIndex: 101,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
          {isMobile && !hideSidebar && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setManualCollapsed(false)}
              style={{ width: 34, height: 34, color: palette.secondary }}
            />
          )}
          <button
            type="button"
            style={{
              display: 'flex',
              alignItems: 'center',
              flexShrink: 0,
              cursor: 'pointer',
              border: 'none',
              padding: 0,
              background: 'transparent',
            }}
            onClick={() => navigate('/')}
          >
            <img
              src={isDark ? '/astral-helio-logo-white.svg' : '/astral-helio-logo.svg'}
              alt="Astral Helio"
              style={{ height: 28, width: 'auto' }}
            />
          </button>

          {!isMobile && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <Dropdown
                trigger={['click']}
                menu={{ items: [] }}
                popupRender={renderProjectPanel}
              >
                <button
                  type="button"
                  style={{
                    border: 'none',
                    background: 'transparent',
                    color: palette.text,
                    fontWeight: 600,
                    cursor: 'pointer',
                    padding: '3px 6px',
                    borderRadius: 6,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    maxWidth: 260,
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {activeProject.label}
                  </span>
                  <DownOutlined style={{ color: palette.secondary, fontSize: 10 }} />
                </button>
              </Dropdown>
            </div>
          )}
        </div>

        <div style={{ height: '100%', display: 'flex', alignItems: 'center', gap: 4 }}>
          {activeProjectKey === 'ai-collect' ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', gap: 2, marginRight: 6, paddingRight: 8, borderRight: `1px solid ${palette.border}` }}>
              {([
                { panel: 'templates', label: '模板管理', icon: <TemplateNavIcon /> },
                { panel: 'tasks', label: '任务调度', icon: <TaskNavIcon /> },
              ] as const).map((item) => {
                const isActive = activeAiWorkspacePanel === item.panel;
                return (
                  <Button
                    key={item.panel}
                    type="text"
                    icon={item.icon}
                    aria-label={item.label}
                    title={item.label}
                    onClick={() => navigate(isActive ? '/ai-collect' : `/ai-collect?panel=${item.panel}`)}
                    style={{
                      width: 34,
                      height: 34,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: isActive ? palette.text : palette.secondary,
                      border: 'none',
                      fontSize: 15,
                    }}
                  />
                );
              })}
            </div>
          ) : null}
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            menu={{ items: [] }}
            popupRender={renderNotificationPanel}
          >
            <Button
              type="text"
              aria-label="打开通知中心"
              style={{
                width: 34,
                height: 34,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: palette.secondary,
                border: 'none',
                fontSize: 15,
              }}
            >
              <Badge dot offset={[-3, 4]}>
                <BellOutlined />
              </Badge>
            </Button>
          </Dropdown>
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            menu={{ items: [] }}
            popupRender={renderAccountPanel}
          >
            <Button
              type="text"
              aria-label="打开账户菜单"
              style={{
                height: 34,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '0 6px',
                border: 'none',
              }}
            >
              <div
                style={{
                  width: 25,
                  height: 25,
                  borderRadius: 6,
                  background: 'linear-gradient(135deg, #018BFF, #0060CC)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                BG
              </div>
            </Button>
          </Dropdown>
        </div>
      </Header>

      <div style={{ display: 'flex', paddingTop: HEADER_H }}>
        {isMobile && !collapsed && !hideSidebar && (
          <button
            type="button"
            aria-label="关闭侧边栏遮罩"
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
            <Dropdown
              trigger={['click']}
              menu={{ items: [] }}
              popupRender={renderProjectPanel}
            >
              <button
                type="button"
                style={{
                  width: '100%',
                  height: 48,
                  border: 'none',
                  borderRadius: 8,
                  background: collapsed && settledCollapsed ? 'transparent' : isDark ? 'rgba(255, 255, 255, 0.04)' : '#F8FAFC',
                  color: palette.text,
                  display: 'block',
                  padding: 0,
                  cursor: 'pointer',
                  textAlign: 'left',
                  overflow: 'hidden',
                  position: 'relative',
                  transition: 'background 0.15s ease',
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
                        {activeProject.label}
                      </span>
                    </span>
                    <DownOutlined
                      style={{
                        position: 'absolute',
                        right: 10,
                        top: 19,
                        color: palette.secondary,
                        fontSize: 10,
                        opacity: projectTextVisible ? 1 : 0,
                        transition: 'opacity 0.12s ease',
                      }}
                    />
                  </>
                )}
              </button>
            </Dropdown>
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
                  <span>今日链路健康度</span>
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
                  aria-label={pinned ? '取消固定侧边栏' : '固定侧边栏'}
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
            minHeight: `calc(100vh - ${HEADER_H}px)`,
            background: palette.appBg,
          }}
        >
          <Content style={{ padding: isMobile ? 16 : 24, minHeight: 280 }}>
            <div className={isDark ? '' : 'light-mode'}>
              {children}
            </div>
          </Content>
        </main>
      </div>
    </div>
  );
};

export default MainLayout;
