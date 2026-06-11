import React, { useCallback, useEffect, useMemo, useState } from 'react';
import type { MenuProps } from 'antd';
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
  CloudServerOutlined,
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
  ProfileOutlined,
  PushpinOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SearchOutlined,
  SettingOutlined,
  SkinOutlined,
  TeamOutlined,
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
  description: string;
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
    description: '从目标识别、模板生成到采集运行的智能入口',
    defaultPath: '/ai-collect',
    accent: '#7C3AED',
    sections: [
      {
        key: 'capture',
        label: '采集编排',
        children: [
          { key: '/ai-collect', icon: <RobotOutlined />, label: '智能采集' },
          { key: '/templates', icon: <ProfileOutlined />, label: '模板库' },
          { key: '/tasks', icon: <ScheduleOutlined />, label: '采集任务', badge: 8 },
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
    description: '沉淀原始数据、湖仓分层、质量治理与服务输出',
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
    description: '开发、调度、监控和发布端到端数据管道',
    defaultPath: '/pipeline',
    accent: '#0EA5E9',
    sections: [
      {
        key: 'development',
        label: '管道开发',
        children: [
          { key: '/pipeline', icon: <ApartmentOutlined />, label: '管道画布' },
          { key: '/tasks', icon: <ScheduleOutlined />, label: '任务中心', badge: 12 },
          { key: '/templates', icon: <CodeOutlined />, label: '处理器模板' },
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
  '/pipeline/schedule': 'etl-pipeline',
  '/pipeline/releases': 'etl-pipeline',
  '/pipeline/alerts': 'etl-pipeline',
};

const legacyRouteToSidebarKey: Record<string, string> = {
  '/': '/',
  '/instances': '/lake/catalog',
  '/import': '/ai-collect',
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

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [pinned, setPinned] = useState(true);
  const [isMobile, setIsMobile] = useState(false);
  const [currentProject, setCurrentProject] = useState<ProjectKey>('etl-pipeline');
  const [settledCollapsed, setSettledCollapsed] = useState(false);
  const [projectTextReady, setProjectTextReady] = useState(true);

  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggle } = useThemeStore();
  const isDark = mode === 'dark';

  const activeProject = projectConfigs[currentProject];
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

  const projectMenuItems: MenuProps['items'] = projectOrder.map((key) => ({
    key,
    label: (
      <div style={{ minWidth: 220 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: projectConfigs[key].accent,
              flexShrink: 0,
            }}
          />
          <span style={{ fontWeight: 600 }}>{projectConfigs[key].label}</span>
        </div>
        <div style={{ marginTop: 4, fontSize: 12, color: palette.secondary }}>
          {projectConfigs[key].description}
        </div>
      </div>
    ),
  }));

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
    const match = Object.entries(explicitRouteProject)
      .sort(([a], [b]) => b.length - a.length)
      .find(([route]) => location.pathname === route || location.pathname.startsWith(`${route}/`));

    if (match && match[1] !== currentProject) {
      setCurrentProject(match[1]);
    }
  }, [currentProject, location.pathname]);

  useEffect(() => {
    document.title = `${PRODUCT_NAME_ZH} · ${activeProject.label}`;
  }, [activeProject.label]);

  const collapsed = isMobile ? manualCollapsed : !pinned && manualCollapsed;
  const projectTextVisible = !collapsed && projectTextReady;
  const projectLogoX = collapsed ? PROJECT_LOGO_COLLAPSED_X : PROJECT_LOGO_EXPANDED_X;
  const siderWidth = isMobile ? (collapsed ? 0 : SIDER_EXPANDED) : collapsed ? SIDER_COLLAPSED : SIDER_EXPANDED;
  const contentMarginLeft = isMobile ? 0 : siderWidth;
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

  const renderNotificationPanel = () => (
    <div
      style={{
        width: 320,
        padding: 12,
        borderRadius: 8,
        background: palette.surface,
        border: `1px solid ${palette.border}`,
        boxShadow: isDark ? '0 16px 38px rgba(0, 0, 0, 0.42)' : '0 16px 34px rgba(15, 23, 42, 0.12)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
        <strong style={{ color: palette.text }}>通知中心</strong>
        <span style={{ color: palette.muted, fontSize: 12 }}>3 条未读</span>
      </div>
      {[
        ['采集任务完成', 'Google Patent 模板已写入 ODS 层', '#10B981'],
        ['质量规则告警', 'navwarn.content 缺失率超过阈值', '#F59E0B'],
        ['ETL 调度提示', 'DWD 聚合任务将在 18:30 执行', '#0EA5E9'],
      ].map(([title, desc, color]) => (
        <div
          key={title}
          style={{
            display: 'grid',
            gridTemplateColumns: '8px 1fr',
            gap: 10,
            padding: '10px 6px',
            borderTop: `1px solid ${palette.borderSoft}`,
          }}
        >
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, marginTop: 6 }} />
          <span>
            <span style={{ display: 'block', color: palette.text, fontSize: 13, fontWeight: 600 }}>{title}</span>
            <span style={{ display: 'block', color: palette.secondary, fontSize: 12, marginTop: 2 }}>{desc}</span>
          </span>
        </div>
      ))}
    </div>
  );

  const renderAccountPanel = () => {
    const rowStyle: React.CSSProperties = {
      width: '100%',
      height: 46,
      border: 'none',
      background: 'transparent',
      color: palette.text,
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '0 10px',
      borderRadius: 6,
      cursor: 'pointer',
      fontSize: 15,
      textAlign: 'left',
    };

    const accountRows = [
      { key: 'account', icon: <SettingOutlined />, label: 'Account settings', onClick: undefined },
      { key: 'theme', icon: <SkinOutlined />, label: 'Theme', onClick: toggle },
      { key: 'legal', icon: <SafetyCertificateOutlined />, label: 'Legal', onClick: undefined },
      { key: 'logout', icon: <LogoutOutlined />, label: 'Log out', onClick: undefined },
    ];

    return (
      <div
        style={{
          width: 306,
          padding: 8,
          borderRadius: 8,
          background: isDark ? '#202326' : '#FFFFFF',
          border: `1px solid ${isDark ? 'rgba(255, 255, 255, 0.12)' : '#E2E8F0'}`,
          boxShadow: isDark ? '0 18px 44px rgba(0, 0, 0, 0.45)' : '0 18px 38px rgba(15, 23, 42, 0.14)',
        }}
      >
        <div style={{ padding: '14px 4px 12px 4px' }}>
          <div style={{ color: palette.text, fontSize: 16, fontWeight: 700 }}>Blank George</div>
          <div style={{ color: palette.secondary, fontSize: 14, marginTop: 4 }}>zhouy674896488@gmail.com</div>
        </div>
        {accountRows.map((item, index) => (
          <React.Fragment key={item.key}>
            {index === 1 || index === 3 ? <div style={{ height: 1, background: palette.border, margin: '4px 0' }} /> : null}
            <button
              type="button"
              onClick={item.onClick}
              style={{
                ...rowStyle,
                color: item.key === 'logout' ? palette.text : rowStyle.color,
              }}
              onMouseEnter={(event) => {
                event.currentTarget.style.background = item.key === 'account'
                  ? isDark ? 'rgba(143, 227, 232, 0.12)' : '#E0F2FE'
                  : palette.hover;
                if (item.key === 'account') {
                  event.currentTarget.style.boxShadow = `inset 0 0 0 1px ${isDark ? '#8FE3E8' : '#0EA5E9'}`;
                }
              }}
              onMouseLeave={(event) => {
                event.currentTarget.style.background = 'transparent';
                event.currentTarget.style.boxShadow = 'none';
              }}
            >
              <span style={{ width: 22, display: 'inline-flex', justifyContent: 'center', color: palette.secondary }}>
                {item.icon}
              </span>
              <span style={{ flex: 1, fontWeight: 600 }}>{item.label}</span>
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
          {isMobile && (
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
              <span style={{ fontSize: 13, color: palette.secondary, padding: '2px 4px' }}>
                {PRODUCT_NAME_ZH}
              </span>
              <span style={{ color: palette.muted, fontSize: 12 }}>/</span>
              <Dropdown
                menu={{
                  items: projectMenuItems,
                  selectable: true,
                  selectedKeys: [currentProject],
                  onClick: ({ key }) => handleProjectChange(key),
                }}
                trigger={['click']}
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
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: activeProject.accent,
                      boxShadow: `0 0 0 3px ${activeProject.accent}22`,
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {activeProject.label}
                  </span>
                  <DownOutlined style={{ color: palette.secondary, fontSize: 10 }} />
                </button>
              </Dropdown>
            </div>
          )}
        </div>

        <Space size={4} align="center">
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
        </Space>
      </Header>

      <div style={{ display: 'flex', paddingTop: HEADER_H }}>
        {isMobile && !collapsed && (
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
              menu={{
                items: projectMenuItems,
                selectable: true,
                selectedKeys: [currentProject],
                onClick: ({ key }) => handleProjectChange(key),
              }}
              trigger={['click']}
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
                        top: 8,
                        opacity: projectTextVisible ? 1 : 0,
                        transform: projectTextVisible ? 'translateX(0)' : 'translateX(-6px)',
                        transition: 'opacity 0.12s ease, transform 0.12s ease',
                        pointerEvents: projectTextVisible ? 'auto' : 'none',
                      }}
                    >
                      <span style={{ display: 'block', fontSize: 13, fontWeight: 700 }}>
                        {activeProject.label}
                      </span>
                      <span
                        style={{
                          display: 'block',
                          marginTop: 2,
                          color: palette.secondary,
                          fontSize: 12,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {activeProject.description}
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
