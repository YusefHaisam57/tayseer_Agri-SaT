# ============================================================
# telegram_integration.py — Tayseer Agri-SaT Telegram Bot
#
# Responsibilities:
#   • Telegram Bot API service (webhook mode)
#   • Message, command, and callback handlers
#   • Egyptian dialect NLP integration
#   • Proactive daily farm updates
# ============================================================

import os
import json
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ╔═══════════════════════════════════════════════════════════╗
# ║  TelegramBotService                                      ║
# ╚═══════════════════════════════════════════════════════════╝

class TelegramBotService:
    """
    Lightweight Telegram Bot API client (no external library needed).
    Uses the official Bot API via HTTPS.
    """

    def __init__(self):
        self.token       = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.username    = os.getenv("TELEGRAM_BOT_USERNAME", "TayseerAgriBot")
        self.enabled     = bool(self.token)
        self.api_base    = f"https://api.telegram.org/bot{self.token}" if self.token else ""

        if self.enabled:
            log.info(f"[Telegram] Bot @{self.username} initialised")
        else:
            log.warning("[Telegram] TELEGRAM_BOT_TOKEN not set — demo mode")

    # ── Webhook management ─────────────────────────────────────

    def set_webhook(self, webhook_url: str) -> dict:
        """Register the webhook URL with Telegram."""
        if not self.enabled:
            return {"ok": False, "error": "Token not configured"}
        try:
            r = requests.post(f"{self.api_base}/setWebhook", json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=10)
            result = r.json()
            if result.get("ok"):
                log.info(f"[Telegram] Webhook set: {webhook_url}")
            else:
                log.warning(f"[Telegram] Webhook failed: {result.get('description')}")
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_webhook(self) -> dict:
        if not self.enabled:
            return {"ok": False}
        r = requests.post(f"{self.api_base}/deleteWebhook", timeout=10)
        return r.json()

    def get_webhook_info(self) -> dict:
        if not self.enabled:
            return {}
        r = requests.get(f"{self.api_base}/getWebhookInfo", timeout=10)
        return r.json()

    # ── Sending messages ───────────────────────────────────────

    def send_message(self, chat_id: int | str, text: str,
                     parse_mode: str = "Markdown",
                     reply_markup: dict = None) -> dict:
        if not self.enabled:
            log.debug(f"[Telegram-Demo] → {chat_id}: {text[:60]}...")
            return {"ok": True, "demo": True}

        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        try:
            r = requests.post(f"{self.api_base}/sendMessage",
                              json=payload, timeout=10)
            return r.json()
        except Exception as exc:
            log.error(f"[Telegram] send_message error: {exc}")
            return {"ok": False, "error": str(exc)}

    def send_photo(self, chat_id: int | str,
                   photo_url: str, caption: str = None) -> dict:
        payload = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
            payload["parse_mode"] = "Markdown"
        try:
            r = requests.post(f"{self.api_base}/sendPhoto", json=payload, timeout=10)
            return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def send_document(self, chat_id: int | str,
                      document_url: str, caption: str = None) -> dict:
        payload = {"chat_id": chat_id, "document": document_url}
        if caption:
            payload["caption"] = caption
        try:
            r = requests.post(f"{self.api_base}/sendDocument", json=payload, timeout=10)
            return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def send_location(self, chat_id, lat: float, lon: float) -> dict:
        try:
            r = requests.post(f"{self.api_base}/sendLocation",
                              json={"chat_id": chat_id, "latitude": lat, "longitude": lon},
                              timeout=10)
            return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def answer_callback_query(self, callback_id: str,
                              text: str = None, show_alert: bool = False) -> dict:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        try:
            r = requests.post(f"{self.api_base}/answerCallbackQuery",
                              json=payload, timeout=10)
            return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Keyboard helpers ───────────────────────────────────────

    @staticmethod
    def inline_keyboard(buttons: list) -> dict:
        """buttons = list[list[{text, callback_data}]]"""
        return {"inline_keyboard": buttons}

    @staticmethod
    def reply_keyboard(rows: list, resize: bool = True, one_time: bool = False) -> dict:
        """rows = list[list[str]]"""
        return {
            "keyboard": [[{"text": btn} for btn in row] for row in rows],
            "resize_keyboard": resize,
            "one_time_keyboard": one_time,
        }

    @staticmethod
    def remove_keyboard() -> dict:
        return {"remove_keyboard": True}


# ╔═══════════════════════════════════════════════════════════╗
# ║  Message / Command Builders                              ║
# ╚═══════════════════════════════════════════════════════════╝

def build_start_message(chat_id: int) -> str:
    return (
        "👋 *أهلاً بك في Tayseer Agri-SaT!*\n\n"
        "🌾 مساعدك الزراعي الذكي — مدعوم بالأقمار الصناعية\n\n"
        "📱 *للبدء:*\n"
        "1️⃣ ربط حسابك: `/link your@email.com`\n"
        "2️⃣ اسأل أي سؤال بالعامية المصرية\n\n"
        "💡 *أمثلة:*\n"
        "• إيه حالة المحصول؟\n"
        "• أروي النهاردة؟\n"
        "• فيه عطل في البيفوت؟\n\n"
        f"🆔 Telegram ID بتاعك: `{chat_id}`\n\n"
        "استخدم /help لعرض جميع الأوامر"
    )


