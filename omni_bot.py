# ============================================================
# omni_bot.py — Tayseer Agri-SaT Omni-Channel AI Chatbot
#
# Channels:
#   1. Web (embedded in dashboard.html via AJAX)
#   2. WhatsApp (Twilio webhook)
#   3. Telegram (python-telegram-bot webhook)
#
# The bot speaks Egyptian Farmer Arabic and dynamically reads
# each user's farm data and weather forecast before responding.
# Every conversation is logged to Chat_Logs table.
# ============================================================

import re
import logging
import requests
from datetime import datetime

from config   import Config
from database import (
    get_user_by_phone, get_user_by_telegram_chat_id,
    get_latest_farm_data, log_chat, get_user_by_id,
)
from ml_gee_engine import (
    fetch_weather_forecast, generate_agronomy_alerts,
    et_to_pump_hours,
)

log = logging.getLogger(__name__)

# ── Egyptian-Arabic Response Templates ───────────────────────

GREETING_RESPONSES = [
    "أهلاً يا حاج! 🌿 أنا موجود، إيه اللي تحتاجه؟",
    "مرحباً بيك يا باشا! عامزني في إيه النهارده؟",
    "يا هلا! إيه أخبار الأرض؟ قولي أساعدك في إيه؟",
]

UNKNOWN_RESPONSE = (
    "معلش يا حاج، مش فاهم سؤالك كويس. "
    "جرّب تسألني عن: الري، المحصول، الطقس، أو حالة الأرض. 🌱"
)

HELP_TEXT = (
    "🌾 ممكن تسألني عن:\n"
    "• هل أروي النهارده؟\n"
    "• إيه حالة المحصول؟\n"
    "• النباتات بخير؟\n"
    "• توقعات الطقس\n"
    "• ساعات الشغل المطلوبة من الموتور\n"
    "• هل في عطل في البيفوت؟\n"
    "• إيه كمية الكتلة الحيوية؟\n"
    "اكتب سؤالك بالعربي وأنا هرد! 😊"
)

# ── Intent Detection (keyword matching) ──────────────────────

INTENTS = {
    "irrigation":   [
        "ري", "روي", "أروي", "ميه", "مية", "ماء", "سقي", "مياه",
        "الموتور", "التوربين", "ضخ",
    ],
    "crop_health":  [
        "حالة", "المحصول", "الزرع", "النبات", "مريض", "تعبان",
        "صحة", "ازاي", "إيه أخبار", "بخير", "حاجة غلط",
    ],
    "ndvi":         ["ndvi", "المؤشر الخضري", "خضرة", "ندفي"],
    "weather":      ["طقس", "درجة حرارة", "حرارة", "غداً", "بكرا", "تقلبات"],
    "pump":         ["ساعات", "موتور", "شغل", "ديزل", "كهرباء", "وقود"],
    "biomass":      ["كتلة", "biomass", "إنتاج", "محصول", "وزن"],
    "pivot":        ["بيفوت", "عطل", "ميكانيكي", "pivot", "شذوذ", "تحذير"],
    "greeting":     ["هلو", "هايي", "أهلاً", "مرحبا", "إزيك", "السلام", "صباح", "مساء"],
    "help":         ["مساعدة", "help", "إيه اللي", "قولي", "ممكن تساعدني"],
    "et":           ["et", "تبخر", "بخر", "التبخّر", "الاستهلاك"],
    "wue":          ["wue", "كفاءة", "مياه", "استخدام المياه"],
}


def detect_intent(message: str) -> str:
    """Return the single best-matching intent or 'unknown'."""
    msg_lower = message.lower()
    for intent, keywords in INTENTS.items():
        for kw in keywords:
            if kw in msg_lower:
                return intent
    return "unknown"


# ── Farm Context Loader ───────────────────────────────────────

