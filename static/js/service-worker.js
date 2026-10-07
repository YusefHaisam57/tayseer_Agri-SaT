const CACHE_NAME = 'tayseer-agrisat-v5.0-final-master';
const OFFLINE_URLS = [
    '/',
    '/dashboard',
    '/risks',
    '/chat',
    '/integrations',
    '/carbon',
    '/map-view',
    '/reports',
    '/static/css/style.css',
    '/static/css/custom.css',
    '/static/js/dashboard.js',
    '/static/js/charts.js',
    '/static/js/nlp-engine.js',
    '/static/js/mini-chat.js',
];

self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(OFFLINE_URLS);
        })
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    // 1. Navigation / HTML pages: Network-First so users always get fresh layout updates
    if (event.request.mode === 'navigate' || (event.request.headers.get('accept') && event.request.headers.get('accept').includes('text/html'))) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    if (response.status === 200) {
                        const clonedRes = response.clone();
                        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clonedRes));
                    }
                    return response;
                })
                .catch(() => caches.match(event.request).then(cached => {
                    if (cached) return cached;
                    return caches.match('/dashboard');
                }))
        );
        return;
    }

    // 2. API calls: Network-First with Arabic offline fallback
    if (event.request.url.includes('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    const clonedRes = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, clonedRes));
                    return response;
                })
                .catch(() => caches.match(event.request).then(cached => {
                    if (cached) return cached;
                    return new Response(JSON.stringify({
                        offline: true,
                        success: false,
                        message: '📵 أنت أوفلاين — يتم استخدام البيانات المخزنة محلياً',
                        response: 'أنت أوفلاين حالياً. البيانات المحلية جاهزة للرد على أسئلتك.'
                    }), { headers: { 'Content-Type': 'application/json' } });
                }))
        );
        return;
    }

    // 3. Static assets: Stale-While-Revalidate
    event.respondWith(
        caches.match(event.request).then(cached => {
            const networkFetch = fetch(event.request).then(response => {
                if (response.status === 200) {
                    const cloned = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, cloned));
                }
                return response;
            }).catch(() => cached);
            return cached || networkFetch;
        })
    );
});
