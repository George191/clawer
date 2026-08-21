import React from 'react';
import { Typography } from 'antd';
import { LockOutlined, MailOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useLocation } from 'react-router-dom';

const AuthLayout: React.FC<React.PropsWithChildren> = ({ children }) => {
  const isRecovery = useLocation().pathname === '/reset-password';

  return (
    <div className="sp-login">
      <div className="sp-login-graph" aria-hidden="true">
        <div className="sp-login-grid" />
        <div className="sp-login-orbit-stage">
          <div className="sp-login-orbit sp-login-orbit-one"><i /><i /><i /></div>
          <div className="sp-login-orbit sp-login-orbit-two"><i /><i /><i /></div>
          <div className="sp-login-orbit sp-login-orbit-three"><i /><i /></div>
          <div className="sp-login-orbit-core"><span /><span /></div>
        </div>
        <div className="sp-login-copy" key={isRecovery ? 'recovery-copy' : 'login-copy'}>
          <Typography.Text className="sp-login-brand">ASIRAL HELIO</Typography.Text>
          <Typography.Title level={1}>{isRecovery ? 'Secure Identity Recovery' : 'Unified Data Intelligence Platform'}</Typography.Title>
          <Typography.Paragraph>{isRecovery ? 'Restore access to your unified data intelligence workspace through an auditable verification flow.' : 'Connect collection, lakehouse, ETL, AI and geospatial data in one observable and governed platform.'}</Typography.Paragraph>
          {!isRecovery && <div className="sp-login-domain-list"><span>Data Collection</span><span>Data Lake</span><span>ETL</span><span>GIS Cockpit</span><span>AI Workspace</span></div>}
          <div className="sp-login-capabilities">
            {isRecovery ? <><span><SafetyCertificateOutlined /> Time-limited verification</span><span><LockOutlined /> Password-safe recovery</span><span><MailOutlined /> Enterprise email identity</span></> : <><span><SafetyCertificateOutlined /> Tenant isolation</span><span><LockOutlined /> Auditable operations</span><span><MailOutlined /> Enterprise identity</span></>}
          </div>
        </div>
      </div>
      <div className={`sp-login-panel${isRecovery ? ' sp-reset-password' : ''}`}>
        {children}
      </div>
    </div>
  );
};

export default AuthLayout;
