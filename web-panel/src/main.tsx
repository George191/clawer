import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/global.css';

const bootOrbitTime = document.querySelector('.sp-auth-boot .sp-auth-loading-satellite')?.getAnimations()[0]?.currentTime;
document.documentElement.style.setProperty('--sp-auth-orbit-epoch', String(performance.now() - (typeof bootOrbitTime === 'number' ? bootOrbitTime : 0)));

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
