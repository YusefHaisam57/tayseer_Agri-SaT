# ============================================================
# config.py — Tayseer Agri-SaT Configuration
# IMPORTANT: Replace all placeholder values with your real keys.
# Never commit this file to a public repository.
# ============================================================

import os
from dotenv import load_dotenv

# Load overrides from a local .env file (if it exists)
load_dotenv()

# ── Flask Core ───────────────────────────────────────────────
class Config:
    # A strong, random secret key for session signing.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-to-a-long-random-string")

    # SQLite database path (sits in the project root)
    DATABASE_PATH = os.getenv("DATABASE_PATH", "tayseer_agrisat.db")

    # ── Twilio (SMS OTP + WhatsApp)  ─────────────────────────
    TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN",  "your_twilio_auth_token")
    # The Twilio phone number that sends SMS (E.164 format)
    TWILIO_SMS_FROM      = os.getenv("TWILIO_SMS_FROM",    "+1XXXXXXXXXX")
    # The Twilio sandbox / production WhatsApp sender
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    # ── Email (SMTP — Gmail example) ─────────────────────────
    SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
    SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER     = os.getenv("SMTP_USER",     "your_email@gmail.com")
    # Use an App Password, NOT your main Gmail password
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your_gmail_app_password")
    SMTP_FROM_NAME = "Tayseer Agri-SaT"

    # ── Google Earth Engine ───────────────────────────────────
    # Path to the GEE service-account JSON key file
    GEE_SERVICE_ACCOUNT_KEY = os.getenv(
        "GEE_SERVICE_ACCOUNT_KEY", "gee_service_account.json"
    )
    # Your GEE cloud project ID
    GEE_PROJECT = os.getenv("GEE_PROJECT", "tayseeragrisat")
    # Service account email
    GEE_SERVICE_ACCOUNT_EMAIL = os.getenv(
        "GEE_SERVICE_ACCOUNT_EMAIL", "tayseeragri-sat@tayseeragrisat.iam.gserviceaccount.com"
    )
    # API Key (for REST API calls)
    GEE_API_KEY = os.getenv("GEE_API_KEY", "AIzaSyDD6XJIqHsps1bt3gxhvkzHy0v49pBOmEA")
    # OAuth 2.0 Client ID
    GEE_OAUTH_CLIENT_ID = os.getenv("GEE_OAUTH_CLIENT_ID", "925429062102-1v0ksma9tugmqbti9174d7qmd253dc04.apps.googleusercontent.com")
    # Service Account Unique ID
    GEE_SERVICE_ACCOUNT_ID = os.getenv("GEE_SERVICE_ACCOUNT_ID", "105106838017167256528")

    # ── OpenWeatherMap ────────────────────────────────────────
    OWM_API_KEY = os.getenv("OWM_API_KEY", "your_openweathermap_api_key")
    # How many days to forecast (OWM free tier provides 5 days / 3-hour steps)
    OWM_FORECAST_DAYS = 5

    # ── Telegram Bot ─────────────────────────────────────────
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
    # Publicly accessible URL where Telegram will POST updates.
    # During local dev use ngrok: e.g. "https://abc123.ngrok.io"
    TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "https://your-ngrok-url.ngrok.io")

    # ── OTP Settings ─────────────────────────────────────────
    OTP_EXPIRY_SECONDS = 300   # 5 minutes
    OTP_LENGTH         = 6

    # ── ML Model ─────────────────────────────────────────────
    # Path where the trained CatBoost model is persisted
    CATBOOST_MODEL_PATH = os.getenv("CATBOOST_MODEL_PATH", "catboost_crop_health.cbm")

    # ── Application ───────────────────────────────────────────
    # IMPORTANT: Set DEBUG=false in production for better performance
    DEBUG         = os.getenv("DEBUG", "False").lower() == "true"
    HOST          = os.getenv("HOST", "0.0.0.0")
    PORT          = int(os.getenv("PORT", "5000"))
    # Session lifetime in minutes
    SESSION_LIFETIME_MINUTES = 60

    # ── Step 12: Carbon-Sat V2.0 ──────────────────────────────
    # Default carbon credit market price (USD per tonne CO2-eq)
    CARBON_CREDIT_PRICE_DEFAULT = float(os.getenv("CARBON_CREDIT_PRICE", "15.0"))
    # IPCC Tier 2 emission factors — Egyptian cattle
    LIVESTOCK_CH4_FACTOR = 55    # kg CH4 / head / year
    LIVESTOCK_NH3_FACTOR = 18    # kg NH3 / head / year
    # IPCC 2019 rice methane emission factor (continuously flooded)
    RICE_CH4_FACTOR = 1.3        # kg CH4 / ha / day
    # Minimum NCS to classify soil as carbon sink (g C/m²/year)
    CARBON_SINK_MINIMUM = 50
    # Maximum livestock density for the density→heads proxy
    LIVESTOCK_MAX_DENSITY = 5    # head / hectare at index = 1.0


# ── Application Build & Version Specification ────────────────
TAYSEER_VERSION = {
    "application": "Tayseer Agri-SaT",
    "version": "4.2.0-prod",
    "git_commit": "v4.2-20261007-final",
    "build_time": "2026-10-07T08:30:00Z",
    "environment": "production" if not os.getenv("DEBUG", "False").lower() == "true" else "development",
    "database": os.getenv("DATABASE_PATH", "tayseer_agrisat.db"),
    "ai_core": "v2.1-rag-multimodal",
    "risk_engine": "v2.0-unified",
    "rag": "v2.0-persistent-isolated",
    "multimodal": "v1.2-vision-agri",
}