def _load_farm_context(user_id: int) -> dict:
    """
    Load the latest farm metrics and a 5-day weather forecast
    for the given user. Returns a unified context dict.
    """
    user        = get_user_by_id(user_id)
    farm_row    = get_latest_farm_data(user_id)
    farm        = dict(farm_row) if farm_row else {}
    forecast    = []
    agro_alerts = []

    if user and user["lat"] and user["lon"]:
        try:
            forecast    = fetch_weather_forecast(user["lat"], user["lon"])
            agro_alerts = generate_agronomy_alerts(forecast, farm)
        except Exception as exc:
            log.warning(f"[Bot] Could not fetch weather: {exc}")

    return {
        "user":        dict(user) if user else {},
        "farm":        farm,
        "forecast":    forecast,
        "agro_alerts": agro_alerts,
    }


# ── Response Generators ───────────────────────────────────────

def _respond_irrigation(ctx: dict) -> str:
    farm     = ctx["farm"]
    forecast = ctx["forecast"]
    user     = ctx["user"]

    et   = farm.get("et")
    ndvi = farm.get("ndvi")
    area = user.get("area_feddan", 1.0) or 1.0

    if et is None:
        return (
            "يا حاج، لسه مفيش بيانات كافية عن الأرض. "
            "ارفع ملف البيانات الأول عشان أقدر أساعدك. 📊"
        )

    pump_h = et_to_pump_hours(et, area)

    # Check tomorrow's weather
    tomorrow_hot = False
    if forecast:
        tomorrow = forecast[0] if len(forecast) > 0 else {}
        if tomorrow.get("temp_max", 0) > 38:
            tomorrow_hot = True

    if et > 7:
        advice = (
            f"🚨 يا حاج، الأرض عطشانة جداً! "
            f"معدل التبخّر عالي ({et:.1f} مم/يوم). "
            f"لازم تشغّل الموتور تقريباً {pump_h:.1f} ساعة."
        )
    elif et > 5:
        advice = (
            f"💧 الأرض محتاجة مية. "
            f"التبخّر ({et:.1f} مم/يوم) — شغّل الموتور {pump_h:.1f} ساعة."
        )
    elif et > 3:
        advice = (
            f"🟡 الأرض في الوسط، التبخّر {et:.1f} مم/يوم. "
            f"راقب الأرض ولو شفت النبات يبان تعبان روّي ({pump_h:.1f} ساعة)."
        )
    else:
        advice = (
            f"✅ الأرض كويسة النهارده، التبخّر منخفض ({et:.1f} مم/يوم). "
            "مش محتاج ري دلوقتي."
        )

    if tomorrow_hot:
        advice += (
            f"\n⚠️ بكرا الحرارة هتعلى لـ {forecast[0]['temp_max']:.0f}°C — "
            "يُنصح تروّي مبكراً قبل الشمس."
        )

    return advice


def _respond_crop_health(ctx: dict) -> str:
    farm   = ctx["farm"]
    status = farm.get("status", "unknown")
    ndvi   = farm.get("ndvi")
    lst    = farm.get("lst")

    if status == "healthy":
        msg = (
            f"✅ المحصول بخير يا حاج! "
            f"مؤشر NDVI = {ndvi:.2f} والحرارة {lst:.1f}°C. "
            "الأرض تمام والزرع نشيط. 🌿"
        )
    elif status == "water_stress":
        msg = (
            f"🔴 عندك إجهاد مائي يا حاج! "
            f"NDVI = {ndvi:.2f} منخفض — الزرع محتاج مية فوراً. "
            "اشتغّل الموتور دلوقتي. 💧"
        )
    elif status == "heat_stress":
        msg = (
            f"🌡️ في إجهاد حراري! "
            f"الحرارة {lst:.1f}°C عالية جداً. "
            "حاول تروّي في الصبح الباكر أو بعد الغروب. 🌅"
        )
    elif status == "pivot_alert":
        msg = (
            "⚠️ تحذير بيفوت! في شذوذ في توزيع الري الدوّار. "
            "افحص ذراع البيفوت وعجَل الرش على طول. 🔧"
        )
    else:
        msg = (
            f"يا حاج، الحالة مش واضحة عندي النهارده. "
            "ارفع بيانات جديدة عشان أقيّم الوضع صح. 📊"
        )

    return msg


