/// <reference types="vite/client" />

interface Window {
  __APP_CONFIG__?: { backendDomainSuffix: string; httpsPort: string; frontendDomainSuffix: string };
}
