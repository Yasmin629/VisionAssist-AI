// Service Worker for AI Smart Currency Detection System PWA
const CACHE_NAME = 'currency-ai-pwa-v3';
const STATIC_ASSETS = [
    '/',
    '/manifest.json',
    '/static/manifest.json',
    '/static/css/product_dashboard.css',
    '/static/js/product_dashboard.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png'
];

// Install: Cache core application shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn('[SW] Core asset caching warning:', err);
            });
        })
    );
    self.skipWaiting();
});

// Activate: Clean up old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((key) => {
                    if (key !== CACHE_NAME) {
                        return caches.delete(key);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// Fetch: Network-first for dynamic API routes, Cache-first for static assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Bypass caching for all API/POST routes
    if (
        event.request.method !== 'GET' ||
        url.pathname.startsWith('/detect') ||
        url.pathname.startsWith('/chat') ||
        url.pathname.startsWith('/verify') ||
        url.pathname.startsWith('/settings') ||
        url.pathname.startsWith('/history')
    ) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) {
                // Fetch in background to keep cache fresh
                fetch(event.request).then((response) => {
                    if (response && response.status === 200) {
                        const copy = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                    }
                }).catch(() => {});
                return cached;
            }
            return fetch(event.request).catch(() => {
                // Return root shell if navigating while offline
                if (event.request.mode === 'navigate') {
                    return caches.match('/');
                }
            });
        })
    );
});
