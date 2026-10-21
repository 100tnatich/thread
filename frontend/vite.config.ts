import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

import { lirePort } from './lire-env.ts'

export default defineConfig(({ command }) => ({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'Thread — révisions',
        short_name: 'Thread',
        description: 'Révisions à répétition espacée pilotées par les dates de test',
        lang: 'fr',
        start_url: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#0B0D12',
        theme_color: '#0B0D12',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // L'API ne doit jamais être servie depuis le cache: les questions du
        // jour et l'état FSRS doivent toujours venir du serveur.
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  // Les ports ne sont lus qu'en dev (`vite`), pas au build Docker (pas de .env dans l'image).
  // Formule 52NN / 81NN (conventions §3.2). PORT_WEB / PORT_API de l'environnement l'emportent
  // sur .env: c'est ainsi que les tests E2E visent leur backend jetable.
  server:
    command === 'serve'
      ? {
          port: lirePort('PORT_WEB'),
          strictPort: true, // jamais de repli silencieux sur un autre port
          host: process.env.HOST ?? '127.0.0.1',
          proxy: {
            '/api': {
              target: `http://127.0.0.1:${lirePort('PORT_API')}`,
              changeOrigin: true,
            },
          },
        }
      : undefined,
}))
