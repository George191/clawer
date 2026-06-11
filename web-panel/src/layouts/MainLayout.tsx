import React, { useState, useEffect, useCallback } from 'react';
import {
  Layout,
  Button,
  Space,
  Tooltip,
  Dropdown,
  Badge,
} from 'antd';
import {
  DatabaseOutlined,
  ImportOutlined,
  BarChartOutlined,
  ApiOutlined,
  SearchOutlined,
  DashboardOutlined,
  CodeOutlined,
  LineChartOutlined,
  FileTextOutlined,
  TeamOutlined,
  DollarOutlined,
  SettingOutlined,
  BookOutlined,
  SunOutlined,
  MoonOutlined,
  BellOutlined,
  LogoutOutlined,
  PushpinOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useThemeStore } from '@/stores/settings';

const { Header, Content } = Layout;

// ── 常量 ──
const HEADER_H = 48;
const SIDER_EXPANDED = 250;
const SIDER_COLLAPSED = 52;

// ── Neo4j Aura Console 侧边栏结构 ──

interface SidebarSection {
  key: string;
  label: string;
  children: SidebarItem[];
}

interface SidebarItem {
  key: string;
  icon: React.ReactNode;
  label: string;
  labelZh: string;
  badge?: number;
  disabled?: boolean;
}

const sidebarSections: SidebarSection[] = [
  {
    key: 'data-services',
    label: 'Data Services',
    children: [
      { key: '/instances', icon: <DatabaseOutlined />, label: 'Instances', labelZh: '实例' },
      { key: '/import', icon: <ImportOutlined />, label: 'Import', labelZh: '导入' },
      { key: '/graph-analytics', icon: <BarChartOutlined />, label: 'Graph Analytics', labelZh: '图分析' },
      { key: '/data-api', icon: <ApiOutlined />, label: 'Data APIs', labelZh: '数据API' },
    ],
  },
  {
    key: 'tools',
    label: 'Tools',
    children: [
      { key: '/explore', icon: <SearchOutlined />, label: 'Explore', labelZh: '探索' },
      { key: '/dashboards', icon: <DashboardOutlined />, label: 'Dashboards', labelZh: '仪表盘' },
      { key: '/query', icon: <CodeOutlined />, label: 'Query', labelZh: '查询' },
    ],
  },
  {
    key: 'operations',
    label: 'Operations',
    children: [
      { key: '/metrics', icon: <LineChartOutlined />, label: 'Metrics', labelZh: '指标' },
      { key: '/logs', icon: <FileTextOutlined />, label: 'Logs', labelZh: '日志' },
    ],
  },
  {
    key: 'project',
    label: 'Project',
    children: [
      { key: '/project-users', icon: <TeamOutlined />, label: 'Users', labelZh: '用户管理' },
      { key: '/billing', icon: <DollarOutlined />, label: 'Billing', labelZh: '计费' },
      { key: '/project-settings', icon: <SettingOutlined />, label: 'Settings', labelZh: '设置' },
    ],
  },
  {
    key: 'learning',
    label: 'Learning',
    children: [
      { key: '/learning', icon: <BookOutlined />, label: 'Guides & Resources', labelZh: '指南与资源' },
    ],
  },
];

