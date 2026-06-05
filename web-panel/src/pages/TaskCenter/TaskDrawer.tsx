import React, { useState, useEffect } from 'react';
import {
  Drawer, Form, Input, Radio, Card, Row, Col, Button, Space, Typography,
  Steps, InputNumber, Alert, App, theme,
} from 'antd';
import {
  CheckCircleOutlined, ClockCircleOutlined, ScheduleOutlined,
  GlobalOutlined, ApiOutlined, FileTextOutlined, MessageOutlined,
  FileSearchOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useTasksStore } from '@/stores/tasks';
import type { TemplateInfo } from '@/services/types';

const { Text } = Typography;

// ── Template config (maps TemplateInfo to form fields) ──
const TEMPLATE_PARAMS: Record<string, { name: string; label: string; placeholder: string; type: 'input' | 'textarea'; required: boolean }[]> = {
  '网页采集模板': [
    { name: 'url', label: '目标 URL', placeholder: 'https://example.com/list', type: 'input', required: true },
    { name: 'selector', label: 'CSS 选择器', placeholder: '.product-item', type: 'input', required: false },
    { name: 'pagination', label: '分页规则', placeholder: '?page={page}', type: 'input', required: false },
  ],
  'API数据同步': [
    { name: 'endpoint', label: 'API 端点', placeholder: '/api/v1/products', type: 'input', required: true },
    { name: 'headers', label: 'Headers (JSON)', placeholder: '{"Authorization": "Bearer xxx"}', type: 'textarea', required: false },
    { name: 'method', label: 'HTTP 方法', placeholder: 'GET', type: 'input', required: false },
  ],
  '日志清洗管道': [
    { name: 'logPath', label: '日志文件路径', placeholder: '/var/log/nginx/access.log', type: 'input', required: true },
    { name: 'format', label: '日志格式', placeholder: 'nginx_json', type: 'input', required: false },
  ],
  '数据质量校验': [
    { name: 'targetTable', label: '目标表', placeholder: 'dwd.user_behavior', type: 'input', required: true },
    { name: 'rules', label: '校验规则 (JSON)', placeholder: '{"row_count": {"min": 100}}', type: 'textarea', required: false },
  ],
  '旧版消息采集': [
    { name: 'topic', label: 'Topic', placeholder: 'raw.web_page', type: 'input', required: true },
    { name: 'group', label: 'Consumer Group', placeholder: 'etl-collector', type: 'input', required: false },
  ],
};

const TEMPLATE_ICONS: Record<string, React.ReactNode> = {
  '网页采集模板': <GlobalOutlined />,
  'API数据同步': <ApiOutlined />,
  '日志清洗管道': <FileTextOutlined />,
  '数据质量校验': <SafetyCertificateOutlined />,
  '旧版消息采集': <MessageOutlined />,
};

const SCHEDULE_MODES = [
  { value: 'now', label: '立即执行', icon: <CheckCircleOutlined /> },
  { value: 'delay', label: '延时执行', icon: <ClockCircleOutlined /> },
  { value: 'cron', label: 'Cron 表达式', icon: <ScheduleOutlined /> },
];

// Simple cron validator
const CRON_FIELD_RANGES = [
  { min: 0, max: 59 }, { min: 0, max: 23 },
  { min: 1, max: 31 }, { min: 1, max: 12 }, { min: 0, max: 7 },
];

function validateCronField(value: string, min: number, max: number): boolean {
  if (value === '*' || value === '?') return true;
  if (value.includes('/')) {
    const [range, step] = value.split('/');
    if (!parseInt(step, 10)) return false;
    return range === '*' || validateCronField(range, min, max);
  }
  if (value.includes('-')) {
    const [start, end] = value.split('-').map(Number);
    return start >= min && end <= max && start <= end;
  }
  if (value.includes(',')) return value.split(',').every((v) => validateCronField(v.trim(), min, max));
  const num = parseInt(value, 10);
  return !isNaN(num) && num >= min && num <= max;
}

function validateCron(expr: string): boolean {
  const fields = expr.trim().split(/\s+/);
  return fields.length === 5 && fields.every((f, i) => validateCronField(f, CRON_FIELD_RANGES[i].min, CRON_FIELD_RANGES[i].max));
}

