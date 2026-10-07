# ============================================================
# whatsapp_integration.py — Tayseer Agri-SaT WhatsApp Service
#
# Responsibilities:
#   • Twilio WhatsApp send/receive
#   • Egyptian NLP intent detection
#   • Proactive alerts (water stress, pivot failure, weather)
#   • Daily scheduled updates (7 AM)
# ============================================================

import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ── Twilio availability check ─────────────────────────────────
try:
    from twilio.rest import Client as TwilioClient
    from twilio.twiml.messaging_response import MessagingResponse
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    log.warning("[WhatsApp] twilio package not installed — service disabled.")


# ╔═══════════════════════════════════════════════════════════╗
# ║  TwilioWhatsAppService                                   ║
# ╚═══════════════════════════════════════════════════════════╝

class TwilioWhatsAppService:
    """
    Production Twilio WhatsApp integration.
    Reads credentials from environment / .env file.
    """

    def __init__(self):
        self.account_sid    = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token     = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.whatsapp_from  = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
        self.sandbox_join   = os.getenv("TWILIO_SANDBOX_JOIN", "join tayseer-demo")

        if TWILIO_AVAILABLE and self.account_sid and self.auth_token:
            self.client  = TwilioClient(self.account_sid, self.auth_token)
            self.enabled = True
            log.info("[WhatsApp] Twilio client initialised ✅")
        else:
            self.client  = None
            self.enabled = False
            log.warning("[WhatsApp] Twilio not configured — demo mode.")

    # ── Send text / media message ──────────────────────────────

    def send_message(self, to_number: str, body: str, media_url: str = None) -> dict:
        """
        Send a WhatsApp message via Twilio.

        Args:
            to_number:  E.164 number, e.g. '+201234567890'  (no whatsapp: prefix needed here)
            body:       Text content
            media_url:  Optional public URL of image / PDF

        Returns: {'success': bool, 'sid': str}
        """
        if not self.enabled:
            log.debug(f"[WhatsApp-Demo] → {to_number}: {body[:60]}...")
            return {"success": True, "sid": "DEMO", "demo": True}

        # Normalise number format
        to_wa = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"

        try:
            params = {"from_": self.whatsapp_from, "to": to_wa, "body": body}
            if media_url:
                params["media_url"] = [media_url]
            msg = self.client.messages.create(**params)
            log.info(f"[WhatsApp] Sent {msg.sid} → {to_number}")
            return {"success": True, "sid": msg.sid, "status": msg.status}
        except Exception as exc:
            log.error(f"[WhatsApp] Send failed → {to_number}: {exc}")
            return {"success": False, "error": str(exc)}

    # ── Build TwiML response (for Flask webhook) ───────────────

    @staticmethod
    def build_twiml(message: str, media_url: str = None) -> str:
        """Return a TwiML XML string for Twilio webhook reply."""
        if TWILIO_AVAILABLE:
            resp = MessagingResponse()
            msg  = resp.message(message)
            if media_url:
                msg.media(media_url)
            return str(resp)
        # Fallback — plain XML
        escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'


# ╔═══════════════════════════════════════════════════════════╗
# ║  EgyptianNLP — Intent Detection & Response Generator     ║
# ╚═══════════════════════════════════════════════════════════╝