// ── 路由到侧边栏 key 的映射 ──
const routeToSidebarKey: Record<string, string> = {
  '/': '/',
  '/explorer': '/explore',
  '/tasks': '/tasks',
  '/monitor': '/metrics',
  '/pipeline': '/pipeline',
  '/templates': '/templates',
  '/ai-collect': '/ai-collect',
  '/data-api': '/data-api',
};

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [pinned, setPinned] = useState(true);
  const [isMobile, setIsMobile] = useState(false);
  const [currentProject, setCurrentProject] = useState('etl');

  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggle } = useThemeStore();

  // ── 项目选项 ──
  const projectOptions = [
    { key: 'etl', label: 'ETL Pipeline' },
    { key: 'data-lake', label: 'Data Lake' },
    { key: 'ai-collect', label: 'AI Collect' },
  ];

  // ── 响应式检测 ──
  const handleResize = useCallback(() => {
    const w = window.innerWidth;
    setIsMobile(w < 768);
    if (w < 768) setManualCollapsed(true);
  }, []);

  useEffect(() => {
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [handleResize]);

  // 收起规则: 不pinned + 鼠标不在侧边栏上 → 收起
  const shouldCollapse = !pinned;
  const collapsed = isMobile || (shouldCollapse && manualCollapsed);
  const siderWidth = isMobile ? 0 : collapsed ? SIDER_COLLAPSED : SIDER_EXPANDED;
  const contentMarginLeft = isMobile ? 0 : collapsed ? SIDER_COLLAPSED : SIDER_EXPANDED;

  // ── 当前选中菜单 ──
  const pathSnippets = location.pathname.split('/').filter(Boolean);
  const selectedKey = routeToSidebarKey[location.pathname] || '/' + (pathSnippets[0] || '');

  // ── 用户下拉菜单 ──
  const userMenuItems = [
    {
      key: 'org',
      label: 'Organization Settings',
      icon: <SettingOutlined />,
    },
    { type: 'divider' as const },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: 'Sign Out',
      danger: true,
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: mode === 'dark' ? '#1A1D27' : '#F8FAFC' }}>
      {/* ── 顶栏 (全宽，固定顶部) ── */}
      <Header
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          height: HEADER_H,
          padding: '0 20px',
          background: mode === 'dark' ? '#3C3F44' : '#F8FAFC',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          lineHeight: `${HEADER_H}px`,
          borderBottom: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.06)' : '1px solid #E2E8F0',
          zIndex: 101,
        }}
      >
        {/* 左侧: Logo + 组织/项目选择器 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Logo */}
          <div
            style={{ display: 'flex', alignItems: 'center', flexShrink: 0, cursor: 'pointer' }}
            onClick={() => navigate('/')}
          >
            <img
              src={mode === 'dark' ? '/astral-helio-logo-white.svg' : '/astral-helio-logo.svg'}
              alt="Astral Helio"
              style={{ height: 28, width: 'auto' }}
            />
          </div>

          {/* 组织/项目 选择器 */}
          {!isMobile && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span
                style={{
                  fontSize: 13,
                  color: mode === 'dark' ? '#B0B5BE' : '#64748B',
                  cursor: 'pointer',
                  padding: '2px 6px',
                  borderRadius: 4,
                  transition: 'color 0.15s',
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = mode === 'dark' ? '#E4E7EB' : '#334155'; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = mode === 'dark' ? '#B0B5BE' : '#64748B'; }}
              >
                Spider Organization
              </span>
              <span style={{ color: mode === 'dark' ? '#6B7280' : '#94A3B8', fontSize: 12 }}>/</span>
              <Dropdown
                menu={{
                  items: projectOptions.map((p) => ({
                    key: p.key,
                    label: p.label,
                  })),
                  onClick: ({ key }) => setCurrentProject(key),
                }}
                trigger={['click']}
              >
                <span
                  style={{
                    fontSize: 13,
                    color: mode === 'dark' ? '#E4E7EB' : '#334155',
                    fontWeight: 500,
                    cursor: 'pointer',
                    padding: '2px 6px',
                    borderRadius: 4,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                  }}
                >
                  {projectOptions.find((p) => p.key === currentProject)?.label}
                  <span style={{ fontSize: 10, color: mode === 'dark' ? '#9CA3AF' : '#64748B' }}>&#9660;</span>
                </span>
              </Dropdown>
            </div>
          )}
        </div>

        {/* 右侧: 操作区 */}
        <Space size={4} align="center">
          <Tooltip title={mode === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}>
            <Button
              type="text"
              icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggle}
              style={{ width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', color: mode === 'dark' ? '#B0B5BE' : '#64748B', border: 'none', fontSize: 15 }}
            />
          </Tooltip>
          <Tooltip title="通知">
            <Badge dot offset={[-2, 2]}>
              <Button type="text" icon={<BellOutlined />} style={{ width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', color: mode === 'dark' ? '#B0B5BE' : '#64748B', border: 'none', fontSize: 15 }} />
            </Badge>
          </Tooltip>
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Button type="text" style={{ height: 32, display: 'flex', alignItems: 'center', gap: 6, padding: '0 6px', border: 'none' }}>
              <div style={{ width: 24, height: 24, borderRadius: 6, background: 'linear-gradient(135deg, #018BFF, #0060CC)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 11, fontWeight: 600 }}>
                A
              </div>
            </Button>
          </Dropdown>
        </Space>
      </Header>

      {/* ── 下方区域：侧边栏 + 内容 ── */}
      <div style={{ display: 'flex', paddingTop: HEADER_H }}>
        {/* ── 侧边栏 ── */}
        <div
          style={{
            position: 'fixed',
            left: 0,
            top: HEADER_H,
            bottom: 0,
            width: siderWidth,
            background: mode === 'dark' ? '#3C3F44' : '#F8FAFC',
            borderRight: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.06)' : '1px solid #E2E8F0',
            zIndex: 100,
            overflow: 'hidden',
            transition: 'width 0.2s ease',
            display: isMobile && collapsed ? 'none' : 'flex',
            flexDirection: 'column',
          }}
          onMouseEnter={() => { if (!pinned) setManualCollapsed(false); }}
          onMouseLeave={() => { if (!pinned) setManualCollapsed(true); }}
        >
          {/* Organization 切换 */}
          <div
            style={{
              padding: '12px 16px',
              borderBottom: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.06)' : '1px solid #E2E8F0',
              flexShrink: 0,
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: mode === 'dark' ? '#9CA3AF' : '#64748B', marginBottom: 6, visibility: collapsed ? 'hidden' : 'visible' }}>
              Organization
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 10px',
                background: collapsed ? 'transparent' : mode === 'dark' ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)',
                borderRadius: 6,
                cursor: 'pointer',
                transition: 'background 0.15s ease',
                height: 32,
              }}
              onMouseEnter={(e) => {
                if (!collapsed) (e.currentTarget as HTMLElement).style.background = mode === 'dark' ? 'rgba(255, 255, 255, 0.07)' : 'rgba(0, 0, 0, 0.07)';
              }}
              onMouseLeave={(e) => {
                if (!collapsed) (e.currentTarget as HTMLElement).style.background = mode === 'dark' ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)';
              }}
            >
              <div style={{ width: 20, height: 20, borderRadius: 4, background: 'linear-gradient(135deg, #018BFF, #0060CC)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#fff', fontWeight: 700, flexShrink: 0, transform: collapsed ? 'translateX(-10px)' : 'translateX(0)', transition: 'transform 0.2s ease' }}>
                S
              </div>
              {!collapsed && (
                <>
                  <span style={{ fontSize: 12, color: mode === 'dark' ? '#E4E7EB' : '#334155', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, marginLeft: 8 }}>
                    Spider Organization
                  </span>
                  <span style={{ color: mode === 'dark' ? '#9CA3AF' : '#64748B', fontSize: 10, flexShrink: 0 }}>&#9660;</span>
                </>
              )}
            </div>
          </div>

          {/* 导航区域 */}
          <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '8px 0' }} className="neo4j-sidebar-scroll">
            <div style={{ minWidth: SIDER_EXPANDED }}>
              {sidebarSections.map((section) => (
                <div key={section.key} style={{ marginBottom: 4 }}>
                  {!collapsed && (
                    <div
                      style={{
                        height: 30,
                        display: 'flex',
                        alignItems: 'center',
                        padding: '0 16px',
                        color: mode === 'dark' ? '#9CA3AF' : '#64748B',
                        fontSize: 10,
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        letterSpacing: '0.08em',
                        userSelect: 'none',
                      }}
                    >
                      {section.label}
                    </div>
                  )}
                  {collapsed && (
                    <div
                      style={{
                        height: 30,
                        display: 'flex',
                        alignItems: 'center',
                        padding: '0 16px',
                        visibility: 'hidden',
                      }}
                    >
                      {section.label}
                    </div>
                  )}
                  {section.children.map((item) => {
                    const isActive = selectedKey === item.key;
                    const isDisabled = item.disabled;
                    const darkInactiveColor = '#B0B5BE';
                    const lightInactiveColor = '#64748B';
                    const inactiveColor = mode === 'dark' ? darkInactiveColor : lightInactiveColor;
                    const darkHoverColor = '#E4E7EB';
                    const lightHoverColor = '#334155';
                    const hoverColor = mode === 'dark' ? darkHoverColor : lightHoverColor;
                    const darkHoverBg = 'rgba(255, 255, 255, 0.04)';
                    const lightHoverBg = 'rgba(0, 0, 0, 0.04)';
                    const hoverBg = mode === 'dark' ? darkHoverBg : lightHoverBg;
                    const selectedColor = mode === 'dark' ? '#8FE3E8' : '#0A6190';
                    const selectedBg = mode === 'dark' ? 'rgba(143, 227, 232, 0.12)' : 'rgba(10, 97, 144, 0.12)';

                    return (
                      <div
                        key={item.key}
                        onClick={() => !isDisabled && navigate(item.key)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          height: 36,
                          margin: '1px 0',
                          cursor: isDisabled ? 'not-allowed' : 'pointer',
                          opacity: isDisabled ? 0.4 : 1,
                          color: isActive ? selectedColor : inactiveColor,
                          background: isActive ? selectedBg : 'transparent',
                          transition: 'all 0.15s ease',
                          fontSize: 13,
                          fontWeight: isActive ? 500 : 400,
                          position: 'relative',
                          borderRadius: '0 6px 6px 0',
                          marginRight: 12,
                          paddingLeft: 16,
                        }}
                        onMouseEnter={(e) => {
                          if (!isDisabled && !isActive) {
                            (e.currentTarget as HTMLElement).style.background = hoverBg;
                            (e.currentTarget as HTMLElement).style.color = hoverColor;
                          }
                        }}
                        onMouseLeave={(e) => {
                          if (!isDisabled && !isActive) {
                            (e.currentTarget as HTMLElement).style.background = 'transparent';
                            (e.currentTarget as HTMLElement).style.color = inactiveColor;
                          }
                        }}
                      >
                        {/* 左侧指示条 */}
                        <div
                          style={{
                            position: 'absolute',
                            left: 0,
                            top: 6,
                            bottom: 6,
                            width: 3,
                            borderRadius: '0 2px 2px 0',
                            background: isActive ? selectedColor : 'transparent',
                            transition: 'background 0.15s ease',
                          }}
                        />
                        <span
                          style={{
                            marginRight: 10,
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
                        <span style={{ flex: 1, lineHeight: '36px', whiteSpace: 'nowrap', visibility: collapsed ? 'hidden' : 'visible' }}>
                          {item.label}
                        </span>
                        {item.badge !== undefined && (
                          <span style={{ fontSize: 11, color: inactiveColor, marginRight: 8 }}>{item.badge}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {/* 置顶按钮 - 侧边栏底部 */}
          {!isMobile && (
            <div
              style={{
                flexShrink: 0,
                borderTop: mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.06)' : '1px solid #E2E8F0',
                padding: '8px 0',
                display: 'flex',
                justifyContent: collapsed ? 'center' : 'flex-end',
                paddingRight: collapsed ? 0 : 12,
              }}
            >
              <Tooltip title={pinned ? '取消置顶' : '置顶侧边栏'} placement="right">
                <Button
                  type="text"
                  icon={<PushpinOutlined />}
                  onClick={() => setPinned(!pinned)}
                  style={{
                    width: 32,
                    height: 32,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: pinned ? (mode === 'dark' ? '#8FE3E8' : '#0A6190') : (mode === 'dark' ? '#B0B5BE' : '#64748B'),
                    border: 'none',
                    fontSize: 14,
                  }}
                />
              </Tooltip>
            </div>
          )}
        </div>

        {/* ── 内容区 ── */}
        <div
          style={{
            marginLeft: contentMarginLeft,
            transition: 'margin-left 0.2s ease',
            width: '100%',
            minHeight: `calc(100vh - ${HEADER_H}px)`,
            background: mode === 'dark' ? '#1A1D27' : '#F8FAFC',
          }}
        >
          <Content style={{ padding: 24, minHeight: 280 }}>
            <div className={mode === 'light' ? 'light-mode' : ''}>
              {children}
            </div>
          </Content>
        </div>
      </div>
    </div>
  );
};

export default MainLayout;