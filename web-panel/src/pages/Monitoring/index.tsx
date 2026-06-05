import React from 'react';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import LogStream from './LogStream';
import StatsPanel from './StatsPanel';

const Monitoring: React.FC = () => {
  return (
    <ErrorBoundary>
      <PageHeader title="采集监控" />

      <div
        style={{
          display: 'flex',
          gap: 16,
          height: 'calc(100vh - 140px)',
          minHeight: 500,
        }}
      >
        {/* Left: Log Stream — takes remaining space */}
        <div style={{ flex: 2, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <LogStream />
        </div>

        {/* Right: Stats Panel — fixed width */}
        <div style={{ width: 300, flexShrink: 0 }}>
          <StatsPanel />
        </div>
      </div>
    </ErrorBoundary>
  );
};

export default Monitoring;