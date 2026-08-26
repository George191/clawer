import React, { useCallback, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Progress,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  BranchesOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  FireOutlined,
  GlobalOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  RadarChartOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  fetchPlatformOverview,
  type PlatformEtlLayer,
  type PlatformGuardrail,
  type PlatformOverview,
  type PlatformRecommendation,
  type PlatformSourceGroup,
  type PlatformStage,
  type PlatformTask,
} from '@/services/aiApi';
import { usePolling } from '@/hooks/usePolling';
import workspacePalette from '@/pages/AICollect/palette';

const { Title, Text } = Typography;

const palette = workspacePalette;

const statusMeta = {
  healthy: { label: 'Healthy', tag: 'success', dot: palette.success },
  degraded: { label: 'Degraded', tag: 'warning', dot: palette.warning },
  inactive: { label: 'Inactive', tag: 'default', dot: palette.subtle },
} as const;

const taskStatusMeta = {
  queued: { label: '排队中', tag: 'processing' },
  running: { label: '运行中', tag: 'processing' },
  paused: { label: '已暂停', tag: 'warning' },
  completed: { label: '已完成', tag: 'success' },
  failed: { label: '失败', tag: 'error' },
  planned: { label: '待发布', tag: 'default' },
} as const;

const recommendationMeta = {
  critical: { color: palette.danger, label: 'Critical' },
  warning: { color: palette.warning, label: 'Warning' },
  info: { color: palette.accent, label: 'Info' },
} as const;

const stageIcons: Record<string, React.ReactNode> = {
  crawler: <RobotOutlined />,
  downloader: <DatabaseOutlined />,
  syncer: <LinkOutlined />,
  etl: <BranchesOutlined />,
};

const sourceIcons: Record<string, React.ReactNode> = {
  patent: <RadarChartOutlined />,
  news: <GlobalOutlined />,
  navwarn: <SafetyCertificateOutlined />,
  intelligence: <ApiOutlined />,
  other: <DatabaseOutlined />,
};

const statCardStyle: React.CSSProperties = {
  borderRadius: 8,
  border: `1px solid ${palette.border}`,
  background: palette.surface,
  boxShadow: palette.shadow,
  padding: 18,
};

const panelStyle: React.CSSProperties = {
  borderRadius: 8,
  border: `1px solid ${palette.border}`,
  background: palette.surfaceElevated,
  boxShadow: palette.shadow,
  padding: 18,
};

function formatTime(value?: string) {
  if (!value) return '刚刚准备';
  return dayjs(value).format('MM-DD HH:mm');
}

function renderStatusTag(status: 'healthy' | 'degraded' | 'inactive') {
  const meta = statusMeta[status];
  return <Tag color={meta.tag}>{meta.label}</Tag>;
}

