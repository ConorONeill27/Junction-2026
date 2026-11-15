import react from '@vitejs/plugin-react';
import {defineConfig} from 'vite';

const backendProxy = {
  '/api/calibration': {
    target: 'http://127.0.0.1:8765',
    changeOrigin: true,
  },
  '/api/detections': {
    target: 'http://127.0.0.1:8765',
    changeOrigin: true,
  },
  '/api/frame': {
    target: 'http://127.0.0.1:8765',
    changeOrigin: true,
  },
  '/api/notes': {
    target: 'http://127.0.0.1:8765',
    changeOrigin: true,
  },
  '/api': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
  '/ws': {
    target: 'ws://127.0.0.1:8000',
    ws: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 4173,
    allowedHosts: true,
    proxy: backendProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    allowedHosts: true,
    headers: {
      'Cache-Control': 'no-store',
    },
    proxy: backendProxy,
  },
});