def _respond_ndvi(ctx: dict) -> str:
    farm = ctx["farm"]
    ndvi = farm.get("ndvi")
    if ndvi is None:
        return "مفيش قراءة NDVI متاحة دلوقتي يا حاج. 📡"
    scale = (
        "ممتاز 🌿" if ndvi > 0.7
        else "كويس" if ndvi > 0.5
        else "متوسط 🟡" if ndvi > 0.35
        else "ضعيف 🔴"
    )
    return (
        f"📊 مؤشر الغطاء النباتي (NDVI) = {ndvi:.3f} — {scale}.\n"
        "NDVI بيتراوح من -1 لـ 1 والأفضل فوق 0.6 للمحاصيل. 🌾"
    )


def _respond_weather(ctx: dict) -> str:
    forecast = ctx["forecast"]
    if not forecast:
        return "معلش يا حاج، مش قادر أجيب توقعات الطقس دلوقتي. 🌡️"
    lines = ["🌤️ توقعات الطقس لـ 5 أيام قادمة:\n"]
    for day in forecast[:5]:
        lines.append(
            f"📅 {day['date']}: {day['temp_min']:.0f}°C – {day['temp_max']:.0f}°C | "
            f"رطوبة {day['humidity']}% | {day['description']}"
        )
    return "\n".join(lines)


def _respond_pump(ctx: dict) -> str:
    farm = ctx["farm"]
    user = ctx["user"]
    et   = farm.get("et")
    area = user.get("area_feddan", 1.0) or 1.0

    pump_h = farm.get("pump_hours") or (et_to_pump_hours(et, area) if et else None)
    if pump_h is None:
        return "مفيش بيانات كافية لحساب ساعات الشغل يا حاج. 🔧"

    return (
        f"⏱️ الموتور محتاج يشتغل تقريباً {pump_h:.1f} ساعة النهارده "
        f"لأرض {area:.0f} فدان بناءً على معدل التبخّر الحالي. 💡"
    )


def _respond_biomass(ctx: dict) -> str:
    farm    = ctx["farm"]
    biomass = farm.get("biomass")
    if biomass is None:
        return "مفيش بيانات الكتلة الحيوية دلوقتي يا حاج. 🌾"
    return (
        f"🌾 الكتلة الحيوية المُقدَّرة للمحصول = {biomass:.2f} طن/هكتار. "
        "دي مؤشر على إنتاجية الزرع. 📈"
    )


def _respond_pivot(ctx: dict) -> str:
    farm = ctx["farm"]
    cv   = farm.get("ndvi_cv")
    ti   = farm.get("thermal_inertia")

    if cv is None:
        return (
            "مفيش بيانات كافية للكشف عن عطل البيفوت دلوقتي. "
            "محتاج على الأقل أسبوع من القراءات. 📡"
        )

    if cv > 0.25 and ti and ti > 0.10:
        return (
            f"⚠️ تحذير حرج! معامل تباين NDVI = {cv:.3f} مع شذوذ حراري. "
            "يُرجح وجود عطل في ذراع البيفوت — افحص الجهاز فوراً! 🔧"
        )
    elif cv > 0.25:
        return (
            f"🟡 تحذير: في تفاوت في الغطاء النباتي (CV={cv:.3f}). "
            "ممكن في مشكلة في الري الدوّار — راقب الوضع. 👀"
        )
    else:
        return (
            f"✅ البيفوت شغّال كويس! التوزيع منتظم (CV={cv:.3f}). 🔄"
        )


def _respond_et(ctx: dict) -> str:
    farm = ctx["farm"]
    et   = farm.get("et")
    if et is None:
        return "مفيش بيانات التبخّر دلوقتي يا حاج. 💧"
    return (
        f"💧 معدل التبخّر والنتح (ET) = {et:.2f} مم/يوم. "
        f"{'مرتفع — الزرع يحتاج ري عاجل 🚨' if et > 7 else 'معتدل ✅' if et > 4 else 'منخفض 🟢'}."
    )