function StageCard({ stage }: { stage: PlatformStage }) {
  return (
    <div
      style={{
        ...panelStyle,
        minHeight: 228,
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        background: `linear-gradient(180deg, ${stage.accent}12 0%, ${palette.surfaceElevated} 42%)`,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span
            style={{
              width: 40,
              height: 40,
              borderRadius: 8,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: `${stage.accent}20`,
              color: stage.accent,
              fontSize: 18,
            }}
          >
            {stageIcons[stage.key] ?? <ThunderboltOutlined />}
          </span>
          <div>
            <div style={{ color: palette.text, fontSize: 17, fontWeight: 700 }}>{stage.title}</div>
            <Text style={{ color: palette.subtle, fontSize: 12 }}>{stage.badge}</Text>
          </div>
        </div>
        {renderStatusTag(stage.status)}
      </div>

      <Text style={{ color: palette.muted, lineHeight: 1.7 }}>{stage.description}</Text>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>主指标</Text>
          <div style={{ marginTop: 6, color: palette.text, fontSize: 18, fontWeight: 700 }}>{stage.primaryMetric}</div>
        </div>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>运行参数</Text>
          <div style={{ marginTop: 6, color: palette.text, fontSize: 15, fontWeight: 600 }}>{stage.secondaryMetric}</div>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {stage.dependencies.map((dependency) => (
          <span
            key={dependency}
            style={{
              padding: '4px 8px',
              borderRadius: 999,
              border: `1px solid ${palette.borderSoft}`,
              color: palette.muted,
              fontSize: 12,
              background: palette.surfaceSoft,
            }}
          >
            {dependency}
          </span>
        ))}
      </div>

      <div style={{ marginTop: 'auto' }}>
        <Text style={{ color: palette.subtle, fontSize: 12 }}>启动入口</Text>
        <div style={{ marginTop: 6, color: palette.text, fontFamily: 'monospace', fontSize: 13 }}>{stage.command}</div>
      </div>
    </div>
  );
}

function SourceCard({ source }: { source: PlatformSourceGroup }) {
  return (
    <div style={{ ...statCardStyle, minHeight: 190 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span
            style={{
              width: 38,
              height: 38,
              borderRadius: 8,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: palette.accentSoft,
              color: palette.accent,
              fontSize: 18,
            }}
          >
            {sourceIcons[source.key] ?? <DatabaseOutlined />}
          </span>
          <div>
            <div style={{ color: palette.text, fontSize: 16, fontWeight: 700 }}>{source.label}</div>
            <Text style={{ color: palette.subtle, fontSize: 12 }}>{source.count} 个模板</Text>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ color: palette.text, fontSize: 20, fontWeight: 800 }}>{source.fieldCount}</div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>字段总数</Text>
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>覆盖域名</Text>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            {source.domains.length > 0 ? source.domains.map((domain) => (
              <span
                key={domain}
                style={{
                  padding: '4px 8px',
                  borderRadius: 999,
                  color: palette.muted,
                  fontSize: 12,
                  background: palette.surfaceSoft,
                  border: `1px solid ${palette.borderSoft}`,
                }}
              >
                {domain}
              </span>
            )) : <Text style={{ color: palette.subtle }}>待补充</Text>}
          </div>
        </div>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>代表模板</Text>
          <div style={{ marginTop: 8, color: palette.text, lineHeight: 1.8 }}>
            {source.templates.join(' / ')}
          </div>
        </div>
      </div>
    </div>
  );
}

function TaskRow({ task }: { task: PlatformTask }) {
  const meta = taskStatusMeta[task.status];
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1.4fr) 120px 100px 80px',
        gap: 12,
        padding: '14px 0',
        borderBottom: `1px solid ${palette.borderSoft}`,
        alignItems: 'center',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ color: palette.text, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {task.name}
        </div>
        <Text style={{ color: palette.subtle, fontSize: 12 }}>
          {task.kind === 'live' ? `${task.template} · ${formatTime(task.startedAt)}` : task.mode ?? '待编排'}
        </Text>
      </div>
      <div style={{ color: palette.muted, fontSize: 13 }}>
        {task.records > 0 ? `${task.records.toLocaleString()} 条` : task.kind === 'live' ? '采集中' : '待触发'}
      </div>
      <Tag color={meta.tag}>{meta.label}</Tag>
      <div style={{ color: palette.text, fontSize: 13, fontWeight: 600 }}>
        {task.status === 'planned' ? '--' : `${task.progress}%`}
      </div>
    </div>
  );
}

