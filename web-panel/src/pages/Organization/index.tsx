import React, { useEffect, useRef, useState } from 'react';
import { Alert, Avatar, Button, Card, ConfigProvider, Descriptions, Dropdown, Empty, Form, Input, Modal, Select, Space, Spin, Tag, Typography, message } from '@/components/ui';
import enUS from '@/components/ui/locale';
import { ArrowLeftOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, LockOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, UserAddOutlined } from '@/components/ui/icons';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  fetchCurrentUserContext, fetchAccessUsers, fetchAccessUser, createAccessUser, updateAccessUser, deleteAccessUser,
  fetchAccessTenants, createAccessTenant, updateAccessTenant, deleteAccessTenant,
  fetchAccessTeams, createAccessTeam, updateAccessTeam, deleteAccessTeam,
} from '../../services/api';
import type { AccessPage, AccessUser, AccessUserInput, AccessTenant, AccessTenantInput, AccessTeam, AccessQuery } from '../../services/api';
import type { CurrentUserContext } from '../../services/types';
import './style.css';
import './compact.css';
import './final.css';
import AccessListPanel from './AccessListPanel';

type View = 'users' | 'roles' | 'permissions' | 'tenants' | 'teams';
type OrganizationRow = Pick<AccessTenant, 'id' | 'name'> & Partial<AccessTenant>;
type Editor = { type: 'users'; record: AccessUser | null } | { type: 'tenants'; record: OrganizationRow | null } | { type: 'teams'; record: AccessTeam | null };
const emptyPage = <T,>(): AccessPage<T> => ({ items: [], total: 0, page: 1, size: 10 });
const tabs: Array<{ key: View; label: string }> = [{ key: 'users', label: 'Users' }, { key: 'roles', label: 'Roles' }, { key: 'permissions', label: 'Permissions' }, { key: 'tenants', label: 'Organizations' }, { key: 'teams', label: 'Teams' }];
const statuses = ['Active', 'Disabled', 'Expired', 'Pending', 'Locked', 'Hidden'];
const plans = ['Free', 'Basic', 'Professional', 'Enterprise'];
const roleOf = (user: Pick<AccessUser, 'is_superuser' | 'is_tenant_admin'>) => user.is_superuser ? 'Platform administrator' : user.is_tenant_admin ? 'Organization administrator' : 'User';
const userName = (user: AccessUser) => user.full_name?.trim() || user.email;
const initials = (name: string) => name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
const showStatus = (value?: number) => value === undefined ? 'Not exposed' : <span className={`access-status is-${value === 0 ? 'active' : value === 3 ? 'pending' : 'inactive'}`}>{statuses[value] || 'Unknown'}</span>;
const date = (value?: string) => value ? new Date(value).toLocaleString('en-US') : 'Not available';
const errorMessage = (error: unknown) => {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown; errors?: unknown } } })?.response;
  if (response?.status === 403) return 'You do not have permission to perform this action.';
  const detail = response?.data?.detail ?? response?.data?.errors;
  return typeof detail === 'string' ? detail : response?.status === 422 ? 'Some fields were rejected by the server. Review the form and try again.' : 'The request could not be completed. Please try again.';
};

