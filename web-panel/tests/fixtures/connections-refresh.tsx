import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider, theme } from '../../src/components/ui';
import DataConnections from '../../src/pages/Pipeline/DataConnections';
import { saveSession } from '../../src/services/session';
import '../../src/styles/global.css';
import '../../src/pages/Pipeline/ontology-frame.css';
import '../../src/pages/Pipeline/ontology-workspace.css';

const now = Math.floor(Date.now() / 1000);
const token = `e30.${btoa(JSON.stringify({ sub: 'layout-test', type: 'access', iat: now, exp: now + 3600, session_exp: now + 3600 }))}.test`;
saveSession(token);
createRoot(document.getElementById('root')!).render(
  <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}><MemoryRouter initialEntries={['/ontology/connections']}>
    <DataConnections warehouseFailed={false} onRetryWarehouse={() => {}} onCancel={() => {}} />
  </MemoryRouter></ConfigProvider>,
);
