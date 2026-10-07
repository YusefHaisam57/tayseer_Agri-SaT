/**
 * Tayseer-AgriSaT — mini-chat.js
 * Mini-Tayseer Chat Controller
 * Manages: Service Worker registration, LocalStorage sync, UI messaging
 * Author: Tayseer Platform v4.0  |  2026
 */

(function () {
    'use strict';

    // ── Constants ────────────────────────────────────────────────────────────────
    const LS_FARM_KEY = 'tayseer_farm_data';
    const LS_SYNC_KEY = 'tayseer_last_sync';
    const SYNC_API = '/api/get-farm-summary';
    const CHAT_API = '/api/chat';

    // ── State ────────────────────────────────────────────────────────────────────
    let nlpEngine = null;
    let farmData = null;
    let _isOnline = navigator.onLine;

    // ── Service Worker Registration ───────────────────────────────────────────────
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/js/service-worker.js', { scope: '/' })
            .then(reg => {
                console.log('[Mini-Tayseer] SW registered, scope:', reg.scope);
                reg.update(); // try to get latest version
            })
            .catch(err => console.warn('[Mini-Tayseer] SW registration failed:', err));
    }

    // ── Online/Offline detection ─────────────────────────────────────────────────
    window.addEventListener('online', () => { _isOnline = true; _updateStatusBadge(); });
    window.addEventListener('offline', () => { _isOnline = false; _updateStatusBadge(); });

    function _updateStatusBadge() {
        const badge = document.getElementById('connection-status');
        if (!badge) return;
        badge.innerHTML = _isOnline
            ? '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#10b981;margin-left:4px;animation:pulse 2s infinite;"></span> متصل'
            : '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#f59e0b;margin-left:4px;"></span> أوفلاين — يعمل بالبيانات المحفوظة';
    }

    // ── LocalStorage Helpers ──────────────────────────────────────────────────────
    function storeFarmData(data) {
        try {
            localStorage.setItem(LS_FARM_KEY, JSON.stringify(data));
            localStorage.setItem(LS_SYNC_KEY, new Date().toISOString());
        } catch (e) {
            console.error('[Mini-Tayseer] localStorage write failed:', e);
        }
    }

    function getFarmData() {
        try {
            const raw = localStorage.getItem(LS_FARM_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function getLastSync() {
        return localStorage.getItem(LS_SYNC_KEY) || null;
    }

    function _updateSyncLabel() {
        const el = document.getElementById('last-sync');
        if (!el) return;
        const ts = getLastSync();
        if (!ts) { el.textContent = 'لم يتم التحديث بعد'; return; }
        try {
            const d = new Date(ts);
            el.textContent = 'آخر تحديث: ' + d.toLocaleString('ar-EG', {
                dateStyle: 'short', timeStyle: 'short'
            });
        } catch (_) { el.textContent = 'آخر تحديث: ' + ts; }
    }

    // ── Farm Data Sync ─────────────────────────────────────────────────────────────
    async function syncFarmData() {
        const btn = document.getElementById('sync-btn');
        if (btn) { btn.disabled = true; btn.textContent = '⌛ جاري التحديث…'; }

        try {
            const res = await fetch(SYNC_API);
            const data = await res.json();

            if (data.success && data.farm_data) {
                storeFarmData(data.farm_data);
                farmData = data.farm_data;
                nlpEngine = new EgyptianNLP(farmData);
                _updateSyncLabel();
                _appendMessage('✅ تم تحديث بيانات المزرعة! Mini-Tayseer جاهز للرد حتى بدون نت.', 'bot', true);
            } else {
                _appendMessage('⚠️ فشل التحديث. تأكد من الاتصال بالإنترنت.', 'bot', false);
            }
        } catch (err) {
            console.warn('[Mini-Tayseer] sync failed:', err);
            _appendMessage('❌ فشل الاتصال بالسيرفر. اتحقق من النت وحاول تاني.', 'bot', false);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '🔄 تحديث البيانات'; }
        }
    }

    // ── Message Renderer ──────────────────────────────────────────────────────────
    function _mdToHtml(text) {
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
    }

    function _appendMessage(text, type, pulse) {
        const container = document.getElementById('mini-chat-messages');
        if (!container) return;

        const isBot = type === 'bot';
        const div = document.createElement('div');
        div.style.cssText = [
            'border-radius:14px', 'padding:12px 16px', 'margin-bottom:10px',
            'font-family:Cairo,sans-serif', 'font-size:14px', 'line-height:1.7',
            'direction:rtl', 'text-align:right',
            isBot
                ? 'background:rgba(16,185,129,0.08);border-right:4px solid #10b981;color:#d1fae5;'
                : 'background:rgba(59,130,246,0.08);border-right:4px solid #3b82f6;color:#e5e7eb;',
            pulse ? 'animation:fadeIn .3s ease;' : '',
        ].join(';');

        if (!isBot) {
            // User label
            const lbl = document.createElement('p');
            lbl.style.cssText = 'color:#93c5fd;font-size:11px;font-weight:700;margin:0 0 4px;';
            lbl.textContent = '👤 أنت:';
            div.appendChild(lbl);
        } else {
            const lbl = document.createElement('p');
            lbl.style.cssText = 'color:#6ee7b7;font-size:11px;font-weight:700;margin:0 0 4px;';
            lbl.textContent = '🤖 Mini-Tayseer:';
            div.appendChild(lbl);
        }

        const p = document.createElement('p');
        p.style.margin = '0';
        p.innerHTML = _mdToHtml(text);
        div.appendChild(p);

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }

    function _appendThinking() {
        const container = document.getElementById('mini-chat-messages');
        if (!container) return null;
        const div = document.createElement('div');
        div.id = 'thinking-bubble';
        div.style.cssText = 'border-radius:14px;padding:12px 16px;margin-bottom:10px;' +
            'background:rgba(16,185,129,0.05);border-right:4px solid rgba(16,185,129,0.3);' +
            'color:#6b7280;font-size:14px;direction:rtl;text-align:right;font-family:Cairo,sans-serif;';
        div.textContent = '🤖 يفكر…';
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
        return div;
    }

    // ── Image Attachment Handling ────────────────────────────────────────────────
    let _selectedImageB64 = null;

    function handleImageSelected(input) {
        if (!input.files || !input.files[0]) return;
        const file = input.files[0];
        const reader = new FileReader();
        reader.onload = function(e) {
            _selectedImageB64 = e.target.result.split(',')[1];
            const badge = document.getElementById('image-preview-badge');
            if (badge) {
                badge.style.display = 'inline-block';
                badge.textContent = '📷 تم إرفاق صورة: ' + file.name;
            }
        };
        reader.readAsDataURL(file);
    }

    // ── Send Message ──────────────────────────────────────────────────────────────
    async function sendMessage(event) {
        if (event) event.preventDefault();

        const input = document.getElementById('mini-chat-input');
        const q = (input ? input.value : '').trim();
        if (!q && !_selectedImageB64) return;

        const displayText = q + (_selectedImageB64 ? ' 📷 [صورة مرفقة]' : '');
        _appendMessage(displayText, 'user', true);
        if (input) input.value = '';

        const thinking = _appendThinking();
        await new Promise(r => setTimeout(r, 80));

        let reply;
        const imageToSend = _selectedImageB64;
        _selectedImageB64 = null;
        const badge = document.getElementById('image-preview-badge');
        if (badge) badge.style.display = 'none';

        if (_isOnline) {
            // Online: Query full AI Core (LLM + RAG + Real Farm Context + Vision)
            try {
                const res = await fetch(CHAT_API, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: q, image: imageToSend }),
                });
                const data = await res.json();
                reply = data.response || 'معلش، لم أتمكن من الحصول على رد من السيرفر.';
            } catch (err) {
                console.warn('[Mini-Tayseer] Online AI Core fetch failed, falling back to local NLP:', err);
                if (nlpEngine) {
                    reply = nlpEngine.respond(q);
                } else {
                    reply = '⚠️ فشل الاتصال بالسيرفر. تأكد من اتصال الإنترنت.';
                }
            }
        } else if (nlpEngine) {
            // Offline: Instant local NLP rule engine
            const intent = nlpEngine.detectIntent(q);
            if (intent.type !== 'unclear') {
                reply = nlpEngine.respond(q);
            } else {
                reply = nlpEngine._unclear();
            }
        } else {
            reply = '⚠️ لا توجد بيانات محفوظة للمزرعة وأنت غير متصل بالإنترنت. يرجى الاتصال بالإنترنت والضغط على "🔄 تحديث البيانات".';
        }

        if (thinking) thinking.remove();
        _appendMessage(reply, 'bot', true);
    }

    function askQuick(q) {
        const input = document.getElementById('mini-chat-input');
        if (input) input.value = q;
        sendMessage(null);
    }

    function clearChat() {
        const container = document.getElementById('mini-chat-messages');
        if (!container) return;
        container.innerHTML = '';
        _appendMessage('تم مسح المحادثة! اسأل أي سؤال جديد. 🌾', 'bot', false);
    }

    // ── Init ──────────────────────────────────────────────────────────────────────
    function init() {
        farmData = getFarmData();
        if (farmData) {
            nlpEngine = new EgyptianNLP(farmData);
            _appendMessage(
                'أهلاً يا حاج! 🌾 بيانات مزرعتك محفوظة محلياً — أقدر أرد عليك حتى بدون نت.\n' +
                'اسأل عن: الري، المحصول، البيفوت، الطقس، أو الكتلة الحيوية.',
                'bot', false
            );
        } else {
            _appendMessage(
                'أهلاً! أنا Mini-Tayseer 🤖\n\nاضغط **"🔄 تحديث البيانات"** عشان أحمّل بيانات مزرعتك وأقدر أرد أوفلاين.',
                'bot', false
            );
        }
        _updateSyncLabel();
        _updateStatusBadge();

        // Auto-sync if online and no data or data > 6h old
        if (_isOnline) {
            const ts = getLastSync();
            const age = ts ? (Date.now() - new Date(ts).getTime()) : Infinity;
            if (age > 6 * 60 * 60 * 1000) {
                setTimeout(syncFarmData, 1500); // silently sync after 1.5s
            }
        }
    }

    // ── Public API ────────────────────────────────────────────────────────────────
    window.MiniTayseer = {
        sendMessage,
        askQuick,
        syncFarmData,
        clearChat,
        handleImageSelected,
    };

    // Boot
    if (document.readyState !== 'loading') init();
    else document.addEventListener('DOMContentLoaded', init);

})();