const Organization: React.FC = () => {
  const navigate = useNavigate();
  const { userId } = useParams<{ userId: string }>();
  const [params, setParams] = useSearchParams();
  const requested = params.get('view') as View;
  const view: View = tabs.some(tab => tab.key === requested) ? requested : 'users';
  const [context, setContext] = useState<CurrentUserContext>();
  const [contextError, setContextError] = useState('');
  const [contextVersion, setContextVersion] = useState(0);
  const [version, setVersion] = useState(0);
  const [teamId, setTeamId] = useState(() => params.get('team') || '');
  const [tenantId, setTenantId] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [roleFilter, setRoleFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<number>();
  const [users, setUsers] = useState<AccessPage<AccessUser>>(emptyPage);
  const [tenants, setTenants] = useState<AccessPage<OrganizationRow>>(emptyPage);
  const [teams, setTeams] = useState<AccessPage<AccessTeam>>(emptyPage);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [detail, setDetail] = useState<AccessUser>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [mutating, setMutating] = useState(false);
  const pending = useRef(false);
  const active = useRef(true);
  const [form] = Form.useForm();
  const platformAdmin = context?.user.is_superuser === true;
  const organizationAdmin = context?.user.is_tenant_admin === true;
  const canManageUsers = platformAdmin || organizationAdmin;
  const canManageTeams = platformAdmin || organizationAdmin;

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setContextError('');
    fetchCurrentUserContext().then(next => {
      if (!current) return;
      setContext(next);
      setTeamId(old => next.teams.some(team => team.id === old) ? old : next.teams[0]?.id || '');
      setTenantId(old => next.tenants.some(tenant => tenant.id === old) ? old : next.tenants[0]?.id || '');
    }).catch(error => { if (current) setContextError(errorMessage(error)); });
    return () => { current = false; };
  }, [contextVersion]);
  useEffect(() => { const timer = window.setTimeout(() => { setQuery(search.trim()); setPage(1); }, 250); return () => window.clearTimeout(timer); }, [search]);
  useEffect(() => { setPage(1); setSearch(''); setQuery(''); setEditor(null); setLoadError(''); }, [view, teamId, tenantId]);

  useEffect(() => {
    if (!context || userId || view === 'roles' || view === 'permissions') return;
    let current = true;
    setLoading(true); setLoadError('');
    const base: AccessQuery = { page, size: pageSize, ...(query ? { search: query } : {}) };
    const run = async () => {
      if (view === 'users') {
        const role = roleFilter === 'platform' ? { is_superuser: true } : roleFilter === 'organization' ? { is_superuser: false, is_tenant_admin: true } : roleFilter === 'user' ? { is_superuser: false, is_tenant_admin: false } : {};
        const result = teamId ? await fetchAccessUsers(teamId, { ...base, ...role, ...(statusFilter === undefined ? {} : { status: statusFilter }) }) : emptyPage<AccessUser>();
        if (current) { setUsers(result); if (page > 1 && !result.items.length) setPage(Math.max(1, Math.ceil(result.total / pageSize))); }
      } else if (view === 'tenants') {
        const result = platformAdmin ? await fetchAccessTenants(base) : { items: context.tenants.filter(tenant => tenant.name.toLowerCase().includes(query.toLowerCase())), total: context.tenants.length, page: 1, size: context.tenants.length };
        if (current) setTenants(result);
      } else {
        const result = tenantId ? await fetchAccessTeams(tenantId, { page, size: pageSize }) : emptyPage<AccessTeam>();
        if (current) { setTeams(result); if (page > 1 && !result.items.length) setPage(Math.max(1, Math.ceil(result.total / pageSize))); }
      }
    };
    void run().catch(error => { if (current) { setLoadError(errorMessage(error)); setUsers(emptyPage()); setTenants(emptyPage()); setTeams(emptyPage()); } }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [context, userId, view, page, pageSize, query, teamId, tenantId, roleFilter, statusFilter, platformAdmin, version]);

  useEffect(() => {
    if (!userId || !context || !teamId) return;
    let current = true;
    setDetail(undefined); setDetailLoading(true); setLoadError('');
    fetchAccessUser(userId, teamId).then(user => { if (current) setDetail(user); }).catch(error => { if (current) setLoadError(errorMessage(error)); }).finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; };
  }, [context, userId, teamId, version]);

  const refresh = () => { setVersion(value => value + 1); setContextVersion(value => value + 1); };
  const mutate = async (action: () => Promise<unknown>) => {
    if (pending.current) return false;
    pending.current = true; setMutating(true);
    try { await action(); if (active.current) { message.success('Changes saved'); refresh(); } return true; }
    catch (error) { if (active.current) message.error(errorMessage(error)); return false; }
    finally { pending.current = false; if (active.current) setMutating(false); }
  };
  const openEditor = (next: Editor) => {
    if (pending.current) return;
    form.resetFields();
    if (next.type === 'users') form.setFieldsValue(next.record ? { ...next.record, accountRole: next.record.is_superuser ? 'platform' : next.record.is_tenant_admin ? 'organization' : 'user' } : { status: 0, accountRole: 'user' });
    else if (next.type === 'tenants') form.setFieldsValue(next.record || { plan: 0, status: 0 });
    else form.setFieldsValue(next.record || {});
    setEditor(next);
  };
  const save = async () => {
    if (!editor || pending.current) return;
    let values;
    try { values = await form.validateFields(); } catch { return; }
    const target = editor;
    const saved = await mutate(async () => {
      if (target.type === 'users') {
        if (!canManageUsers || !teamId) throw new Error('Unavailable user scope');
        const body: AccessUserInput = { full_name: values.full_name, email: values.email, phone: values.phone, status: values.status, ...(platformAdmin ? { is_superuser: values.accountRole === 'platform', is_tenant_admin: values.accountRole === 'organization' } : {}), ...(!target.record ? { password: values.password } : {}) };
        return target.record ? updateAccessUser(target.record.id, teamId, body) : createAccessUser(teamId, body);
      }
      if (target.type === 'tenants') {
        if (!platformAdmin) throw new Error('Unavailable organization scope');
        const body: AccessTenantInput = { name: values.name, description: values.description, plan: values.plan, status: values.status };
        return target.record ? updateAccessTenant(target.record.id, body) : createAccessTenant(body);
      }
      if (!canManageTeams || !tenantId) throw new Error('Unavailable team scope');
      const body = { name: values.name, description: values.description };
      return target.record ? updateAccessTeam(target.record.id, target.record.tenant_id, body) : createAccessTeam(tenantId, body);
    });
    if (saved && active.current) setEditor(null);
  };
  const confirmDelete = (type: 'user' | 'organization' | 'team', name: string, action: () => Promise<unknown>) => Modal.confirm({
    title: `Delete ${type} “${name}”?`, content: 'This action permanently removes the record.', okText: 'Delete', okButtonProps: { danger: true },
    onOk: async () => { if (!await mutate(action)) throw new Error('The record was not deleted'); },
  });
  const changeView = (next: View) => { if (!pending.current) setParams({ view: next }); };
  const pageProps = (total: number) => ({ current: page, pageSize, total, onChange: (next: number, size: number) => { setPage(size === pageSize ? next : 1); setPageSize(size); } });
  const exportPage = () => {
    const cell = (value: string) => `"${(/^[=+@-]/.test(value) ? "'" : '') + value.replace(/"/g, '""')}"`;
    const csv = ['Name,Email,Phone,Account role,Status', ...users.items.map(user => [userName(user), user.email, user.phone, roleOf(user), statuses[user.status] || 'Unknown'].map(cell).join(','))].join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = 'access-users-page.csv'; link.click(); URL.revokeObjectURL(url);
  };
  const userActions = (user: AccessUser) => canManageUsers && user.id !== context?.user.id ? <Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: 'Edit user', icon: <EditOutlined /> }, { key: 'toggle', label: user.status === 0 ? 'Disable access' : 'Enable access', icon: <LockOutlined /> }, ...(platformAdmin ? [{ key: 'delete', label: 'Delete user', danger: true, icon: <DeleteOutlined /> }] : [])], onClick: ({ key }) => {
    if (key === 'edit') openEditor({ type: 'users', record: user });
    if (key === 'toggle') void mutate(() => updateAccessUser(user.id, teamId, { status: user.status === 0 ? 1 : 0 }));
    if (key === 'delete') confirmDelete('user', userName(user), () => deleteAccessUser(user.id, teamId));
  } }}><Button type="text" aria-label={`Actions for ${userName(user)}`} disabled={mutating} icon={<MoreOutlined />} /></Dropdown> : null;
  const scopeControl = view === 'users' ? <Select aria-label="Directory team" value={teamId || undefined} placeholder="Select a team" disabled={mutating} onChange={setTeamId} options={context?.teams.map(team => ({ value: team.id, label: team.name }))} /> : view === 'teams' ? <Select aria-label="Team organization" value={tenantId || undefined} placeholder="Select an organization" disabled={mutating} onChange={setTenantId} options={context?.tenants.map(tenant => ({ value: tenant.id, label: tenant.name }))} /> : null;

  const renderUsers = () => <AccessListPanel<AccessUser> filters={<><Typography.Title level={5}>Directory scope</Typography.Title>{scopeControl}<Select aria-label="Account role filter" value={roleFilter} onChange={value => { setRoleFilter(value); setPage(1); }} options={[{ value: 'all', label: 'All account roles' }, { value: 'platform', label: 'Platform administrator' }, { value: 'organization', label: 'Organization administrator' }, { value: 'user', label: 'User' }]} /><Select aria-label="User status filter" allowClear placeholder="All statuses" value={statusFilter} onChange={value => { setStatusFilter(value); setPage(1); }} options={statuses.map((label, value) => ({ value, label }))} /><Typography.Text type="secondary" className="access-scope-note">Users in the selected team’s organization. Your own account is excluded.</Typography.Text></>} search={{ placeholder: 'Search name or email', value: search, onChange: event => setSearch(event.target.value) }} actions={<><Button icon={<DownloadOutlined />} disabled={!users.items.length || loading} onClick={exportPage}>Export page</Button>{canManageUsers && <Button icon={<UserAddOutlined />} disabled={!teamId || loading || mutating} onClick={() => openEditor({ type: 'users', record: null })}>Add user</Button>}</>} table={{ rowKey: 'id', loading, dataSource: users.items, scroll: { x: 800 }, pagination: pageProps(users.total), locale: { emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={!teamId ? 'No accessible team is available' : loadError ? 'Directory could not be loaded' : 'No users match the current filters'} /> }, columns: [
    { title: 'User', render: (_: unknown, user: AccessUser) => <Space><Avatar>{initials(userName(user))}</Avatar><div><Button type="link" className="access-user-link" onClick={() => navigate(`/organization/users/${user.id}?team=${encodeURIComponent(teamId)}`)}>{userName(user)}</Button><Typography.Text className="access-subtext" type="secondary">{user.email}</Typography.Text></div></Space> },
    { title: 'Account role', render: (_: unknown, user: AccessUser) => roleOf(user) }, { title: 'Status', dataIndex: 'status', render: showStatus }, { title: 'Updated', dataIndex: 'updated_at', render: date }, { title: 'Actions', width: 85, render: (_: unknown, user: AccessUser) => userActions(user) },
  ] }} />;
  const renderTenants = () => <AccessListPanel<OrganizationRow> filters={<Typography.Text type="secondary">{platformAdmin ? 'Server-managed organizations and account plans.' : 'Organizations available to your signed-in account.'}</Typography.Text>} search={{ placeholder: 'Search organizations', value: search, onChange: event => setSearch(event.target.value) }} actions={platformAdmin && <Button icon={<PlusOutlined />} disabled={mutating} onClick={() => openEditor({ type: 'tenants', record: null })}>Add organization</Button>} table={{ rowKey: 'id', loading, dataSource: tenants.items, pagination: platformAdmin ? pageProps(tenants.total) : false, columns: [
    { title: 'Organization', dataIndex: 'name' }, { title: 'Description', dataIndex: 'description', render: (value: string) => value || 'Not exposed' }, { title: 'Plan', dataIndex: 'plan', render: (value?: number) => value === undefined ? 'Not exposed' : plans[value] || 'Unknown' }, { title: 'Status', dataIndex: 'status', render: showStatus },
    { title: 'Actions', width: 85, render: (_: unknown, tenant: OrganizationRow) => platformAdmin && <Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: 'Edit organization', icon: <EditOutlined /> }, { key: 'delete', label: 'Delete organization', danger: true, icon: <DeleteOutlined /> }], onClick: ({ key }) => key === 'edit' ? openEditor({ type: 'tenants', record: tenant }) : confirmDelete('organization', tenant.name, () => deleteAccessTenant(tenant.id)) }}><Button type="text" disabled={mutating} icon={<MoreOutlined />} aria-label={`Actions for ${tenant.name}`} /></Dropdown> },
  ] }} />;
  const renderTeams = () => <AccessListPanel<AccessTeam> filters={<><Typography.Title level={5}>Organization scope</Typography.Title>{scopeControl}<Typography.Text className="access-scope-note" type="secondary">Membership and team-role assignment are not exposed by the current API.</Typography.Text></>} search={{ placeholder: 'Search is unavailable for this endpoint', disabled: true }} actions={canManageTeams && <Button icon={<PlusOutlined />} disabled={!tenantId || mutating} onClick={() => openEditor({ type: 'teams', record: null })}>Add team</Button>} table={{ rowKey: 'id', loading, dataSource: teams.items, pagination: pageProps(teams.total), columns: [
    { title: 'Team', dataIndex: 'name' }, { title: 'Description', dataIndex: 'description', render: (value: string) => value || 'No description' }, { title: 'Organization', dataIndex: 'tenant_id', render: (value: string) => context?.tenants.find(tenant => tenant.id === value)?.name || value }, { title: 'Updated', dataIndex: 'updated_at', render: date },
    { title: 'Actions', width: 85, render: (_: unknown, team: AccessTeam) => canManageTeams && <Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: 'Edit team', icon: <EditOutlined /> }, { key: 'delete', label: 'Delete team', danger: true, icon: <DeleteOutlined /> }], onClick: ({ key }) => key === 'edit' ? openEditor({ type: 'teams', record: team }) : confirmDelete('team', team.name, () => deleteAccessTeam(team.id, team.tenant_id)) }}><Button type="text" disabled={mutating} icon={<MoreOutlined />} aria-label={`Actions for ${team.name}`} /></Dropdown> },
  ] }} />;
  const renderRoles = () => <><Alert type="info" showIcon message="Built-in access roles" description="Custom role definitions and assignments are not exposed by the current API." /><div className="access-role-grid access-contract-grid">{[
    ['Platform administrator', 'Platform-wide user, organization and team administration. Assigned through account settings by a platform administrator.'],
    ['Organization administrator', 'Administration within organizations where the account has active team membership.'],
    ['User', 'Access limited to the account’s active team memberships.'],
    ['Team roles', 'Member, Moderator, Administrator and Owner are stored on team memberships. Membership-role management is not exposed by the current API.'],
  ].map(([name, description]) => <Card key={name} className="access-role-card"><Tag>Built-in</Tag><Typography.Title level={4}>{name}</Typography.Title><Typography.Paragraph type="secondary">{description}</Typography.Paragraph></Card>)}</div></>;

  const renderEditor = () => <Modal title={editor ? `${editor.record ? 'Edit' : 'Add'} ${editor.type === 'users' ? 'user' : editor.type === 'teams' ? 'team' : 'organization'}` : ''} open={!!editor} onCancel={() => { if (!pending.current) setEditor(null); }} onOk={() => void save()} confirmLoading={mutating} cancelButtonProps={{ disabled: mutating }} maskClosable={!mutating} keyboard={!mutating} destroyOnHidden okText="Save changes"><Form form={form} layout="vertical" disabled={mutating}>
    {editor?.type === 'users' ? <><Typography.Paragraph type="secondary">New users are added to the selected team as members. Account roles apply at platform or organization level.</Typography.Paragraph><Form.Item name="full_name" label="Full name" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}><Input /></Form.Item><Form.Item name="phone" label="Phone" rules={[{ required: true }]}><Input /></Form.Item>{!editor.record && <Form.Item name="password" label="Initial password" rules={[{ required: true, min: 8 }]}><Input.Password autoComplete="new-password" /></Form.Item>}{platformAdmin && <Form.Item name="accountRole" label="Account role" rules={[{ required: true }]}><Select options={[{ value: 'user', label: 'User' }, { value: 'organization', label: 'Organization administrator' }, { value: 'platform', label: 'Platform administrator' }]} /></Form.Item>}<Form.Item name="status" label="Status" rules={[{ required: true }]}><Select options={statuses.map((label, value) => ({ value, label }))} /></Form.Item></> : <><Form.Item name="name" label="Name" rules={[{ required: true }, ...(editor?.type === 'teams' ? [{ pattern: /^[a-zA-Z0-9_-]{1,64}$/, message: 'Use 1–64 letters, numbers, underscores or hyphens.' }] : [])]}><Input /></Form.Item><Form.Item name="description" label="Description"><Input.TextArea rows={3} maxLength={editor?.type === 'tenants' ? 256 : undefined} /></Form.Item>{editor?.type === 'tenants' && <><Form.Item name="plan" label="Plan" rules={[{ required: true }]}><Select options={plans.map((label, value) => ({ value, label }))} /></Form.Item><Form.Item name="status" label="Status" rules={[{ required: true }]}><Select options={statuses.map((label, value) => ({ value, label }))} /></Form.Item></>}</>}
  </Form></Modal>;

  if (contextError) return <div className="access-page"><Alert type="error" showIcon message="Access context could not be loaded" description={contextError} action={<Button onClick={() => setContextVersion(value => value + 1)}>Retry</Button>} /></div>;
  if (!context) return <div className="access-page"><Spin tip="Loading access context" /></div>;
  return <ConfigProvider locale={enUS}><div className={`access-page ${userId ? 'access-user-detail-page' : 'access-directory-page'}`}>
    {userId ? <><Button type="text" className="access-back-button" icon={<ArrowLeftOutlined />} onClick={() => navigate(`/organization?view=users&team=${encodeURIComponent(teamId)}`)}>Back to users</Button>{detailLoading ? <Spin /> : loadError ? <Alert type="error" showIcon message="User details could not be loaded" description={loadError} action={<Button onClick={refresh}>Retry</Button>} /> : !detail ? <Empty description="No user is available in the selected scope" /> : <Card className="access-profile-card"><div className="access-detail-identity"><Avatar size={64}>{initials(userName(detail))}</Avatar><Typography.Title level={3}>{userName(detail)}</Typography.Title><Tag>{roleOf(detail)}</Tag></div><Descriptions column={1} bordered items={[{ key: 'email', label: 'Email', children: detail.email }, { key: 'phone', label: 'Phone', children: detail.phone }, { key: 'status', label: 'Status', children: showStatus(detail.status) }, { key: 'created', label: 'Created', children: date(detail.created_at) }, { key: 'updated', label: 'Updated', children: date(detail.updated_at) }]} /><div className="access-detail-actions">{canManageUsers && <Button icon={<EditOutlined />} onClick={() => openEditor({ type: 'users', record: detail })}>Edit user</Button>}</div></Card>}</> : <><header className="access-header"><div><Typography.Text className="access-kicker">IDENTITY & ACCESS MANAGEMENT</Typography.Text><Typography.Title level={2}>Access</Typography.Title></div><Space><Typography.Text type="secondary">{context.user.full_name || context.user.email}</Typography.Text><Button icon={<ReloadOutlined />} onClick={refresh} disabled={loading || mutating}>Refresh</Button></Space></header><nav className="access-tabs">{tabs.map(tab => <button key={tab.key} type="button" disabled={mutating} className={view === tab.key ? 'is-active' : ''} onClick={() => changeView(tab.key)}>{tab.label}</button>)}</nav>{loadError && <Alert className="access-load-error" type="error" showIcon message="Directory could not be loaded" description={loadError} action={<Button onClick={refresh}>Retry</Button>} />}{view === 'users' ? renderUsers() : view === 'tenants' ? renderTenants() : view === 'teams' ? renderTeams() : view === 'roles' ? renderRoles() : <Card><Empty description="Fine-grained permission policies are not exposed by the current API" /><Typography.Paragraph type="secondary">Account flags and active team memberships are enforced by the server. Custom permission catalogs, policy editing and effective-permission inspection require additional backend interfaces.</Typography.Paragraph></Card>}</>}
    {renderEditor()}
  </div></ConfigProvider>;
};
export default Organization;