def _respond_wue(ctx: dict) -> str:
    farm = ctx["farm"]
    wue  = farm.get("wue")
    if wue is None:
        return "مفيش بيانات كفاءة استخدام المياه دلوقتي. 💧"
    return (
        f"📊 كفاءة استخدام المياه (WUE) = {wue:.3f} كجم/م³. "
        f"{'ممتازة 🌟' if wue > 1.5 else 'مقبولة 🟡' if wue > 0.8 else 'تحتاج تحسين 🔴'}."
    )


# ── Intent Router ─────────────────────────────────────────────

INTENT_HANDLERS = {
    "irrigation":  _respond_irrigation,
    "crop_health": _respond_crop_health,
    "ndvi":        _respond_ndvi,
    "weather":     _respond_weather,
    "pump":        _respond_pump,
    "biomass":     _respond_biomass,
    "pivot":       _respond_pivot,
    "et":          _respond_et,
    "wue":         _respond_wue,
}


def generate_response(user_id: int, message: str, channel: str = "web", image_b64: str = None) -> str:
    """
    Core NLP entry point with AI Core integration.
    1. Direct shortcuts for greeting and help.
    2. Deep LLM + RAG + Farm Context intelligence via ai_core.ask_agri_assistant.
    3. Resilient fallback to local templates if ai_core unavailable.
    """
    if not message or not message.strip():
        if not image_b64:
            return "تفضل يا حاج، بس ارسلّي سؤالك! 😊"

    message = (message or "").strip()
    intent  = detect_intent(message)

    # Greeting / help shortcuts
    if intent == "greeting":
        response = GREETING_RESPONSES[hash(message) % len(GREETING_RESPONSES)]
        try:
            log_chat(user_id, channel, message, response)
        except Exception:
            pass
        return response
    elif intent == "help":
        response = HELP_TEXT
        try:
            log_chat(user_id, channel, message, response)
        except Exception:
            pass
        return response

    # ── AI Core Pipeline (LLM + RAG + Multi-turn memory) ─────
    try:
        from ai_core import ask_agri_assistant
        return ask_agri_assistant(user_id, message, channel=channel, image_b64=image_b64)
    except Exception as exc:
        log.warning(f"[Bot] ai_core call failed, using rule engine: {exc}")

    # Fallback to local rule engine if ai_core encounters an issue
    if intent in INTENT_HANDLERS:
        ctx      = _load_farm_context(user_id)
        response = INTENT_HANDLERS[intent](ctx)
    else:
        ctx      = _load_farm_context(user_id)
        alerts   = ctx.get("agro_alerts", [])
        if alerts:
            latest  = alerts[0]
            response = latest["message_ar"] + "\n\n" + UNKNOWN_RESPONSE
        else:
            response = UNKNOWN_RESPONSE

    try:
        log_chat(user_id, channel, message, response)
    except Exception as exc:
        log.warning(f"[Bot] Could not log chat: {exc}")

    return response


# ── WhatsApp Handler (Twilio) ─────────────────────────────────

def handle_whatsapp_message(from_number: str, body: str) -> str:
    """
    Called by the Flask WhatsApp webhook route.
    Maps incoming Twilio phone number to a DB user, then generates a response.
    Returns a TwiML-compatible response string (plain text for Twilio Messaging).
    """
    # Strip the 'whatsapp:' prefix that Twilio adds
    clean_phone = from_number.replace("whatsapp:", "").strip()

    user = get_user_by_phone(clean_phone)
    if not user:
        return (
            "مرحباً! 👋 أنا مساعد تيسير Agri-SaT. "
            "رقم تليفونك مش مسجّل في النظام. "
            "من فضلك سجّل أولاً على الموقع."
        )

    if not user["is_verified"]:
        return "حسابك لسه مش متحقق منه. سجّل دخولك على الموقع وأكمل التحقق. 🔐"

    return generate_response(user["id"], body, channel="whatsapp")


def build_whatsapp_twiml(response_text: str) -> str:
    """Wrap plain text in a minimal TwiML <Response><Message> body."""
    safe = response_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{safe}</Message></Response>"
    )


