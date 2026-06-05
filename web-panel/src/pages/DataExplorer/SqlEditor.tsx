import React, { useRef, useCallback } from 'react';
import Editor, { type OnMount } from '@monaco-editor/react';
import { Button, Space, theme as antTheme, Tooltip, Typography } from 'antd';
import {
  PlayCircleOutlined,
  ClearOutlined,
  FormatPainterOutlined,
  HistoryOutlined,
  FieldTimeOutlined,
} from '@ant-design/icons';
import type { editor } from 'monaco-editor';

const { Text } = Typography;

interface SqlEditorProps {
  value: string;
  onChange: (val: string) => void;
  onExecute: (sql: string) => void;
  error?: string | null;
  execTime?: number | null;
}

const SqlEditor: React.FC<SqlEditorProps> = ({ value, onChange, onExecute, error, execTime }) => {
  const { token } = antTheme.useToken();
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const isDark = token.colorBgBase === 'rgb(0, 0, 0)' || token.colorBgBase === '#000000' || token.colorBgBase === '#000';

  const handleMount: OnMount = (editor) => {
    editorRef.current = editor;

    // Ctrl+Enter / Cmd+Enter to execute
    editor.addAction({
      id: 'execute-query',
      label: 'Execute Query',
      keybindings: [2048 | 3, 512 | 3],
      run: () => {
        const sql = editor.getValue();
        if (sql.trim()) onExecute(sql);
      },
    });
  };

  const handleClear = useCallback(() => {
    onChange('');
    editorRef.current?.focus();
  }, [onChange]);

  const handleFormat = useCallback(() => {
    editorRef.current?.getAction('editor.action.formatDocument')?.run();
  }, []);

  return (
    <div style={{ marginBottom: 12 }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '8px 12px', background: token.colorFillAlter,
          border: error ? `1px solid ${token.colorError}` : `1px solid ${token.colorBorder}`,
          borderBottom: 'none', borderRadius: `${token.borderRadiusLG}px ${token.borderRadiusLG}px 0 0`,
          transition: 'border-color 0.2s',
        }}
      >
        <Space size={4}>
          <Tooltip title="执行查询 (Ctrl+Enter)">
            <Button type="primary" size="small" icon={<PlayCircleOutlined />} onClick={() => onExecute(value)} disabled={!value.trim()}>
              执行
            </Button>
          </Tooltip>
          <Tooltip title="格式化 SQL (Shift+Alt+F)">
            <Button size="small" icon={<FormatPainterOutlined />} onClick={handleFormat} />
          </Tooltip>
          <Tooltip title="查询历史">
            <Button size="small" icon={<HistoryOutlined />} />
          </Tooltip>
          <Tooltip title="清空编辑器">
            <Button size="small" icon={<ClearOutlined />} onClick={handleClear} />
          </Tooltip>
        </Space>
        <Space size={12}>
          {execTime != null && (
            <Text style={{ fontSize: 12, color: token.colorTextSecondary, display: 'flex', alignItems: 'center', gap: 4 }}>
              <FieldTimeOutlined /> {execTime}ms
            </Text>
          )}
          <Text type="secondary" style={{ fontSize: 11, fontFamily: '"Fira Code", monospace' }}>SQL</Text>
        </Space>
      </div>

      {/* Monaco Editor */}
      <div
        style={{
          border: error ? `2px solid ${token.colorError}` : `1px solid ${token.colorBorder}`,
          borderTop: 'none', borderBottom: error ? 'none' : `1px solid ${token.colorBorder}`,
          borderRadius: error ? `${token.borderRadiusLG}px ${token.borderRadiusLG}px 0 0` : '0',
          overflow: 'hidden', transition: 'border-color 0.2s',
        }}
      >
        <Editor
          height="200px"
          language="sql"
          theme={isDark ? 'vs-dark' : 'vs'}
          value={value}
          onChange={(v) => onChange(v ?? '')}
          onMount={handleMount}
          options={{
            minimap: { enabled: false },
            fontSize: 13, lineNumbers: 'on', wordWrap: 'on',
            scrollBeyondLastLine: false, automaticLayout: true,
            suggest: { showKeywords: true, showSnippets: true },
            tabSize: 2, lineNumbersMinChars: 3, padding: { top: 8 },
            renderLineHighlight: 'line', cursorBlinking: 'smooth',
            smoothScrolling: true, bracketPairColorization: { enabled: true },
            matchBrackets: 'always',
          }}
          loading={
            <div style={{ padding: 24, textAlign: 'center', color: token.colorTextSecondary }}>
              编辑器加载中...
            </div>
          }
        />
      </div>

      {/* Error message */}
      {error && (
        <div
          style={{
            padding: '8px 12px', background: token.colorErrorBg,
            border: `1px solid ${token.colorErrorBorder}`, borderTop: 'none',
            borderRadius: `0 0 ${token.borderRadiusLG}px ${token.borderRadiusLG}px`,
          }}
        >
          <Text type="danger" style={{ fontSize: 12, fontFamily: '"Fira Code", monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {error}
          </Text>
        </div>
      )}
    </div>
  );
};

export default SqlEditor;