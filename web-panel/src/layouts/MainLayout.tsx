import React, { useState, useEffect, useCallback } from 'react';
import {
  Layout,
  Menu,
  Button,
  Dropdown,
  Breadcrumb,
  theme as antTheme,
  Typography,
  Space,
  Tooltip,
  Divider,
  Badge,
} from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  ScheduleOutlined,
  MonitorOutlined,
  ApartmentOutlined,
  FileProtectOutlined,
  ThunderboltOutlined,
  SunOutlined,
  MoonOutlined,
  BellOutlined,
  SettingOutlined,
  LogoutOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useThemeStore } from '@/stores/settings';
import type { FullToken } from '@/theme/tokens';

const { Header, Sider, Content } = Layout;
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
    if (w < 768) setManualCollapsed(true);
  }, []);

  useEffect(() => {
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [handleResize]);

  const collapsed = isMobile || manualCollapsed;

  // ── 当前选中菜单 & 面包屑 ──
  const selectedKey = '/' + (location.pathname.split('/')[1] || '');
  const pathSnippets = location.pathname.split('/').filter(Boolean);

  const breadcrumbItems = [
    {
      title: <HomeOutlined style={{ fontSize: 14 }} />,
      onClick: () => navigate('/'),
    },
    ...pathSnippets.map((_, i) => {
      const path = '/' + pathSnippets.slice(0, i + 1).join('/');
      return {
        title: breadcrumbNameMap[path] || pathSnippets[i],
        onClick: () => navigate(path),
      };
    }),
  ];

  // ── 用户下拉菜单 ──
  const userMenuItems = [
    {
      key: 'profile',
      icon: <SettingOutlined />,
      label: '系统设置',
    },
    { type: 'divider' as const },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: token.colorBgLayout }}>
      {/* ── 侧边栏 ── */}
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={token.siderWidth}
        collapsedWidth={token.siderCollapsedWidth}
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          zIndex: 100,
          background: token.colorSiderBg,
          borderRight: `1px solid ${token.colorBorder}`,
        }}
      >
        {/* Logo */}
        <div
          style={{
            height: token.headerHeight,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            padding: collapsed ? 0 : '0 24px',
            borderBottom: `1px solid ${token.colorBorder}`,
          }}
        >
          {!collapsed ? (
            <Space size={10} align="center">
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: '0 4px 12px rgba(59, 130, 246, 0.4)',
                }}
              >
                <ThunderboltOutlined style={{ fontSize: 18, color: '#fff' }} />
              </div>
              <div>
                <div style={{ fontSize: 15, fontWeight: 700, color: token.colorText, lineHeight: 1.2, letterSpacing: '-0.01em' }}>
                  Spider AI
                </div>
                <div style={{ fontSize: 10, color: token.colorTextTertiary, letterSpacing: '0.05em' }}>
                  INTELLIGENCE PLATFORM
                </div>
              </div>
            </Space>
          ) : (
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 4px 12px rgba(59, 130, 246, 0.4)',
              }}
            >
              <ThunderboltOutlined style={{ fontSize: 18, color: '#fff' }} />
            </div>
          )}
        </div>

        {/* 导航菜单 */}
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => navigate(key)}
          items={menuItems}
          style={{
            background: 'transparent',
            borderInlineEnd: 'none',
            padding: '8px 0',
            fontSize: 14,
          }}
        />

        {/* 底部版本信息 */}
        {!collapsed && (
          <div
            style={{
              position: 'absolute',
              bottom: 16,
              left: 24,
              right: 24,
              textAlign: 'center',
            }}
          >
            <Divider style={{ margin: '0 0 12px', borderColor: token.colorBorder }} />
            <Text style={{ fontSize: 11, color: token.colorTextQuaternary }}>
              v1.0 · Spider Platform
            </Text>
          </div>
        )}
      </Sider>

      {/* ── 主内容区 ── */}
      <Layout
        style={{
          marginLeft: collapsed ? token.siderCollapsedWidth : token.siderWidth,
          transition: 'margin-left 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
          minHeight: '100vh',
          background: token.colorBgLayout,
        }}
      >
        {/* ── 顶栏 ── */}
        <Header
          style={{
            padding: '0 24px',
            background: token.colorHeaderBg,
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            height: token.headerHeight,
            lineHeight: `${token.headerHeight}px`,
            borderBottom: `1px solid ${token.colorBorder}`,
            position: 'sticky',
            top: 0,
            zIndex: 99,
          }}
        >
          {/* 左侧: 折叠按钮 + 面包屑 */}
          <Space size={16} align="center">
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setManualCollapsed(!manualCollapsed)}
              style={{
                fontSize: 16,
                width: 36,
                height: 36,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: token.colorTextSecondary,
              }}
            />

            {!isMobile && (
              <Breadcrumb
                items={breadcrumbItems}
                style={{ fontSize: 13 }}
              />
            )}
          </Space>

          {/* 右侧: 操作区 */}
          <Space size={8} align="center">
            {/* 暗色/亮色切换 */}
            <Tooltip title={mode === 'dark' ? '切换亮色模式' : '切换暗色模式'}>
              <Button
                type="text"
                icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
                onClick={toggle}
                style={{
                  width: 36,
                  height: 36,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: token.colorTextSecondary,
                  fontSize: 16,
                }}
              />
            </Tooltip>

            {/* 通知 */}
            <Tooltip title="通知">
              <Badge dot offset={[-2, 2]}>
                <Button
                  type="text"
                  icon={<BellOutlined />}
                  style={{
                    width: 36,
                    height: 36,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: token.colorTextSecondary,
                    fontSize: 16,
                  }}
                />
              </Badge>
            </Tooltip>

            {/* 用户 */}
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Button
                type="text"
                style={{
                  height: 36,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '0 8px',
                }}
              >
                <div
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 8,
                    background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#fff',
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  A
                </div>
                {!isMobile && (
                  <Text style={{ fontSize: 13, color: token.colorTextSecondary }}>
                    Admin
                  </Text>
                )}
              </Button>
            </Dropdown>
          </Space>
        </Header>

        {/* ── 内容区 ── */}
        <Content style={{ padding: 24, minHeight: 280 }}>
          <div className={mode === 'light' ? 'light-mode' : ''}>
            {children}
          </div>
        </Content>

        {/* ── 底部 ── */}
        <div
          style={{
            textAlign: 'center',
            padding: '12px 24px',
            fontSize: 12,
            color: token.colorTextQuaternary,
            borderTop: `1px solid ${token.colorBorder}`,
          }}
        >
          Spider AI Intelligence Platform &copy; {new Date().getFullYear()}
        </div>
      </Layout>
    </Layout>
  );
};

export default MainLayout;