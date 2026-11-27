import React from 'react';
import ReactDOM from 'react-dom/client';
import ProjectRouter from './ProjectRouter';
import App from './App';
import './styles/global.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ProjectRouter>
      <App />
    </ProjectRouter>
  </React.StrictMode>,
);
