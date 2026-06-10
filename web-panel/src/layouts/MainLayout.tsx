import React, { useState, useEffect, useCallback } from 'react';
import {
  Layout,
  Menu,
  Button,
  Badge,
  Avatar,
  Dropdown,
  Breadcrumb,
  Drawer,
  Space,
  Tooltip,
  Select,
} from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  ScheduleOutlined,
  MonitorOutlined,
  ApartmentOutlined,
  FileProtectOutlined,
  ThunderboltOutlined,
  MenuUnfoldOutlined,
  SunOutlined,
  MoonOutlined,
  DesktopOutlined,
  BellOutlined,
  UserOutlined,
  SettingOutlined,
  LogoutOutlined,
  CheckCircleOutlined,
  HomeOutlined,
  AimOutlined,
  DownOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTheme, type ThemeMode } from '@/hooks/useTheme';
import type { MenuProps } from 'antd';

const { Header, Sider, Content, Footer } = Layout;

// ── 布局常量 ──
const HEADER_HEIGHT = 56;
const SIDER_WIDTH = 232;
const SIDER_COLLAPSED_WIDTH = 68;

interface MainLayoutProps {
  children: React.ReactNode;
}

// ── Bloom 风格分组菜单 ──
// 特点：粗体组标题，组间无分割线，仅靠间距区分
const menuGroups = [
  {
    label: '概览',
    items: [
      { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
      { key: '/explorer', icon: <DatabaseOutlined />, label: '数据探索' },
    ],
  },
  {
    label: '数据管理',
    items: [
      { key: '/tasks', icon: <ScheduleOutlined />, label: '任务中心' },
      { key: '/monitor', icon: <MonitorOutlined />, label: '采集监控' },
    ],
  },
  {
    label: '管道与模板',
    items: [
      { key: '/pipeline', icon: <ApartmentOutlined />, label: '管道管理' },
      { key: '/templates', icon: <FileProtectOutlined />, label: '模板管理' },
    ],
  },
  {
    label: 'AI 工具',
    items: [
      { key: '/ai-collect', icon: <ThunderboltOutlined />, label: 'AI 采集' },
    ],
  },
];

function buildMenuItems(): MenuProps['items'] {
  return menuGroups.map((group) => ({
    type: 'group' as const,
    label: group.label,
    key: `_group-${group.label}`,
    children: group.items.map((item) => ({
      key: item.key,
      icon: item.icon,
      label: item.label,
    })),
  }));
}

const menuAllItems = buildMenuItems();

const breadcrumbNameMap: Record<string, string> = {
  '/': '仪表盘',
  '/explorer': '数据探索',
  '/tasks': '任务中心',
  '/monitor': '采集监控',
  '/pipeline': '管道管理',
  '/templates': '模板管理',
  '/ai-collect': 'AI 智能采集',
};

// ── 模拟组织/项目数据 ──
const organizations = [
  { value: 'data-team', label: '数据平台部' },
  { value: 'ai-lab', label: 'AI 创新实验室' },
];
const projects = [
  { value: 'main', label: '核心采集平台' },
  { value: 'crawler-v2', label: '爬虫引擎 v2' },
  { value: 'etl-pipeline', label: 'ETL 管道项目' },
];

function getThemeIcon(mode: ThemeMode) {
  switch (mode) {
    case 'dark': return <MoonOutlined style={{ fontSize: 14 }} />;
    case 'light': return <SunOutlined style={{ fontSize: 14 }} />;
    case 'system': return <DesktopOutlined style={{ fontSize: 14 }} />;
  }
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isTablet, setIsTablet] = useState(false);

  const location = useLocation();
  const navigate = useNavigate();
  const { mode, cycle } = useTheme();

  // ── 响应式 ──
  const handleResize = useCallback(() => {
    const w = window.innerWidth;
    setIsMobile(w < 768);
    setIsTablet(w >= 768 && w < 1200);
  }, []);
  useEffect(() => {
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [handleResize]);

  // ── 选中菜单 ──
  const pathFirst = '/' + (location.pathname.split('/')[1] || '');
  const allItemKeys = menuGroups.flatMap(g => g.items.map(i => i.key));
  const selectedKey = allItemKeys.includes(pathFirst) ? pathFirst : '/';

  // ── 面包屑 ──
  const pathSnippets = location.pathname.split('/').filter(Boolean);
  const breadcrumbItems = [
    { title: <Space size={4}><HomeOutlined /><span>首页</span></Space>, path: '/' },
    ...pathSnippets.map((_, i) => {
      const path = '/' + pathSnippets.slice(0, i + 1).join('/');
      return { title: breadcrumbNameMap[path] || pathSnippets[i], path };
    }),
  ];

  // ── 用户下拉 ──
  const userMenuItems: MenuProps['items'] = [
    { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  // ── 折叠 ──
  const siderCollapsed = isTablet || manualCollapsed;
  const showSiderText = !isMobile && !siderCollapsed;
  const siderBg = '#0a0d14';

  // ── 菜单组件 ──
  const renderMenu = (inlineCollapsed: boolean) => (
    <div className="bloom-menu-wrapper">
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[selectedKey]}
        inlineCollapsed={inlineCollapsed}
        items={menuAllItems}
        onClick={({ key }) => {
          if (!key.startsWith('_')) {
            navigate(key);
            setMobileDrawerOpen(false);
          }
        }}
        style={{ borderInlineEnd: 'none', background: 'transparent' }}
      />
    </div>
  );

  // ── 折叠按钮 ──
  const collapseButton = !isMobile ? (
    <Button
      type="text"
      icon={<MenuUnfoldOutlined style={{ transition: 'transform 0.3s cubic-bezier(0.42,0,0.58,1)' }} />}
      onClick={() => setManualCollapsed((v) => !v)}
      style={{ fontSize: 15, width: 32, height: 32, color: 'var(--theme-color-neutral-text-weaker)' }}
    />
  ) : (
    <Button
      type="text"
      icon={<MenuUnfoldOutlined />}
      onClick={() => setMobileDrawerOpen(true)}
      style={{ fontSize: 15, width: 32, height: 32, color: 'var(--theme-color-neutral-text-weaker)' }}
    />
  );

  return (
    <Layout style={{ height: '100vh', display: 'flex', flexDirection: 'row' }}>
      {/* ========== 桌面 Sider ========== */}
      {!isMobile && (
        <Sider
          trigger={null}
          collapsible
          collapsed={siderCollapsed}
          width={SIDER_WIDTH}
          collapsedWidth={SIDER_COLLAPSED_WIDTH}
          style={{
            background: siderBg,
            height: '100vh',
            position: 'sticky',
            top: 0,
            left: 0,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            borderRight: '1px solid rgba(255,255,255,0.04)',
          }}
        >
          {/* ═══ Logo 区 — 文本始终渲染，CSS 控制折叠过渡 ═══ */}
          <div className="bloom-logo-area">
            <div className="bloom-logo-icon-wrapper">
              <AimOutlined className="bloom-logo-icon" />
            </div>
            <div className="bloom-logo-text">
              <span className="bloom-logo-title">AI Collector</span>
              <span className="bloom-logo-sub">DATA PLATFORM</span>
            </div>
          </div>

          {/* ═══ 菜单 ═══ */}
          {renderMenu(siderCollapsed)}
        </Sider>
      )}

      {/* ========== 右侧主区域 ========== */}
      <Layout style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {/* ═══ Header（Bloom 风格：面包屑 + 右侧工具） ═══ */}
        <Header className="bloom-header">
          <Space size="small">
            {collapseButton}
            <Breadcrumb
              items={breadcrumbItems.map((item) => ({
                title:
                  item.path !== location.pathname ? (
                    <a onClick={() => navigate(item.path)}>{item.title}</a>
                  ) : (
                    item.title
                  ),
              }))}
            />
          </Space>

          <Space size={4}>
            {/* 组织选择器 */}
            <Select
              defaultValue="data-team"
              size="small"
              variant="borderless"
              popupMatchSelectWidth={false}
              options={organizations}
              suffixIcon={<DownOutlined style={{ fontSize: 10, color: 'var(--theme-color-neutral-text-weaker)' }} />}
              style={{ minWidth: 110, color: 'var(--theme-color-neutral-text-default)', fontSize: 12 }}
            />
            {/* 项目选择器 */}
            <Select
              defaultValue="main"
              size="small"
              variant="borderless"
              popupMatchSelectWidth={false}
              options={projects}
              suffixIcon={<DownOutlined style={{ fontSize: 10, color: 'var(--theme-color-neutral-text-weaker)' }} />}
              style={{ minWidth: 120, color: 'var(--theme-color-neutral-text-default)', fontSize: 12, fontWeight: 500 }}
            />

            <div style={{ width: 1, height: 20, background: 'var(--theme-color-neutral-border-weak)', margin: '0 6px' }} />

            <Tooltip title="帮助文档">
              <Button type="text" icon={<QuestionCircleOutlined />} style={{ color: 'var(--theme-color-neutral-text-weaker)', fontSize: 15, width: 32, height: 32 }} />
            </Tooltip>
            <Badge count={3} size="small" offset={[-2, 4]}>
              <Button type="text" icon={<BellOutlined />} style={{ color: 'var(--theme-color-neutral-text-weaker)', fontSize: 15, width: 32, height: 32 }} />
            </Badge>

            <Tooltip title={mode === 'dark' ? '暗色模式' : mode === 'light' ? '亮色模式' : '跟随系统'}>
              <Button type="text" icon={getThemeIcon(mode)} onClick={cycle} style={{ color: 'var(--theme-color-neutral-text-weaker)', fontSize: 15, width: 32, height: 32 }} />
            </Tooltip>

            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Avatar
                size={28}
                icon={<UserOutlined />}
                className="bloom-avatar bloom-header-avatar"
              />
            </Dropdown>
          </Space>
        </Header>

        {/* ═══ Content ═══ */}
        <Content className="bloom-content">
          {children}
        </Content>

        {/* ═══ Footer ═══ */}
        <Footer className="bloom-footer">
          <span>© 2026 AI Collector · v3.0</span>
          <span>
            <CheckCircleOutlined style={{ color: 'var(--theme-color-success-text)', marginRight: 4 }} />
            Status: Healthy
          </span>
        </Footer>
      </Layout>

      {/* ========== 移动端 Drawer ========== */}
      <Drawer
        placement="left"
        closable={false}
        onClose={() => setMobileDrawerOpen(false)}
        open={mobileDrawerOpen}
        width={SIDER_WIDTH}
        styles={{
          body: { padding: 0, background: siderBg, display: 'flex', flexDirection: 'column', height: '100%' },
          header: { display: 'none' },
        }}
      >
        <div className="bloom-logo-area">
          <div className="bloom-logo-icon-wrapper">
            <AimOutlined className="bloom-logo-icon" />
          </div>
          <div className="bloom-logo-text">
            <span className="bloom-logo-title">AI Collector</span>
            <span className="bloom-logo-sub">DATA PLATFORM</span>
          </div>
        </div>
        {renderMenu(false)}
      </Drawer>
    </Layout>
  );
};

export default MainLayout;