class EgyptianNLP:
    """
    Lightweight keyword-based NLP tuned for Egyptian agricultural dialect.
    Context: farm_data dict from get_latest_farm_data().
    """

    INTENTS = {
        "crop_status":   ["حال", "حالة", "صحة", "محصول", "زرع", "نبات", "ndvi", "status"],
        "irrigation":    ["أروي", "روي", "ري", "مياه", "طلمبة", "شغل", "irrigat", "pump",
                          "ساعات", "وقت الري", "irrigation", "أشغل"],
        "pivot_health":  ["بيفوت", "برج", "عطل", "خراب", "ميكانيكي", "كهرب", "pivot", "fault"],
        "weather":       ["جو", "طقس", "حرارة", "مطر", "رياح", "weather", "temp"],
        "biomass":       ["انتاج", "إنتاج", "محصول", "حصاد", "biomass", "كتلة"],
        "water_stress":  ["اجهاد", "إجهاد", "جفاف", "ضغط مائي", "stress"],
        "alerts":        ["تنبيه", "تحذير", "خطر", "alert", "warning"],
        "help":          ["مساعدة", "مساعده", "help", "أسئلة", "ايه ممكن", "إيه ممكن"],
    }

    def __init__(self, farm_data: dict = None):
        self.fd = farm_data or {}

    def detect_intent(self, text: str) -> str:
        text_lower = text.lower()
        for intent, keywords in self.INTENTS.items():
            if any(kw in text_lower for kw in keywords):
                return intent
        return "general"

    def generate_response(self, intent: str, farm_name: str = "مزرعتك") -> str:
        fd = self.fd
        ndvi       = fd.get("ndvi",       0.65)
        et         = fd.get("et",         4.5)
        pump_hours = fd.get("pump_hours", 6.0)
        lst        = fd.get("lst",        35.0)
        biomass    = fd.get("biomass",    2.5)
        status     = fd.get("status",     "healthy")

        status_emoji = "✅" if status == "healthy" else ("⚠️" if status == "warning" else "🔴")
        status_ar    = {"healthy": "ممتازة", "warning": "متوسطة", "water_stress": "إجهاد مائي", "heat_stress": "إجهاد حراري"}.get(status, status)

        if intent == "crop_status":
            return (
                f"🌾 *صحة المحصول — {farm_name}*\n\n"
                f"{status_emoji} الحالة: {status_ar}\n"
                f"• NDVI: {ndvi:.2f}\n"
                f"• درجة حرارة السطح: {lst:.1f}°م\n"
                f"• الكتلة الحيوية: {biomass:.2f} ط/هكتار\n\n"
                + ("💧 يُنصح بالري خلال 24 ساعة." if ndvi < 0.45 else "✅ لا يحتاج تدخل فوري.")
            )

        elif intent == "irrigation":
            return (
                f"💧 *توصية الري — {farm_name}*\n\n"
                f"⏰ شغّل الطلمبة: *{pump_hours:.1f} ساعة*\n"
                f"📊 ET الفعلي: {et:.2f} مم/يوم\n"
                f"⏰ أفضل وقت: الليل (9م – 3ص)\n\n"
                + ("⚠️ تجنب الري وقت الظهيرة — تبخر عالي!" if lst > 38 else "")
            )

        elif intent == "pivot_health":
            return (
                f"🔧 *صحة البيفوت — {farm_name}*\n\n"
                f"استخدم المنصة لتفاصيل CM60:\n"
                f"🌐 https://tayseer.app/map-view\n\n"
                f"📡 تقرير CM60 اليومي يصلك الساعة 7 ص."
            )

        elif intent == "weather":
            return (
                f"🌤️ *حالة الجو — {farm_name}*\n\n"
                f"🌡️ درجة حرارة السطح: {lst:.1f}°م\n"
                f"💧 ET: {et:.2f} مم/يوم\n\n"
                f"للتفاصيل الكاملة: 🌐 tayseer.app/dashboard"
            )

        elif intent == "biomass":
            return (
                f"🌾 *الإنتاجية — {farm_name}*\n\n"
                f"📊 الكتلة الحيوية: {biomass:.2f} ط/هكتار\n"
                f"NDVI: {ndvi:.2f}\n\n"
                + ("✅ إنتاجية جيدة." if ndvi > 0.55 else "🧪 يُنصح بجرعة تسميد نيتروجيني.")
            )

        elif intent == "water_stress":
            stress = max(0, (0.5 - ndvi) / 0.5 * 100) if ndvi < 0.5 else 0
            return (
                f"💧 *الإجهاد المائي — {farm_name}*\n\n"
                f"📊 مستوى الإجهاد: {stress:.0f}%\n"
                f"NDVI: {ndvi:.2f}\n\n"
                + (f"🚨 إجهاد مائي! شغّل الطلمبة {pump_hours:.1f} ساعة فوراً." if stress > 30
                   else "✅ لا يوجد إجهاد مائي حالياً.")
            )

        elif intent == "alerts":
            return (
                f"🔔 *التنبيهات النشطة — {farm_name}*\n\n"
                + (f"🔴 إجهاد مائي — راجع نظام الري" if ndvi < 0.4 else "✅ لا توجد تنبيهات حرجة حالياً.")
                + f"\n\nللتفاصيل: 🌐 tayseer.app/dashboard"
            )

        elif intent == "help":
            return (
                f"🌾 *مساعد تيسير — {farm_name}*\n\n"
                f"يمكنك أن تسألني:\n"
                f"• إيه حالة المحصول؟\n"
                f"• أروي النهاردة؟\n"
                f"• كام ساعة أشغل الطلمبة؟\n"
                f"• فيه عطل في البيفوت؟\n"
                f"• الجو عامل إيه؟\n"
                f"• كام الإنتاجية؟\n"
                f"• فيه تنبيهات?"
            )

        else:
            return (
                f"🌾 *Tayseer — {farm_name}*\n\n"
                f"الحالة الحالية: {status_emoji} {status_ar}\n"
                f"NDVI: {ndvi:.2f} | ET: {et:.2f} مم\n\n"
                f"ابعت «مساعدة» لقائمة الأسئلة المتاحة."
            )