def build_help_message() -> str:
    return (
        "❓ *أوامر البوت*\n\n"
        "📱 *الأوامر الأساسية:*\n"
        "/start — بدء المحادثة\n"
        "/link email — ربط حسابك\n"
        "/status — حالة المزرعة\n"
        "/irrigate — توصية الري\n"
        "/pivot — حالة البيفوت\n"
        "/weather — توقعات الطقس\n"
        "/help — هذه الرسالة\n\n"
        "💬 *أسئلة حرة بالعربي:*\n"
        "• إيه حالة المحصول؟\n"
        "• أروي النهاردة؟\n"
        "• كام ساعة أشغل الطلمبة؟\n"
        "• فيه عطل في البيفوت؟\n"
        "• الجو عامل إيه؟"
    )


def build_status_message(user: dict, fd: dict) -> str:
    fn     = user.get("farm_name") or "مزرعتك"
    nd     = fd.get("ndvi",       0.65)
    et     = fd.get("et",         4.5)
    ph     = fd.get("pump_hours", 6.0)
    st     = fd.get("status",     "healthy")
    icon   = "✅" if st == "healthy" else ("⚠️" if st == "warning" else "🔴")
    st_ar  = {"healthy": "ممتاز", "warning": "متوسط"}.get(st, st)
    irr    = (f"💧 شغّل الطلمبة {ph:.1f} ساعة اليوم."
              if nd < 0.5 else "✅ لا يحتاج ري اليوم.")

    return (
        f"🌾 *{fn}*\n\n"
        f"{icon} الحالة: {st_ar}\n"
        f"• NDVI: {nd:.3f}\n"
        f"• ET: {et:.2f} مم/يوم\n"
        f"• ساعات الري: {ph:.1f}\n\n"
        f"{irr}\n\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    )


def build_irrigation_message(user: dict, fd: dict) -> str:
    fn  = user.get("farm_name") or "مزرعتك"
    et  = fd.get("et",         4.5)
    ph  = fd.get("pump_hours", 6.0)
    nd  = fd.get("ndvi",       0.65)
    lst = fd.get("lst",        35.0)

    return (
        f"💧 *توصية الري — {fn}*\n\n"
        f"⏰ شغّل الطلمبة: *{ph:.1f} ساعة*\n"
        f"📊 ET: {et:.2f} مم/يوم | NDVI: {nd:.2f}\n\n"
        f"⏰ أفضل وقت: الليل (9م – 3ص)\n"
        + (f"\n⚠️ تجنب الري وقت الظهيرة — LST: {lst:.0f}°م" if lst > 38 else "")
    )


def build_pivot_message(user: dict, fd: dict) -> str:
    fn = user.get("farm_name") or "مزرعتك"
    nd = fd.get("ndvi_cv", 0.12)
    ok = nd < 0.20
    return (
        f"🔧 *صحة البيفوت — {fn}*\n\n"
        + ("✅ جميع الأبراج تعمل بشكل طبيعي.\n"
           if ok else "⚠️ تباين NDVI مرتفع — راجع الأبراج.\n")
        + f"📊 NDVI CV: {nd:.2f}\n\n"
        "🌐 تفاصيل CM60: tayseer.app/map-view"
    )


def build_daily_update(user: dict, fd: dict) -> str:
    fn  = user.get("farm_name") or "مزرعتك"
    nd  = fd.get("ndvi",       0.65)
    et  = fd.get("et",         4.5)
    ph  = fd.get("pump_hours", 6.0)
    st  = fd.get("status",     "healthy")
    icon = "✅" if st == "healthy" else "⚠️"
    st_ar = {"healthy": "ممتاز", "warning": "متوسط"}.get(st, st)
    return (
        f"🌅 *صباح الخير!*\n\n"
        f"🌾 تحديث يومي — {fn}\n"
        f"📅 {datetime.now().strftime('%A %d/%m/%Y')}\n\n"
        f"{icon} الحالة: {st_ar}\n"
        f"• NDVI: {nd:.2f} | ET: {et:.2f} مم\n\n"
        + (f"💧 أشغل الطلمبة {ph:.1f} ساعة اليوم." if nd < 0.5 else "✅ لا ري مطلوب اليوم.")
        + "\n\nاستخدم /status للتفاصيل الكاملة"
    )


# ── Quick-action inline keyboard ──────────────────────────────
QUICK_KEYBOARD = TelegramBotService.inline_keyboard([
    [
        {"text": "💧 أروي؟",     "callback_data": "irrigate"},
        {"text": "🌿 المحصول",   "callback_data": "crop_status"},
    ],
    [
        {"text": "🔧 البيفوت",   "callback_data": "pivot"},
        {"text": "🌤️ الطقس",    "callback_data": "weather"},
    ],
])


# ── Singleton ─────────────────────────────────────────────────
_tg_svc: TelegramBotService = None

def get_telegram_service() -> TelegramBotService:
    global _tg_svc
    if _tg_svc is None:
        _tg_svc = TelegramBotService()
    return _tg_svc