function getCronDesc(expr: string): string {
  if (!validateCron(expr)) return '无效的 Cron 表达式';
  const [min, hour, day, month, weekday] = expr.trim().split(/\s+/);
  const parts: string[] = [];
  if (weekday !== '*') {
    const days = ['日', '一', '二', '三', '四', '五', '六', '日'];
    parts.push(`每周${weekday.split(',').map((d) => days[parseInt(d, 10)]).join('、')}`);
  }
  if (month !== '*') parts.push(`${month}月`);
  if (day !== '*') parts.push(`${day}号`);
  if (hour !== '*' && min !== '*') parts.push(`${hour.padStart(2, '0')}:${min.padStart(2, '0')}`);
  else if (hour !== '*') parts.push(`${hour.padStart(2, '0')}:00`);
  if (min.includes('/')) parts.push(`每${min.split('/')[1]}分钟`);
  if (hour.includes('/')) parts.push(`每${hour.split('/')[1]}小时`);
  return parts.length > 0 ? parts.join(' ') : '每分钟执行';
}

interface TaskDrawerProps {
  open: boolean;
  onClose: () => void;
}

const TaskDrawer: React.FC<TaskDrawerProps> = ({ open, onClose }) => {
  const [form] = Form.useForm();
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('');
  const [scheduleMode, setScheduleMode] = useState<string>('now');
  const [cronExpr, setCronExpr] = useState('');
  const [cronPreview, setCronPreview] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const { templates, loading, fetchTemplates } = useTasksStore();

  useEffect(() => {
    if (open) {
      fetchTemplates();
      setCurrentStep(0);
      setScheduleMode('now');
      setCronExpr('');
      setCronPreview('');
      setSelectedTemplate('');
      form.resetFields();
    }
  }, [open, fetchTemplates, form]);

  const activeTemplates = templates.filter((t) => t.status === 'active');

  const handleNext = async () => {
    if (currentStep === 0 && !selectedTemplate) {
      message.warning('请选择一个模板');
      return;
    }
    if (currentStep === 1) {
      try { await form.validateFields(); } catch { return; }
    }
    setCurrentStep((s) => Math.min(s + 1, 2));
  };

  const handlePrev = () => setCurrentStep((s) => Math.max(s - 1, 0));

  const handleCronChange = (val: string) => {
    setCronExpr(val);
    setCronPreview(val.trim() ? getCronDesc(val.trim()) : '');
  };

  const handleSubmit = async () => {
    try {
      await form.validateFields();
    } catch { return; }
    setSubmitting(true);
    await new Promise((r) => setTimeout(r, 500));
    message.success('任务创建成功');
    form.resetFields();
    setSubmitting(false);
    onClose();
  };

  const steps = [{ title: '选择模板' }, { title: '填写参数' }, { title: '调度设置' }];

  const params = TEMPLATE_PARAMS[selectedTemplate] || [];

  return (
    <Drawer title="新建采集任务" open={open} onClose={onClose} width={520} footer={null}>
      <Steps current={currentStep} size="small" items={steps} style={{ marginBottom: 24 }} />

      <Form form={form} layout="vertical" preserve={false}>
        {/* Step 1: Select Template */}
        {currentStep === 0 && (
          <div>
            <Text type="secondary" style={{ marginBottom: 12, display: 'block' }}>选择采集模板类型</Text>
            <Row gutter={[10, 10]}>
              {activeTemplates.map((t: TemplateInfo) => (
                <Col span={12} key={t.name}>
                  <Card
                    size="small"
                    hoverable
                    onClick={() => setSelectedTemplate(t.name)}
                    style={{
                      border: selectedTemplate === t.name
                        ? `2px solid ${token.colorPrimary}`
                        : `1px solid ${token.colorBorderSecondary}`,
                      cursor: 'pointer', transition: 'all 0.2s', height: '100%',
                    }}
                    bodyStyle={{ padding: 12 }}
                  >
                    <Space direction="vertical" size={4}>
                      <Space size={6}>
                        <span style={{ fontSize: 18, color: token.colorPrimary }}>
                          {TEMPLATE_ICONS[t.name] || <FileSearchOutlined />}
                        </span>
                        <Text strong>{t.type}采集</Text>
                      </Space>
                      <Text type="secondary" style={{ fontSize: 11 }}>{t.description}</Text>
                    </Space>
                  </Card>
                </Col>
              ))}
              {activeTemplates.length === 0 && !loading && (
                <Text type="secondary" style={{ padding: 16 }}>暂无可用的模板</Text>
              )}
            </Row>
          </div>
        )}

        {/* Step 2: Parameters */}
        {currentStep === 1 && (
          <div>
            <Text type="secondary" style={{ marginBottom: 12, display: 'block' }}>
              填写 {selectedTemplate} 参数
            </Text>
            <Form.Item label="任务名称" name="name" rules={[{ required: true, message: '请输入任务名称' }]}>
              <Input placeholder="例如: 竞品价格采集-淘宝" />
            </Form.Item>
            {params.map((param) => (
              <Form.Item
                key={param.name}
                label={param.label}
                name={param.name}
                rules={param.required ? [{ required: true, message: `请输入${param.label}` }] : []}
              >
                {param.type === 'textarea' ? (
                  <Input.TextArea rows={3} placeholder={param.placeholder} />
                ) : (
                  <Input placeholder={param.placeholder} />
                )}
              </Form.Item>
            ))}
          </div>
        )}

        {/* Step 3: Schedule */}
        {currentStep === 2 && (
          <div>
            <Text type="secondary" style={{ marginBottom: 12, display: 'block' }}>设置调度方式</Text>
            <Radio.Group value={scheduleMode} onChange={(e) => setScheduleMode(e.target.value)} style={{ width: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                {SCHEDULE_MODES.map((mode) => (
                  <Card
                    key={mode.value}
                    size="small"
                    hoverable
                    onClick={() => setScheduleMode(mode.value)}
                    style={{
                      width: '100%',
                      border: scheduleMode === mode.value ? `2px solid ${token.colorPrimary}` : `1px solid ${token.colorBorderSecondary}`,
                      cursor: 'pointer', transition: 'all 0.2s',
                    }}
                    bodyStyle={{ padding: '8px 12px' }}
                  >
                    <Space>
                      <span style={{ color: token.colorPrimary }}>{mode.icon}</span>
                      <Text strong>{mode.label}</Text>
                    </Space>
                  </Card>
                ))}
              </Space>
            </Radio.Group>

            {scheduleMode === 'delay' && (
              <Form.Item label="延时 (分钟)" name="delayMinutes" rules={[{ required: true, message: '请输入延时时长' }]}
                style={{ marginTop: 16 }}>
                <InputNumber min={1} max={1440} placeholder="30" addonAfter="分钟" style={{ width: '100%' }} />
              </Form.Item>
            )}

            {scheduleMode === 'cron' && (
              <div style={{ marginTop: 16 }}>
                <Form.Item
                  label="Cron 表达式"
                  name="cronExpr"
                  rules={[
                    { required: true, message: '请输入 Cron 表达式' },
                    {
                      validator: (_, value) => {
                        if (!value) return Promise.resolve();
                        return validateCron(value.trim())
                          ? Promise.resolve()
                          : Promise.reject('无效的 Cron 表达式');
                      },
                    },
                  ]}
                >
                  <Input placeholder="0 */6 * * * (每6小时)" value={cronExpr} onChange={(e) => handleCronChange(e.target.value)} />
                </Form.Item>
                {cronPreview && cronPreview !== '无效的 Cron 表达式' && (
                  <Alert type="info" message="调度预览" description={cronPreview} style={{ marginBottom: 16 }} />
                )}
                <Text type="secondary" style={{ fontSize: 11, display: 'block' }}>格式: 分 时 日 月 星期 (空格分隔)</Text>
              </div>
            )}
          </div>
        )}
      </Form>

      {/* Footer */}
      <div style={{ marginTop: 24, padding: '16px 0 0', borderTop: `1px solid ${token.colorBorderSecondary}`, display: 'flex', justifyContent: 'space-between' }}>
        <Space>{currentStep > 0 && <Button onClick={handlePrev}>上一步</Button>}</Space>
        <Space>
          <Button onClick={onClose}>取消</Button>
          {currentStep < 2 ? (
            <Button type="primary" onClick={handleNext}>下一步</Button>
          ) : (
            <Button type="primary" onClick={handleSubmit} loading={submitting}>创建任务</Button>
          )}
        </Space>
      </div>
    </Drawer>
  );
};

export default TaskDrawer;