# ╔═══════════════════════════════════════════════════════════╗
# ║  Proactive Alert Messages                                ║
# ╚═══════════════════════════════════════════════════════════╝

def build_daily_summary(user: dict, farm_data: dict) -> str:
    """Format a friendly morning update message."""
    nd  = farm_data.get("ndvi", 0.65)
    et  = farm_data.get("et",   4.5)
    ph  = farm_data.get("pump_hours", 6.0)
    st  = farm_data.get("status", "healthy")
    fn  = user.get("farm_name") or "مزرعتك"

    icon = "✅" if st == "healthy" else ("⚠️" if st == "warning" else "🔴")
    st_ar = {"healthy": "ممتاز", "warning": "متوسط", "water_stress": "إجهاد مائي"}.get(st, st)

    irr_line = (f"💧 أشغل الطلمبة {ph:.1f} ساعة النهاردة."
                if nd < 0.5 else "✅ الري مش مطلوب النهاردة.")

    return (
        f"🌅 *تحديث الصبح — {fn}*\n"
        f"📅 {datetime.now().strftime('%A %d/%m/%Y')}\n\n"
        f"{icon} الحالة: {st_ar}\n"
        f"• NDVI: {nd:.2f}\n"
        f"• ET: {et:.2f} مم/يوم\n\n"
        f"{irr_line}\n\n"
        f"💬 ابعت أي سؤال عن مزرعتك!"
    )


def build_pivot_alert(pivot_id: str, tower_num: int, fault_type: str, action: str) -> str:
    icon = "🔴" if fault_type == "electrical" else "🔧"
    ft   = "كهربائي" if fault_type == "electrical" else "ميكانيكي"
    return (
        f"{icon} *تحذير عاجل — البيفوت {pivot_id}*\n\n"
        f"برج رقم {tower_num}: عطل {ft}\n"
        f"📝 {action}\n"
        f"⏰ {datetime.now().strftime('%I:%M %p')}"
    )


def build_water_stress_alert(ndvi: float, stress_pct: float, pump_hours: float) -> str:
    return (
        f"⚠️ *إجهاد مائي!*\n\n"
        f"NDVI: {ndvi:.2f}\n"
        f"🌡️ {stress_pct:.0f}% من المحصول يحتاج ري\n\n"
        f"💧 شغّل الطلمبة {pump_hours:.1f} ساعة فوراً"
    )


def build_weather_alert(warning: str, temp_min: float, temp_max: float, recommendation: str) -> str:
    return (
        f"🌧️ *تحذير طقس!*\n\n"
        f"{warning}\n"
        f"🌡️ {temp_min:.0f}°م — {temp_max:.0f}°م\n\n"
        f"💡 {recommendation}"
    )


# ── Singleton ─────────────────────────────────────────────────
_svc: TwilioWhatsAppService = None

def get_whatsapp_service() -> TwilioWhatsAppService:
    global _svc
    if _svc is None:
        _svc = TwilioWhatsAppService()
    return _svc
