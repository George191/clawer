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
  theme as antTheme,
  Typography,
  Space,
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
  BellOutlined,
  UserOutlined,
  SettingOutlined,
  LogoutOutlined,
  CheckCircleOutlined,
  HomeOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useThemeStore } from '@/stores/settings';
import type { FullToken } from '@/theme/tokens';

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

interface MainLayoutProps {
  children: React.ReactNode;
}

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/explorer', icon: <DatabaseOutlined />, label: '数据探索' },
  { key: '/tasks', icon: <ScheduleOutlined />, label: '任务中心' },
  { key: '/monitor', icon: <MonitorOutlined />, label: '采集监控' },
  { key: '/pipeline', icon: <ApartmentOutlined />, label: '管道管理' },
  { key: '/templates', icon: <FileProtectOutlined />, label: '模板管理' },
  { key: '/ai-collect', icon: <ThunderboltOutlined />, label: 'AI 采集' },
];

const breadcrumbNameMap: Record<string, string> = {
  '/': '仪表盘',
  '/explorer': '数据探索',
  '/tasks': '任务中心',
  '/monitor': '采集监控',
  '/pipeline': '管道管理',
  '/templates': '模板管理',
  '/ai-collect': 'AI 智能采集',
};

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [manualCollapsed, setManualCollapsed] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isTablet, setIsTablet] = useState(false);

  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggle } = useThemeStore();
  const { token: rawToken } = antTheme.useToken();
  const token = rawToken as unknown as FullToken;

  // ── 响应式检测 ──
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

  // ── 当前选中菜单 & 面包屑 ──
  const selectedKey = '/' + (location.pathname.split('/')[1] || '');
  const pathSnippets = location.pathname.split('/').filter(Boolean);

  const breadcrumbItems = [
    {
      title: (
        <Space size={4}>
          <HomeOutlined />
          <span>首页</span>
        </Space>
      ),
      path: '/',
    },
    ...pathSnippets.map((_, i) => {
      const path = '/' + pathSnippets.slice(0, i + 1).join('/');
      return {
        title: breadcrumbNameMap[path] || pathSnippets[i],
        path,
      };
    }),
  ];

  // ── 用户下拉菜单 ──
  const userMenuItems = [
    { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
    { type: 'divider' as const },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
    },
  ];

  // ── Sider 有效折叠状态 ──
  // < 1200px 自动折叠，>= 1200px 尊重手动切换
  const siderCollapsed = isTablet || manualCollapsed;

  // ── 菜单区（Sider 与 Drawer 复用） ──
  const showSiderText = !isMobile && !siderCollapsed;

  const siderMenu = (
    <Menu
      theme="dark"
      mode="inline"
      selectedKeys={[selectedKey]}
      items={menuItems}
      onClick={({ key }) => {
        navigate(key);
        setMobileDrawerOpen(false);
      }}
      style={{ borderInlineEnd: 'none', marginTop: 8, flex: 1 }}
    />
  );

  const siderUserCard = (
    <div
      style={{
        borderTop: '1px solid rgba(255, 255, 255, 0.06)',
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}
    >
      <Avatar
        size={36}
        icon={<UserOutlined />}
        style={{ background: '#3B82F6', flexShrink: 0 }}
      />
      {showSiderText && (
        <div style={{ overflow: 'hidden' }}>
          <Text
            style={{
              color: '#e6edf3',
              display: 'block',
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            Admin
          </Text>
          <Text style={{ color: '#8b949e', fontSize: 11 }}>系统管理员</Text>
        </div>
      )}
    </div>
  );

  // ── 折叠按钮：桌面端手动切换 ──
  const collapseButton = !isMobile ? (
    <Button
      type="text"
      icon={<MenuUnfoldOutlined />}
      onClick={() => setManualCollapsed((v) => !v)}
      style={{ fontSize: 16, width: 40, height: 40 }}
    />
  ) : (
    <Button
      type="text"
      icon={<MenuUnfoldOutlined />}
      onClick={() => setMobileDrawerOpen(true)}
      style={{ fontSize: 16, width: 40, height: 40 }}
    />
  );

  return (
    <Layout style={{ height: '100vh', display: 'flex', flexDirection: 'row' }}>
      {/* ========== 桌面端 Sider（>= 768px） ========== */}
      {!isMobile && (
        <Sider
          trigger={null}
          collapsible
          collapsed={siderCollapsed}
          width={token.siderWidth}
          collapsedWidth={token.siderCollapsedWidth}
          style={{
            background: token.colorSiderBg,
            height: '100vh',
            position: 'sticky',
            top: 0,
            left: 0,
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* Logo */}
          <div
            style={{
              height: token.headerHeight,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
              padding: '0 16px',
              borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
              flexShrink: 0,
            }}
          >
            <ApartmentOutlined style={{ fontSize: 24, color: '#3B82F6' }} />
            {!siderCollapsed && (
              <span
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  color: '#e6edf3',
                  whiteSpace: 'nowrap',
                }}
              >
                AI Collector
              </span>
            )}
          </div>

          {/* 菜单 */}
          <div style={{ flex: 1, overflow: 'auto' }}>{siderMenu}</div>

          {/* 用户卡片 */}
          <div style={{ flexShrink: 0 }}>{siderUserCard}</div>
        </Sider>
      )}

      {/* ========== 右侧主区域 ========== */}
      <Layout style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {/* ---- Header ---- */}
        <Header
          style={{
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: token.colorHeaderBg,
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            height: token.headerHeight,
            lineHeight: `${token.headerHeight}px`,
            position: 'sticky',
            top: 0,
            zIndex: 99,
            backdropFilter: 'blur(8px)',
          }}
        >
          {/* 左侧：折叠按钮 + 面包屑 */}
          <Space size="middle">
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

          {/* 右侧：通知 + 头像 + 主题 */}
          <Space size="middle">
            <Badge count={3} size="small">
              <Button
                type="text"
                icon={<BellOutlined style={{ fontSize: 18 }} />}
              />
            </Badge>

            <Dropdown
              menu={{
                items: userMenuItems,
                onClick: ({ key }) => {
                  if (key === 'settings') {
                    // TODO: 打开系统设置
                  } else if (key === 'logout') {
                    // TODO: 退出登录
                  }
                },
              }}
              placement="bottomRight"
            >
              <Space style={{ cursor: 'pointer' }} size={8}>
                <Avatar
                  size={32}
                  icon={<UserOutlined />}
                  style={{ background: '#3B82F6' }}
                />
                <Text
                  style={{ color: token.colorText, fontSize: 13 }}
                  className="hide-on-mobile"
                >
                  Admin
                </Text>
              </Space>
            </Dropdown>

            <Button
              type="text"
              icon={
                mode === 'dark' ? (
                  <SunOutlined style={{ fontSize: 18 }} />
                ) : (
                  <MoonOutlined style={{ fontSize: 18 }} />
                )
              }
              onClick={toggle}
            />
          </Space>
        </Header>

        {/* ---- Content ---- */}
        <Content
          style={{
            margin: 24,
            padding: 24,
            flex: 1,
            overflow: 'auto',
            background: token.colorBgContainer,
            borderRadius: token.borderRadiusLG,
            boxShadow: token.boxShadowCard,
          }}
          className="light-scrollbar"
        >
          {children}
        </Content>

        {/* ---- Footer ---- */}
        <Footer
          style={{
            height: 40,
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: token.colorBgLayout,
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            fontSize: 12,
            color: token.colorTextTertiary,
            flexShrink: 0,
          }}
        >
          <span>&copy; 2026 AI Collector &middot; v3.0</span>
          <span>
            <CheckCircleOutlined
              style={{ color: '#52c41a', marginRight: 6 }}
            />
            Status: Healthy
          </span>
        </Footer>
      </Layout>

      {/* ========== 移动端 Drawer（< 768px） ========== */}
      <Drawer
        placement="left"
        closable={false}
        onClose={() => setMobileDrawerOpen(false)}
        open={mobileDrawerOpen}
        width={token.siderWidth}
        styles={{
          body: {
            padding: 0,
            background: token.colorSiderBg,
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
          },
          header: { display: 'none' },
        }}
      >
        {/* Logo */}
        <div
          style={{
            height: token.headerHeight,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-start',
            gap: 10,
            padding: '0 16px',
            borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
            flexShrink: 0,
          }}
        >
          <ApartmentOutlined style={{ fontSize: 24, color: '#3B82F6' }} />
          <span
            style={{
              fontSize: 16,
              fontWeight: 700,
              color: '#e6edf3',
              whiteSpace: 'nowrap',
            }}
          >
            AI Collector
          </span>
        </div>

        {/* 菜单 */}
        <div style={{ flex: 1, overflow: 'auto' }}>{siderMenu}</div>

        {/* 用户卡片 */}
        <div style={{ flexShrink: 0 }}>
          <div
            style={{
              borderTop: '1px solid rgba(255, 255, 255, 0.06)',
              padding: '12px 16px',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
            }}
          >
            <Avatar
              size={36}
              icon={<UserOutlined />}
              style={{ background: '#3B82F6', flexShrink: 0 }}
            />
            <div style={{ overflow: 'hidden' }}>
              <Text
                style={{
                  color: '#e6edf3',
                  display: 'block',
                  fontSize: 13,
                  fontWeight: 600,
                }}
              >
                Admin
              </Text>
              <Text style={{ color: '#8b949e', fontSize: 11 }}>
                系统管理员
              </Text>
            </div>
          </div>
        </div>
      </Drawer>
    </Layout>
  );
};

export default MainLayout;
