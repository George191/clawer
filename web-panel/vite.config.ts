import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.2:8000',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: 'ws://127.0.0.2:8000',
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      input: { main: path.resolve(__dirname, 'index.html'), helio: path.resolve(__dirname, 'helio.html') },
    },
    outDir: '../static',
    emptyOutDir: true,
  },
});