function EtlLayerCard({ layer }: { layer: PlatformEtlLayer }) {
  return (
    <div
      style={{
        ...statCardStyle,
        minHeight: 182,
        background: `linear-gradient(180deg, ${statusMeta[layer.status].dot}10 0%, ${palette.surface} 48%)`,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
        <div>
          <div style={{ color: palette.text, fontSize: 16, fontWeight: 700 }}>{layer.label}</div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>{layer.schema}</Text>
        </div>
        {renderStatusTag(layer.status)}
      </div>

      <Text style={{ display: 'block', marginTop: 14, color: palette.muted, lineHeight: 1.7 }}>{layer.focus}</Text>

      <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>Topic In</Text>
          <div style={{ marginTop: 4, color: palette.text, fontFamily: 'monospace', fontSize: 13 }}>
            {layer.topicIn || '未配置'}
          </div>
        </div>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>Topic Out</Text>
          <div style={{ marginTop: 4, color: palette.text, fontFamily: 'monospace', fontSize: 13 }}>
            {layer.topicOut || '未配置'}
          </div>
        </div>
      </div>
    </div>
  );
}

function GuardrailCard({ item }: { item: PlatformGuardrail }) {
  return (
    <div style={{ ...statCardStyle, minHeight: 144 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <Text style={{ color: palette.subtle, fontSize: 12 }}>{item.label}</Text>
          <div style={{ marginTop: 8, color: palette.text, fontSize: 22, fontWeight: 800 }}>{item.value}</div>
        </div>
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: statusMeta[item.status].dot,
            marginTop: 6,
            boxShadow: `0 0 14px ${statusMeta[item.status].dot}`,
            flexShrink: 0,
          }}
        />
      </div>
      <Text style={{ display: 'block', marginTop: 12, color: palette.muted, lineHeight: 1.7 }}>{item.hint}</Text>
    </div>
  );
}

function RecommendationCard({
  item,
  onAction,
}: {
  item: PlatformRecommendation;
  onAction: (path: string) => void;
}) {
  const meta = recommendationMeta[item.level];
  return (
    <div
      style={{
        ...panelStyle,
        padding: 16,
        borderColor: `${meta.color}55`,
        background: `linear-gradient(180deg, ${meta.color}14 0%, ${palette.surfaceElevated} 44%)`,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <FireOutlined style={{ color: meta.color, fontSize: 16 }} />
          <Text style={{ color: palette.text, fontSize: 15, fontWeight: 700 }}>{item.title}</Text>
        </div>
        <Tag color={item.level === 'critical' ? 'error' : item.level === 'warning' ? 'warning' : 'processing'}>
          {meta.label}
        </Tag>
      </div>
      <Text style={{ display: 'block', marginTop: 10, color: palette.muted, lineHeight: 1.7 }}>{item.detail}</Text>
      <Button
        type="text"
        icon={<PlayCircleOutlined />}
        onClick={() => onAction(item.path)}
        style={{ marginTop: 12, paddingInline: 0, color: meta.color, fontWeight: 600 }}
      >
        {item.action}
      </Button>
    </div>
  );
}

const CommandCenter: React.FC = () => {
  const navigate = useNavigate();
  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const bootstrappedRef = useRef(false);

  const loadOverview = useCallback(async (background = false) => {
    if (background) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const next = await fetchPlatformOverview();
      setOverview(next);
      setError('');
    } catch (err) {
      const message = err instanceof Error ? err.message : '加载数据中台总览失败';
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  usePolling(() => {
    if (!bootstrappedRef.current) {
      bootstrappedRef.current = true;
      void loadOverview(false);
      return;
    }
    void loadOverview(true);
  }, 20000, true);

  const summary = overview?.summary;

  return (
    <div
      style={{
        minHeight: 'calc(100vh - 52px)',
        padding: 24,
        borderRadius: 8,
        background: `
          radial-gradient(circle at top left, rgba(138, 180, 255, 0.18), transparent 34%),
          radial-gradient(circle at top right, rgba(101, 213, 163, 0.12), transparent 28%),
          radial-gradient(circle at bottom left, rgba(255, 122, 122, 0.12), transparent 30%),
          ${palette.bg}
        `,
        color: palette.text,
        display: 'flex',
        flexDirection: 'column',
        gap: 22,
      }}
    >
      <section
        style={{
          borderRadius: 8,
          border: `1px solid ${palette.border}`,
          background: 'linear-gradient(140deg, rgba(33, 37, 46, 0.96) 0%, rgba(23, 26, 34, 0.98) 100%)',
          boxShadow: palette.shadow,
          padding: 24,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: 24,
        }}
      >
        <div>
          <Space size={8} wrap>
            <Tag color="processing">AI Collect Style</Tag>
            <Tag color="success">Crawler / Downloader / Syncer / ETL</Tag>
            <Tag color="blue">Unified Mission Control</Tag>
          </Space>

          <Title level={1} style={{ color: palette.text, margin: '14px 0 0', fontSize: 34, lineHeight: 1.18 }}>
            全智能 AI 数据中台
          </Title>
          <Text style={{ display: 'block', marginTop: 14, maxWidth: 760, color: palette.muted, lineHeight: 1.8, fontSize: 15 }}>
            用一个界面承接智能采集、资源下载、Kafka 同步和六层 ETL 加工。首页看全链路健康度，工作台负责发起采集与治理策略。
          </Text>

          <Space size={10} style={{ marginTop: 22 }} wrap>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={() => navigate('/scout')}
              style={{ background: palette.accent, borderColor: palette.accent, color: '#111827' }}
            >
              发起智能采集
            </Button>
            <Button
              icon={<FieldTimeOutlined />}
              onClick={() => navigate('/tasks')}
              style={{ background: 'transparent', borderColor: palette.border, color: palette.text }}
            >
              查看任务编排
            </Button>
            <Button
              icon={<BranchesOutlined />}
              onClick={() => navigate('/flow')}
              style={{ background: 'transparent', borderColor: palette.border, color: palette.text }}
            >
              打开 ETL 管道
            </Button>
          </Space>
        </div>

        <div
          style={{
            ...panelStyle,
            padding: 20,
            background: 'linear-gradient(180deg, rgba(138, 180, 255, 0.14) 0%, rgba(29, 33, 41, 0.88) 42%)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
            <div>
              <Text style={{ color: palette.subtle, fontSize: 12 }}>全链路健康度</Text>
              <div style={{ marginTop: 8, color: palette.text, fontSize: 28, fontWeight: 800 }}>
                {summary?.healthScore ?? '--'}
              </div>
            </div>
            <Progress
              type="circle"
              percent={summary?.healthScore ?? 0}
              strokeColor={palette.accent}
              trailColor="rgba(255,255,255,0.08)"
              size={92}
              format={(percent) => <span style={{ color: palette.text, fontWeight: 700 }}>{percent}</span>}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 18 }}>
            <div>
              <Text style={{ color: palette.subtle, fontSize: 12 }}>健康服务</Text>
              <div style={{ marginTop: 6, color: palette.text, fontSize: 20, fontWeight: 700 }}>
                {summary?.healthyStageCount ?? '--'} / 4
              </div>
            </div>
            <div>
              <Text style={{ color: palette.subtle, fontSize: 12 }}>活跃任务</Text>
              <div style={{ marginTop: 6, color: palette.text, fontSize: 20, fontWeight: 700 }}>
                {summary?.liveTaskCount ?? '--'}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text style={{ color: palette.subtle, fontSize: 12 }}>
              {overview ? `更新时间 ${formatTime(overview.updatedAt)}` : '等待总览数据'}
            </Text>
            <Button
              type="text"
              icon={refreshing ? <Spin size="small" /> : <ReloadOutlined />}
              onClick={() => void loadOverview(true)}
              style={{ color: palette.muted }}
            >
              刷新
            </Button>
          </div>
        </div>
      </section>

      {error ? (
        <Alert
          message="总览加载异常"
          description={error}
          type="warning"
          showIcon
          style={{ borderRadius: 8 }}
        />
      ) : null}

      {loading && !overview ? (
        <div style={{ ...panelStyle, minHeight: 280, display: 'grid', placeItems: 'center' }}>
          <Spin size="large" />
        </div>
      ) : null}

      {overview ? (
        <>
          <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
            {[
              { label: '采集模板', value: overview.summary.templateCount, hint: '已沉淀模板资产', icon: <RobotOutlined />, color: palette.accent },
              { label: '源站域名', value: overview.summary.sourceCount, hint: '真实 base_url 去重', icon: <GlobalOutlined />, color: palette.success },
              { label: '活跃任务', value: overview.summary.liveTaskCount, hint: '运行中与排队中的任务', icon: <ClockCircleOutlined />, color: palette.warning },
              { label: '数据主题', value: overview.summary.dataDomainCount, hint: '模板声明的数据域', icon: <ApiOutlined />, color: palette.danger },
            ].map((item) => (
              <div key={item.label} style={statCardStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <Text style={{ color: palette.subtle, fontSize: 12 }}>{item.label}</Text>
                  <span style={{ color: item.color, fontSize: 18 }}>{item.icon}</span>
                </div>
                <div style={{ marginTop: 14, color: palette.text, fontSize: 28, fontWeight: 800 }}>{item.value}</div>
                <Text style={{ display: 'block', marginTop: 8, color: palette.muted }}>{item.hint}</Text>
              </div>
            ))}
          </section>

          <section>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <div>
                <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>运行链路</Title>
                <Text style={{ color: palette.subtle }}>按当前服务实现逻辑，对 crawler、downloader、syncer、etl 进行统一展示。</Text>
              </div>
              <Tag color="processing">End-to-End</Tag>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
              {overview.stages.map((stage) => <StageCard key={stage.key} stage={stage} />)}
            </div>
          </section>

          <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            <div style={panelStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 14 }}>
                <div>
                  <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>数据抓取矩阵</Title>
                  <Text style={{ color: palette.subtle }}>按模板声明的数据域聚合，帮助快速识别当前抓取版图。</Text>
                </div>
                <Button
                  type="text"
                  icon={<RadarChartOutlined />}
                  onClick={() => navigate('/source-strategy')}
                  style={{ color: palette.accent }}
                >
                  源站治理
                </Button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                {overview.sources.map((source) => <SourceCard key={source.key} source={source} />)}
              </div>
            </div>

            <div style={panelStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 8 }}>
                <div>
                  <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>任务面板</Title>
                  <Text style={{ color: palette.subtle }}>
                    {overview.taskBoard.some((item) => item.kind === 'live')
                      ? '优先展示当前运行中的任务。'
                      : '当前没有 live task，展示模板侧的推荐编排。'}
                  </Text>
                </div>
                <Button
                  type="text"
                  icon={<PlayCircleOutlined />}
                  onClick={() => navigate('/tasks')}
                  style={{ color: palette.accent }}
                >
                  任务中心
                </Button>
              </div>
              <div style={{ display: 'grid' }}>
                {overview.taskBoard.map((task) => <TaskRow key={task.id} task={task} />)}
              </div>
            </div>
          </section>

          <section style={panelStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <div>
                <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>ETL 分层资产</Title>
                <Text style={{ color: palette.subtle }}>直接映射当前 `app.etl.main` 的分层 Worker 与 Kafka 主题流向。</Text>
              </div>
              <Button
                type="text"
                icon={<ExperimentOutlined />}
                onClick={() => navigate('/flow')}
                style={{ color: palette.accent }}
              >
                打开管道画布
              </Button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
              {overview.etlLayers.map((layer) => <EtlLayerCard key={layer.key} layer={layer} />)}
            </div>
          </section>

          <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            <div style={panelStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 14 }}>
                <div>
                  <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>策略与门禁</Title>
                  <Text style={{ color: palette.subtle }}>基于当前 AI Collect scope 与 settings，展示发布前可控边界。</Text>
                </div>
                <Button
                  type="text"
                  icon={<SafetyCertificateOutlined />}
                  onClick={() => navigate('/anti-crawl')}
                  style={{ color: palette.accent }}
                >
                  查看策略
                </Button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                {overview.guardrails.map((item) => <GuardrailCard key={item.key} item={item} />)}
              </div>
            </div>

            <div style={panelStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 14 }}>
                <div>
                  <Title level={3} style={{ color: palette.text, margin: 0, fontSize: 20 }}>AI 运维建议</Title>
                  <Text style={{ color: palette.subtle }}>结合当前模板、服务依赖与任务状态，给出下一步动作。</Text>
                </div>
                <Tag color="processing">Actionable</Tag>
              </div>
              <div style={{ display: 'grid', gap: 12 }}>
                {overview.recommendations.map((item) => (
                  <RecommendationCard key={item.title} item={item} onAction={(path) => navigate(path)} />
                ))}
              </div>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
};

export default CommandCenter;