# ── Telegram Handler ──────────────────────────────────────────

def handle_telegram_update(update_payload: dict) -> None:
    """
    Process an incoming Telegram webhook payload.
    Identifies the user by their Telegram chat_id and sends a reply
    via the Telegram Bot API (no library dependency — raw HTTP).
    """
    try:
        message_obj = update_payload.get("message") or update_payload.get("edited_message")
        if not message_obj:
            return

        chat_id = str(message_obj["chat"]["id"])
        text    = message_obj.get("text", "").strip()
        if not text:
            return

        # Handle /start — link a new user or greet existing
        if text.startswith("/start"):
            _telegram_send(chat_id, (
                "أهلاً بيك يا حاج في تيسير Agri-SaT! 🌾\n"
                "لربط حسابك، ابعتلي الأمر:\n"
                "/link email@example.com\n"
                "أو كلّمني بأي سؤال زراعي. 😊"
            ))
            return

        # Handle /link <email>
        if text.startswith("/link"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                _telegram_send(chat_id, "كتبت /link بس مكتبتش الإيميل. مثال: /link farmer@example.com")
                return
            email = parts[1].strip()
            _link_telegram_account(chat_id, email)
            return

        # Normal conversation
        user = get_user_by_telegram_chat_id(chat_id)
        if not user:
            _telegram_send(chat_id, (
                "مش قادر أعرّفك يا حاج! 🤔\n"
                "ربط حسابك بـ:\n/link your_email@example.com"
            ))
            return

        response = generate_response(user["id"], text, channel="telegram")
        _telegram_send(chat_id, response)

    except Exception as exc:
        log.error(f"[Telegram] Handler error: {exc}")


def _link_telegram_account(chat_id: str, email: str) -> None:
    """Find user by email and link their Telegram chat ID."""
    from database import get_user_by_email, link_telegram
    user = get_user_by_email(email)
    if not user:
        _telegram_send(chat_id, f"مش لاقي حساب بالإيميل ده: {email}. تأكد من البيانات. 🙁")
        return
    if not user["is_verified"]:
        _telegram_send(chat_id, "حسابك لسه مش متحقق منه. اعمل تحقق على الموقع الأول. 🔐")
        return
    link_telegram(user["id"], chat_id)
    _telegram_send(chat_id, (
        f"✅ تم ربط حسابك بنجاح يا {user['name']}! "
        "دلوقتي ممكن تسألني أي سؤال عن أرضك. 🌾"
    ))


def _telegram_send(chat_id: str, text: str) -> None:
    """Send a message via the Telegram Bot API (raw HTTP POST)."""
    url     = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.error(f"[Telegram] Send error: {exc}")


def set_telegram_webhook(base_url: str = None) -> bool:
    """
    Register the Telegram webhook URL with the Bot API.
    Call once after deployment or when the URL changes.
    """
    webhook_url = base_url or Config.TELEGRAM_WEBHOOK_URL
    full_url    = f"{webhook_url}/api/webhook/telegram"
    api_url     = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/setWebhook"
    try:
        resp = requests.post(api_url, json={"url": full_url}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            log.info(f"[Telegram] Webhook set to {full_url}")
            return True
        else:
            log.error(f"[Telegram] Webhook registration failed: {data}")
            return False
    except Exception as exc:
        log.error(f"[Telegram] Webhook error: {exc}")
        return False


# ── Self-test ─────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Intent detection test
    tests = [
        ("هل أروي النهارده؟",   "irrigation"),
        ("إيه حالة المحصول؟",   "crop_health"),
        ("توقعات الطقس عندك؟",  "weather"),
        ("ساعات شغل الموتور",   "pump"),
        ("هلو يا بوت",          "greeting"),
    ]
    for msg, expected in tests:
        result = detect_intent(msg)
        status = "✓" if result == expected else "✗"
        print(f"  {status}  '{msg}' → {result} (expected: {expected})")
    print("[Bot] Intent detection tests done.")
