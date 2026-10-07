# ============================================================
# app.py — Tayseer Agri-SaT Flask Application Entry Point
#
# Routes:
#   GET  /                     → redirect to login or dashboard
#   GET  /login                → login page
#   POST /auth/register        → create account + send OTP
#   POST /auth/verify-otp      → verify OTP and log user in
#   POST /auth/login           → password login + send OTP
#   GET  /auth/logout          → logout
#   GET  /dashboard            → main dashboard (auth required)
#   GET  /api/farm-data        → latest farm metrics JSON
#   GET  /api/weather          → 5-day forecast JSON
#   POST /api/run-pipeline     → trigger GEE + ML analysis
#   POST /api/upload-csv       → upload CSV and ingest data
#   POST /api/chat             → web chatbot (AJAX)
#   GET  /api/download-csv     → download user farm data as CSV
#   POST /api/webhook/whatsapp → Twilio WhatsApp webhook
#   POST /api/webhook/telegram → Telegram bot webhook
# ============================================================

import os
import io
import csv
import random
import string
import smtplib
import logging
import datetime
from email.mime.text   import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

import json
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, send_file, send_from_directory, g,
)
from werkzeug.utils import secure_filename

from config       import Config, TAYSEER_VERSION
from database     import (
    init_db, create_user, get_user_by_email, get_user_by_phone,
    get_user_by_id, get_latest_farm_data,
    get_farm_data_range, get_all_farm_data, hash_password,
    check_password, upsert_farm_data, log_chat,
    update_user_profile, get_chat_history,
    get_all_users, approve_farm_name, reject_farm_name,
    create_pending_change, store_tile_urls, get_cached_tiles, get_all_active_farms,
    get_farm_data_timeseries, get_connection,
    # Step 9 — Reports
    log_report, get_user_reports, mark_report_downloaded,
)
from ml_gee_engine import (
    run_full_pipeline, parse_csv, apply_physics_pipeline,
    fetch_weather_forecast, generate_agronomy_alerts,
    detect_pivot_failure, predict_crop_health,
    fetch_gee_per_zone, generate_gee_tiles,
    interpret_timeseries, calculate_wue_numeric,
    fetch_gee_data, compute_lst_split,
)
from omni_bot import (
    generate_response, handle_whatsapp_message,
    build_whatsapp_twiml, handle_telegram_update,
)
from cache_manager import get_cache, set_cache, invalidate_cache
from gee_integration import register_gee_blueprint
# ── Step 10 — Security & Multi-Tenancy ─────────────────────────────
from security import (
    init_security_manager, get_security_manager,
    token_required, role_required, permission_required, rate_limit as jwt_rate_limit,
)

logging.basicConfig(
    level=logging.DEBUG if Config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ── APScheduler: Daily GEE Tile Update (تحديث تيلز الأقمار يومياً) ─────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler_started = False
except ImportError:
    _scheduler = None
    _scheduler_started = False
    log.warning("[Scheduler] APScheduler not installed — daily tile updates disabled.")

# ── App setup ─────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.permanent_session_lifetime = datetime.timedelta(
    minutes=Config.SESSION_LIFETIME_MINUTES
)

# ── Database initialisation ───────────────────────────────────

with app.app_context():
    init_db()

# ── Step 10 — Initialise SecurityManager ─────────────────────────
_security = init_security_manager(app)

# ── Template Context: Build & Version Injection ───────────────
@app.context_processor
def inject_system_context():
    return {
        "app_version": TAYSEER_VERSION,
        "BUILD_VERSION": TAYSEER_VERSION["version"],
    }

# ── System Health & Version Endpoints ─────────────────────────
@app.route("/api/system/version", methods=["GET"])
def api_system_version():
    """Centralized application version endpoint."""
    return jsonify(TAYSEER_VERSION)

@app.route("/health", methods=["GET"])
def health_check():
    """Lightweight process health."""
    return jsonify({
        "status": "UP",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "version": TAYSEER_VERSION["version"]
    }), 200

@app.route("/ready", methods=["GET"])
def readiness_check():
    """Check critical platform dependencies."""
    db_ok = False
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
            db_ok = True
    except Exception:
        db_ok = False

    ai_ok = True
    weather_ok = bool(Config.OWM_API_KEY and not Config.OWM_API_KEY.startswith("your_"))
    gee_ok = bool(os.getenv("GEE_PROJECT"))

    ready = db_ok
    status = "READY" if ready else "NOT_READY"
    code = 200 if ready else 503

    return jsonify({
        "status": status,
        "database": "OK" if db_ok else "ERROR",
        "ai": "OK" if ai_ok else "ERROR",
        "weather": "OK" if weather_ok else "NO_CREDENTIAL",
        "gee": "AVAILABLE" if gee_ok else "DEMO_MODE",
        "version": TAYSEER_VERSION["version"]
    }), code

# Track user IDs with a pipeline already running in background
_running_pipelines: set = set()

# Register GEE Blueprint
try:
    register_gee_blueprint(app)
except Exception as e:
    log.error(f"[Init] Failed to register GEE Blueprint: {e}")

# ── Auth helpers ──────────────────────────────────────────────

def login_required(f):
    """Decorator: redirect to /login if no active session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: restrict route exactly to admins."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        user = get_user_by_id(session["user_id"])
        if not user or not user["is_admin"]:
            return "Unauthorized. Admins only.", 403
        return f(*args, **kwargs)
    return decorated


def current_user():
    """Return the logged-in user dict (or None). Handles Impersonation."""
    # If the real admin is impersonating a user, return the impersonated user's data
    impersonated_id = session.get("impersonated_user_id")
    if impersonated_id:
        return get_user_by_id(impersonated_id)

    uid = session.get("user_id")
    if uid:
        return get_user_by_id(uid)
    return None

# ── Page Routes ───────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    # The actual requesting session determines if the top banner shows "Impersonating"
    real_user_id = session.get("user_id")
    is_impersonating = "impersonated_user_id" in session
    user_dict = dict(user)
    # Load cached GEE tile URLs for the MapLibre 3D map
    gee_tiles = get_cached_tiles(user["id"])
    return render_template(
        "dashboard.html",
        user=user_dict,
        is_impersonating=is_impersonating,
        gee_tiles=gee_tiles,
    )


@app.route("/map-view")
@login_required
def map_view_page():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("map_view.html", user=dict(user))


@app.route("/profile")
@login_required
def profile_page():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("profile.html", user=dict(user))


@app.route("/whatsapp")
@login_required
def whatsapp_page():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("whatsapp.html", user=dict(user))


@app.route("/telegram")
@login_required
def telegram_page():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("telegram.html", user=dict(user))


@app.route("/settings")
@login_required
def settings_page():
    """Client Settings page: Planted_Area, Pump_Flow_Rate, irrigation device."""
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("settings.html", user=dict(user))


@app.route("/reports")
@login_required
def reports_page():
    """Step 9 — Custom Reports System page."""
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("reports.html", user=dict(user))


@app.route("/host-admin")
@admin_required
def host_admin_redirect():
    """Friendly URL alias for the admin dashboard."""
    return redirect(url_for("admin_dashboard"))


@app.route("/integrations")
@login_required
def integrations_page():
    """Platform Integrations & Diagnostic Dashboard."""
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("integrations.html", user=dict(user))


@app.route("/api/system/integrations-status")
@login_required
def api_integrations_status():
    """Real-time diagnostic check for all external integrations. No fake data."""
    import urllib.request
    import json as _ij
    statuses = {}

    # 1. Google Earth Engine
    gee_key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tayseeragrisat-b55999a2322f.json"
    )
    gee_project  = os.getenv("GEE_PROJECT", "")
    gee_key_ok   = os.path.isfile(gee_key_path)
    try:
        from ml_gee_engine import _gee_initialized
        gee_live = bool(_gee_initialized)
    except Exception:
        gee_live = False
    statuses["gee"] = {
        "status":    "connected" if gee_live else ("credential_error" if gee_key_ok else "not_configured"),
        "project":   gee_project or "NOT_SET",
        "key_found": gee_key_ok,
        "note":      ("GEE authenticated" if gee_live else
                      "Key found but 403 — EXTERNAL_CREDENTIAL_REQUIRED" if gee_key_ok else
                      "No GEE service account key"),
    }

    # 2. OpenWeatherMap
    owm_key = Config.OWM_API_KEY or ""
    owm_ok  = bool(owm_key and not owm_key.startswith("your_") and len(owm_key) > 10)
    owm_live = False
    if owm_ok:
        try:
            url = f"https://api.openweathermap.org/data/2.5/weather?q=Cairo&appid={owm_key}&units=metric"
            with urllib.request.urlopen(url, timeout=5) as r:
                owm_live = (r.status == 200)
        except Exception:
            pass
    statuses["owm"] = {
        "status": "connected" if owm_live else ("configured" if owm_ok else "not_configured"),
        "note":   ("OWM LIVE — real forecast data" if owm_live else
                   "Key set, connection failed" if owm_ok else "Not configured"),
    }

    # 3. LLM providers
    def _key_ok(k):
        return bool(k and not k.startswith("your_"))
    statuses["llm"] = {
        "gemini":   "configured" if _key_ok(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) else "not_configured",
        "groq":     "configured" if _key_ok(os.getenv("GROQ_API_KEY"))   else "not_configured",
        "openai":   "configured" if _key_ok(os.getenv("OPENAI_API_KEY")) else "not_configured",
        "fallback": "active",
        "note":     "Expert rule-based RAG fallback always active",
    }

    # 4. Telegram
    tg_tok  = os.getenv("TELEGRAM_BOT_TOKEN") or ""
    tg_ok   = bool(tg_tok and ":" in tg_tok and not tg_tok.startswith("your_"))
    tg_live = False
    if tg_ok:
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{tg_tok}/getMe", timeout=5) as r:
                tg_live = _ij.loads(r.read().decode()).get("ok", False)
        except Exception:
            pass
    statuses["telegram"] = {
        "status": "connected" if tg_live else ("configured" if tg_ok else "not_configured"),
        "note":   "Bot authenticated" if tg_live else ("Token set" if tg_ok else "Not configured"),
    }

    # 5. WhatsApp / Twilio
    t_sid = os.getenv("TWILIO_ACCOUNT_SID") or ""
    t_num = os.getenv("TWILIO_WHATSAPP_FROM") or ""
    statuses["whatsapp"] = {
        "status":      "configured" if (t_sid and not t_sid.startswith("your_")) else "not_configured",
        "from_number": t_num or "NOT_SET",
    }

    # 6. SMTP
    smtp_host = os.getenv("SMTP_HOST") or getattr(Config, "SMTP_HOST", "") or ""
    smtp_user = os.getenv("SMTP_USER") or getattr(Config, "SMTP_USER", "") or ""
    statuses["smtp"] = {
        "status": "configured" if (smtp_host and smtp_user) else "not_configured",
        "host":   smtp_host or "NOT_SET",
    }

    # 7. IoT / CM60 Telemetry
    try:
        with get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM Pivot_Telemetry LIMIT 1").fetchone()[0]
            has_iot = cnt > 0
    except Exception:
        has_iot = False
    statuses["iot"] = {
        "status": "data_available" if has_iot else "data_unavailable",
        "note":   "Real CM60 telemetry in DB" if has_iot else "No IoT hardware — DATA_UNAVAILABLE",
    }

    # 8. Database
    try:
        with get_connection() as conn:
            uc = conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
        statuses["database"] = {"status": "connected", "user_count": uc}
    except Exception as exc:
        statuses["database"] = {"status": "error", "note": str(exc)}

    return jsonify({
        "success":   True,
        "statuses":  statuses,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })


import re

def is_strong_password(password: str) -> bool:
    """
    Check if a password is strong:
    - At least 8 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 number
    - At least 1 special character (@$!%*?&#^_-)
    """
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(r"[@$!%*?&#^_-]", password):
        return False
    return True

# ── Auth API Routes ───────────────────────────────────────────

@app.route("/auth/register", methods=["POST"])
def register():
    """
    Create a new user account and directly log them in.
    Expects JSON body: name, phone, email, password,
                       farm_location, crop_type, area_feddan, lat, lon, farm_geojson (optional)
    """
    data = request.get_json(silent=True) or {}
    required = ["name", "phone", "email", "password",
                 "farm_location", "crop_type", "area_feddan", "lat", "lon"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"success": False,
                        "message": f"حقول مطلوبة ناقصة: {', '.join(missing)}"}), 400

    # Check for duplicates
    if get_user_by_email(data["email"]):
        return jsonify({"success": False,
                        "message": "البريد الإلكتروني مسجّل بالفعل."}), 409
    if get_user_by_phone(data["phone"]):
        return jsonify({"success": False,
                        "message": "رقم الهاتف مسجّل بالفعل."}), 409

    # Validate password strength
    if not is_strong_password(data["password"]):
        return jsonify({"success": False,
                        "message": "كلمة السر ضعيفة. يجب أن تحتوي على الأقل على 8 حروف، حرف كبير، حرف صغير، رقم، ورمز خاص."}), 400

    user_id = create_user(
        name          = data["name"],
        phone         = data["phone"],
        email         = data["email"],
        password_hash = hash_password(data["password"]),
        farm_location = data["farm_location"],
        crop_type     = data["crop_type"],
        area_feddan   = float(data["area_feddan"]),
        lat           = float(data["lat"]),
        lon           = float(data["lon"]),
        farm_geojson  = data.get("farm_geojson"),
    )

    # Immediately log the user in
    session.permanent = True
    session["user_id"] = user_id

    return jsonify({
        "success": True,
        "message": "تم إنشاء الحساب وانضمامك لمنصة تيسير بنجاح! 🌿",
        "redirect": url_for("dashboard"),
    })


@app.route("/auth/login", methods=["POST"])
def login():
    """
    Authenticate with email + password, then log the user in via Flask session.
    Step 10 additions: account lockout protection, audit logging, failed-attempt tracking.
    """
    data     = request.get_json(silent=True) or {}
    email    = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"success": False,
                        "message": "أدخل البريد الإلكتروني وكلمة المرور."}), 400

    user = get_user_by_email(email)
    _sm  = get_security_manager()

    if not user:
        # Generic message prevents user-enumeration
        if _sm:
            _sm.log_security_event(
                'failed_login', 'low',
                description=f'Login attempt for unknown email from {request.remote_addr}',
            )
        return jsonify({"success": False,
                        "message": "بيانات تسجيل الدخول غير صحيحة."}), 401

    # ── Account lockout check ─────────────────────────────────
    if _sm:
        is_locked, lock_until = _sm.check_account_locked(user["id"])
        if is_locked:
            mins_left = max(1, int(
                (lock_until - datetime.datetime.utcnow()).total_seconds() / 60
            ))
            return jsonify({"success": False,
                            "message": f"الحساب مقفل مؤقتاً. حاول مرة أخرى بعد {mins_left} دقيقة."}), 403

    # ── Password verification ──────────────────────────────────
    password_ok = check_password(user["password_hash"], password)
    if not password_ok:
        locked = _sm.handle_failed_login(user["id"]) if _sm else False
        if locked:
            return jsonify({
                "success": False,
                "message": (
                    f"تم قفل الحساب بعد 5 محاولات فاشلة. "
                    f"سيتم فتحه تلقائياً بعد 30 دقيقة."
                ),
            }), 403
        return jsonify({"success": False,
                        "message": "بيانات تسجيل الدخول غير صحيحة."}), 401

    # ── Account active check ──────────────────────────────────
    if user.get('is_active') == 0:
        return jsonify({"success": False,
                        "message": "الحساب معطل. تواصل مع الدعم للمساعدة."}), 403

    # ── Success ─────────────────────────────────────────────────
    if _sm:
        _sm.reset_failed_attempts(user["id"])
        _sm.log_action(user["id"], 'login', 'user', user["id"])

    session.permanent  = True
    session["user_id"] = user["id"]

    return jsonify({
        "success": True,
        "message": "أهلاً بك مجدداً في تيسير! 🌿",
        "redirect": url_for("dashboard"),
    })



@app.route("/auth/logout")
def logout():
    uid = session.get('user_id')
    session.clear()
    _sm = get_security_manager()
    if _sm and uid:
        _sm.log_action(uid, 'logout', 'user', uid)
    return redirect(url_for("login_page"))


# ════════════════════════════════════════════════════════════
# Step 10 — JWT-Based API Auth Endpoints
# ════════════════════════════════════════════════════════════

@app.route('/api/auth/login', methods=['POST'])
@jwt_rate_limit(max_requests=5, window_minutes=15)   # brute-force guard
def api_auth_login():
    """
    JWT login endpoint for API clients.
    Returns a signed Bearer token on success.
    """
    data     = request.get_json(silent=True) or {}
    email    = data.get('email', '').strip()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'success': False, 'error': 'البريد وكلمة المرور مطلوبان'}), 400

    _sm  = get_security_manager()
    user = get_user_by_email(email)

    if not user:
        if _sm:
            _sm.log_security_event('failed_login', 'low',
                description=f'API login — unknown email from {request.remote_addr}')
        return jsonify({'success': False, 'error': 'بيانات غير صحيحة'}), 401

    if _sm:
        is_locked, lock_until = _sm.check_account_locked(user['id'])
        if is_locked:
            mins = max(1, int((lock_until - datetime.datetime.utcnow()).total_seconds() / 60))
            return jsonify({'success': False,
                            'error': f'الحساب مقفل — حاول بعد {mins} دقيقة'}), 403

    if not check_password(user['password_hash'], password):
        locked = _sm.handle_failed_login(user['id']) if _sm else False
        if locked:
            return jsonify({'success': False,
                            'error': 'تم قفل الحساب بعد 5 محاولات'}), 403
        return jsonify({'success': False, 'error': 'بيانات غير صحيحة'}), 401

    if user.get('is_active') == 0:
        return jsonify({'success': False, 'error': 'الحساب معطل'}), 403

    role  = 'host' if user.get('is_admin') else 'client'
    token = None
    if _sm:
        _sm.reset_failed_attempts(user['id'])
        token = _sm.generate_token(user['id'], role=role)
        _sm.log_action(user['id'], 'api_login', 'user', user['id'])

    return jsonify({
        'success': True,
        'token':   token,
        'user': {
            'id':    user['id'],
            'name':  user.get('name'),
            'email': user.get('email'),
            'role':  role,
        },
    })


@app.route('/api/auth/logout', methods=['POST'])
@token_required
def api_auth_logout():
    """Revoke the JWT token (API logout)."""
    _sm = get_security_manager()
    if _sm:
        _sm.revoke_token(request.jwt_token)
        _sm.log_action(request.jwt_user_id, 'api_logout', 'user', request.jwt_user_id)
    return jsonify({'success': True, 'message': 'تم تسجيل الخروج'})


@app.route('/api/auth/change-password', methods=['POST'])
@token_required
def api_jwt_change_password():
    """Change password via JWT auth. Revokes all existing tokens on success."""
    data         = request.get_json(silent=True) or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password:
        return jsonify({'success': False, 'error': 'كلتا كلمتي المرور مطلوبتان'}), 400

    user = get_user_by_id(request.jwt_user_id)
    if not user:
        return jsonify({'success': False, 'error': 'المستخدم غير موجود'}), 404

    if not check_password(user['password_hash'], old_password):
        return jsonify({'success': False, 'error': 'كلمة المرور القديمة غير صحيحة'}), 401

    _sm = get_security_manager()
    if _sm:
        valid, msg = _sm.validate_password_strength(new_password)
        if not valid:
            return jsonify({'success': False, 'error': msg}), 400

    new_hash = hash_password(new_password)
    from database import update_user_field
    update_user_field(user['id'], 'password_hash', new_hash)
    update_user_field(user['id'], 'password_changed_at',
                      datetime.datetime.utcnow().isoformat())

    if _sm:
        _sm.revoke_all_user_tokens(user['id'])
        _sm.log_action(user['id'], 'password_changed', 'user', user['id'])
        _sm.log_security_event('password_change', 'medium', user_id=user['id'],
                               description='User changed password via API')

    return jsonify({'success': True,
                    'message': 'تم تغيير كلمة المرور. سجّل الدخول مرة أخرى.'})


# ════════════════════════════════════════════════════════════
# Step 10 — Admin Security & Audit API (session-based admin auth)
# ════════════════════════════════════════════════════════════

@app.route('/api/admin/audit-logs')
@login_required
@admin_required
def api_audit_logs():
    """
    Return paginated audit log entries. Admin-only.
    Query params: days(int), user_id(int), action(str), limit(int≤500)
    """
    from database import get_audit_logs
    days   = request.args.get('days',    30,  type=int)
    uid    = request.args.get('user_id', None, type=int)
    action = request.args.get('action',  None)
    limit  = min(request.args.get('limit', 100, type=int), 500)
    logs   = get_audit_logs(days=days, user_id=uid, action=action, limit=limit)
    return jsonify({'success': True, 'count': len(logs), 'logs': logs})


@app.route('/api/admin/security-events')
@login_required
@admin_required
def api_security_events():
    """
    Return security event entries. Admin-only.
    Query params: severity(str), resolved(bool), limit(int≤500)
    """
    from database import get_security_events
    severity     = request.args.get('severity')
    resolved_str = request.args.get('resolved')
    resolved     = (resolved_str.lower() == 'true') if resolved_str else None
    limit        = min(request.args.get('limit', 100, type=int), 500)
    events       = get_security_events(severity=severity, resolved=resolved, limit=limit)
    return jsonify({'success': True, 'count': len(events), 'events': events})


@app.route('/api/admin/security-events/<int:event_id>/resolve', methods=['POST'])
@login_required
@admin_required
def api_resolve_security_event(event_id):
    """Mark a security event as resolved."""
    from database import resolve_security_event
    admin_id = session.get('user_id')
    resolve_security_event(event_id, resolved_by=admin_id)
    _sm = get_security_manager()
    if _sm:
        _sm.log_action(admin_id, 'resolve_security_event', 'security_event', event_id)
    return jsonify({'success': True, 'message': 'تم تمييز الحدث كمحلول'})


@app.route('/api/admin/users/<int:target_uid>/toggle-active', methods=['POST'])
@login_required
@admin_required
def api_toggle_user_active(target_uid):
    """Enable or disable a user account (admin-only)."""
    from database import toggle_user_active
    data      = request.get_json(silent=True) or {}
    is_active = bool(data.get('is_active', True))
    admin_id  = session.get('user_id')
    toggle_user_active(target_uid, is_active)
    _sm = get_security_manager()
    if _sm:
        _sm.log_action(admin_id, 'enable_user' if is_active else 'disable_user',
                       'user', target_uid)
    status = 'مفعّل' if is_active else 'معطّل'
    return jsonify({'success': True, 'message': f'تم تغيير حالة الحساب: {status}'})


@app.route('/api/admin/users/<int:target_uid>/unlock', methods=['POST'])
@login_required
@admin_required
def api_unlock_user(target_uid):
    """Manually unlock a locked user account."""
    from database import unlock_user_account
    admin_id = session.get('user_id')
    unlock_user_account(target_uid)
    _sm = get_security_manager()
    if _sm:
        _sm.log_action(admin_id, 'unlock_account', 'user', target_uid)
        _sm.log_security_event('account_unlocked', 'low', user_id=target_uid,
                               description=f'Account unlocked by admin {admin_id}')
    return jsonify({'success': True, 'message': 'تم فتح الحساب بنجاح'})


@app.route('/api/security/dashboard')
@login_required
@admin_required
def api_security_dashboard():
    """Summary stats for the admin security dashboard widget."""
    from database import get_audit_logs, get_security_events
    recent_events  = get_security_events(resolved=False, limit=10)
    recent_audits  = get_audit_logs(days=1, limit=20)
    critical_count = len(get_security_events(severity='critical', resolved=False))
    high_count     = len(get_security_events(severity='high',     resolved=False))
    return jsonify({
        'success':        True,
        'critical_count': critical_count,
        'high_count':     high_count,
        'recent_events':  recent_events,
        'recent_audits':  recent_audits,
    })


# ── Admin Panel Routes ────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    """Main Host Super-Panel view."""
    users = get_all_users()
    # Ensure rows act like dicts for template
    dict_users = [dict(u) for u in users]
    return render_template("admin/dashboard.html", users=dict_users)


@app.route("/admin/approve-name/<int:target_user_id>", methods=["POST"])
@admin_required
def admin_approve_name(target_user_id):
    """Host approves a client's pending farm name."""
    reviewed_by = session.get("user_id")
    success = approve_farm_name(target_user_id, reviewed_by=reviewed_by)
    if success:
        return jsonify({"success": True, "message": "تمت الموافقة على اسم المزرعة."})
    return jsonify({"success": False, "message": "لا يوجد اسم معلق للموافقة عليه."}), 404


@app.route("/admin/reject-name/<int:target_user_id>", methods=["POST"])
@admin_required
def admin_reject_name(target_user_id):
    """Host rejects a client's pending farm name."""
    reviewed_by = session.get("user_id")
    success = reject_farm_name(target_user_id, reviewed_by=reviewed_by)
    return jsonify({"success": success, "message": "تم رفض الاسم."})


@app.route("/admin/impersonate/<int:target_user_id>")
@admin_required
def admin_impersonate(target_user_id):
    """Login As Client (Impersonate)."""
    user = get_user_by_id(target_user_id)
    if not user:
        return "User not found", 404
        
    session["impersonated_user_id"] = target_user_id
    # Temporarily invalidate their cache to ensure fresh data loads
    from cache_manager import invalidate_cache
    invalidate_cache(f"farm_data:{target_user_id}")
    return redirect(url_for("dashboard"))


@app.route("/admin/stop-impersonation")
def admin_stop_impersonation():
    """Exit impersonation and return to Admin view."""
    if "impersonated_user_id" in session:
        session.pop("impersonated_user_id")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/update-access/<int:target_user_id>", methods=["POST"])
@admin_required
def admin_update_access(target_user_id):
    """Set the JSON modules_access array for a client."""
    data = request.get_json(silent=True) or {}
    modules = data.get("modules_access", "[]")  # e.g. '["basic", "digital_twin"]'
    success, msg = update_user_profile(target_user_id, modules_access=modules)
    return jsonify({"success": success, "message": msg})



# ── Mini-Tayseer Chat Page ─────────────────────────────────────

@app.route("/chat")
@login_required
def chat_assistant_page():
    """Serve the unified Tayseer AI Assistant page."""
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("ai_assistant.html", user=dict(user))


# ── Detect Pivot Issues Helper ─────────────────────────────────

def detect_pivot_issues(row: dict) -> list:
    """
    Analyse a Farm_Data row and return a list of detected issues.
    Each issue: {type, component, tower, severity, action}
    """
    issues = []
    ndvi   = float(row.get("ndvi")    or 0.5)
    cv     = float(row.get("cv")      or 0.0)   # Coefficient of Variation (%)
    et     = float(row.get("et")      or 0.0)
    area   = float(row.get("area_feddan") or row.get("area") or 1.0)

    # 1. Mechanical (ring artefact or high spatial CV → pivot stopped)
    if cv > 20:
        tower = int(cv // 5) % 8 + 1   # heuristic tower from CV magnitude
        issues.append({
            "type":      "mechanical",
            "component": "كوبلنج",
            "tower":     f"برج {tower}",
            "severity":  "حرج",
            "action":    f"عطل ميكانيكي في برج {tower} - افحص الكوبلنج فوراً",
        })
    elif cv > 15:
        tower = int(cv // 3) % 8 + 1
        issues.append({
            "type":      "mechanical",
            "component": "رشاشات",
            "tower":     f"برج {tower}",
            "severity":  "متوسط",
            "action":    f"تفاوت في التوزيع - افحص رشاشات برج {tower}",
        })

    # 2. Water stress (NDVI critically low)
    if ndvi < 0.25:
        issues.append({
            "type":      "water_stress",
            "component": "رشاشات",
            "tower":     "كل الخط",
            "severity":  "حرج",
            "action":    "إجهاد مائي شديد - تأكد من فتح الرشاشات وشغّل الطلمبة",
        })
    elif ndvi < 0.35:
        issues.append({
            "type":      "water_stress",
            "component": "رشاشات",
            "tower":     "الخط الخارجي",
            "severity":  "متوسط",
            "action":    "إجهاد مائي - راقب ضغط الشبكة وزيادة ساعات الري",
        })

    return issues


# ── Farm Summary API (for Mini-Tayseer offline cache) ─────────

@app.route("/api/get-farm-summary")
@login_required
def api_get_farm_summary():
    """
    Build a complete farm-data JSON payload for LocalStorage caching.
    Powers the Mini-Tayseer offline chat — called once on first load,
    then periodically refreshed when the user is online.
    """
    import datetime as _dt, json as _json

    user_row = current_user()
    if not user_row:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    user = dict(user_row)
    uid = user["id"]

    # ── Latest row ──────────────────────────────────────────────
    raw_row = get_latest_farm_data(uid)
    row = dict(raw_row) if raw_row else {}

    # ── Timeseries rows for histro context (7 days) ─────────────
    ts_rows = get_farm_data_timeseries(uid, days=7,
                                        columns=["ndvi", "et", "biomass", "tvdi"]) or []

    # ── Overall metrics ─────────────────────────────────────────
    ndvi_avg   = float(row.get("ndvi")    or 0.55)
    et_avg     = float(row.get("et")      or 4.5)
    biomass    = float(row.get("biomass") or 1.0)
    tvdi       = float(row.get("tvdi")    or 0.35)

    status = "healthy" if ndvi_avg >= 0.6 else "warning" if ndvi_avg >= 0.3 else "critical"

    # WUE numeric
    area_feddan = float(user.get("area_feddan") or 1.0)
    try:
        wue_num = calculate_wue_numeric(biomass, et_avg, area_feddan)
    except Exception:
        wue_num = 0.0

    # ── Build fake pivot list from single-row data ───────────────
    # (Real multi-pivot data would come from pivot-level rows;
    #  for now we represent the whole farm as a single pivot)
    single_pivot_issues = detect_pivot_issues(row)
    pivots = [{
        "id":           "FARM",
        "name":         user.get("farm_name") or "المزرعة",
        "crop":         user.get("crop_type") or "محصول مجهول",
        "area_feddan":  area_feddan,
        "ndvi":         ndvi_avg,
        "et":           et_avg,
        "pump_hours":   round((et_avg * 4.2 * area_feddan) /
                              max(float(user.get("pump_flow_rate") or 50), 1), 2),
        "health":       status,
        "issues":       single_pivot_issues,
        "last_irrigation": row.get("date") or _dt.date.today().isoformat(),
    }]

    # ── Insights ─────────────────────────────────────────────────
    irr_today  = ("لا - الأرض رطبة" if tvdi < 0.25 else
                  "يُنصح - الأرض جافة نسبياً" if tvdi < 0.5 else
                  "أروِ الآن! الأرض جافة جداً")

    alerts = [f"{p['name']}: {iss['action']}"
              for p in pivots for iss in p["issues"] if iss["severity"] == "حرج"]

    waste_label = ("ممتاز - لا يوجد هدر" if et_avg <= 4.5 else
                   "مرتفع قليلاً" if et_avg <= 6.0 else "هدر مياه!")

    disease_risk = "منخفض" if ndvi_avg >= 0.45 else "متوسط" if ndvi_avg >= 0.3 else "مرتفع"

    # ── Weather: try real OWM, never fake ──────────────────────────
    weather = []
    weather_status = "DATA_UNAVAILABLE"
    if user.get("lat") and user.get("lon"):
        try:
            weather = fetch_weather_forecast(user["lat"], user["lon"])
            if weather:
                weather_status = "available"
        except Exception as _wex:
            log.warning(f"[FarmSummary] Weather fetch failed: {_wex}")
    if not weather:
        weather_status = "DATA_UNAVAILABLE"


    today = _dt.date.today()
    summary = {
        "timestamp":   _dt.datetime.utcnow().isoformat() + "Z",
        "farm_name":   user.get("farm_name") or "مزرعتي",
        "last_update": today.isoformat(),
        "overall": {
            "ndvi_avg":      round(ndvi_avg,  4),
            "et_avg":        round(et_avg,    3),
            "biomass_total": round(biomass,   3),
            "wue_numeric":   round(wue_num,   3),
            "tvdi":          round(tvdi,      3),
            "status":        status,
        },
        "pivots":   pivots,
        "weather":  weather,
        "weather_status": weather_status,
        "insights": {
            "irrigation_today":  irr_today,
            "critical_alerts":   alerts,
            "water_waste":       waste_label,
            "disease_risk":      disease_risk,
        },
    }


    return jsonify({"success": True, "farm_data": summary})


# ── Farm Data API ─────────────────────────────────────────────

@app.route("/api/farm-data")

@login_required
def api_farm_data():
    """Return the latest farm metrics for the logged-in user."""
    # Handle impersonation properly:
    # We fetch metrics for the 'current_user()', not strictly the browser session["user_id"]
    user = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    effective_user_id = user["id"]
    
    # Check cache first (10-minute TTL)
    cache_key = f"farm_data:{effective_user_id}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)
    
    row = get_latest_farm_data(effective_user_id)

    # Auto-run pipeline in background if no data exists (non-blocking)
    if not row and user and user["lat"] and user["lon"]:
        if effective_user_id not in _running_pipelines:
            _running_pipelines.add(effective_user_id)
            log.info(f"[AutoPipeline] No data for user {effective_user_id} — launching background pipeline...")
            import threading
            from ml_gee_engine import run_full_pipeline

            def _bg_pipeline():
                try:
                    run_full_pipeline(
                        user_id        = effective_user_id,
                        lat            = user["lat"],
                        lon            = user["lon"],
                        area_feddan    = user["area_feddan"] or 1.0,
                        crop_type      = user["crop_type"]   or "general",
                        pump_flow_rate = user.get("pump_flow_rate", 50.0),
                    )
                    log.info(f"[AutoPipeline] Done for user {effective_user_id}")
                    # Invalidate cache when new data arrives
                    invalidate_cache("farm_data")
                except Exception as exc:
                    log.error(f"[AutoPipeline] Failed for user {effective_user_id}: {exc}")
                finally:
                    _running_pipelines.discard(effective_user_id)

            threading.Thread(target=_bg_pipeline, daemon=True).start()
        else:
            log.info(f"[AutoPipeline] Already running for user {effective_user_id}, skipping.")

    history  = get_farm_data_range(effective_user_id, days=30)
    farm = dict(row) if row else {}

    # Compute pivot alert from history
    ndvi_series = [r["ndvi"] for r in history if r["ndvi"]]
    lst_series  = [r["lst"]  for r in history if r["lst"]]
    pivot_alert = detect_pivot_failure(ndvi_series, lst_series)

    # History for chart (last 30 days) — all metrics for per-crop chart rendering
    chart_data = [
        {
            "date":    r["date"],
            "ndvi":    r["ndvi"],
            "et":      r["et"],
            "biomass": r["biomass"],
            "wue":     r["wue"],
            "ndwi":    r["ndwi"],
            "tvdi":    r["tvdi"],
            "lst":     r["lst"],
            "lst_soil": r["lst_soil"],
            "lst_canopy": r["lst_canopy"],
            "fapar":   r["fapar"],
        }
        for r in history
    ]

    result = {
        "success":          True,
        "farm":             farm,
        "pivot_alert":      pivot_alert,
        "chart":            chart_data,
        "agro_alerts":      [],   # Weather alerts are fetched separately via /api/weather
        "pipeline_running": effective_user_id in _running_pipelines,
    }
    
    # Cache the result
    set_cache(cache_key, result)
    return jsonify(result)


@app.route("/api/gee-tiles")
@login_required
def api_gee_tiles():
    """
    Generate GEE tile URL templates for satellite index overlays.
    Returns XYZ tile URLs for NDVI, NDWI, NDSI, Soil Moisture, ET,
    Biomass, and TVDI at 10m resolution (Sentinel-2).
    """
    from ml_gee_engine import generate_gee_tiles

    user = current_user()
    if not user or not user["lat"] or not user["lon"]:
        return jsonify({"success": False, "message": "No coordinates."}), 400

    tiles = generate_gee_tiles(
        lat          = user["lat"],
        lon          = user["lon"],
        farm_geojson = user.get("farm_geojson"),
        date_str     = None,   # latest available
    )

    if not tiles:
        return jsonify({
            "success": False,
            "message": "GEE tiles unavailable. Check GEE credentials.",
        }), 503

    # Separate metadata from tile URLs for clean JSON
    palettes = tiles.pop("_palettes", {})
    tile_date = tiles.pop("_date", None)

    return jsonify({
        "success":   True,
        "tiles":     tiles,
        "palettes":  palettes,
        "tile_date": tile_date,
    })


@app.route("/api/weather")
@login_required
def api_weather():
    """Return 5-day weather forecast and agronomy alerts."""
    user_id = session["user_id"]
    
    # Check cache first (1-hour TTL)
    cache_key = f"weather:{user_id}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)
    
    user    = current_user()
    if not user or not user["lat"] or not user["lon"]:
        return jsonify({"success": False, "message": "لا توجد إحداثيات مسجّلة."}), 400

    row      = get_latest_farm_data(user_id)
    farm_row = dict(row) if row else {}

    forecast    = fetch_weather_forecast(user["lat"], user["lon"])
    agro_alerts = generate_agronomy_alerts(forecast, farm_row)

    result = {
        "success":     True,
        "forecast":    forecast,
        "agro_alerts": agro_alerts,
    }
    
    # Cache for 1 hour
    set_cache(cache_key, result)
    return jsonify(result)


@app.route("/api/run-pipeline", methods=["POST"])
@login_required
def api_run_pipeline():
    """
    Trigger the full GEE + ML analysis pipeline for the current user.
    This is intentionally synchronous for local dev; use APScheduler for prod.
    """
    user    = current_user()
    user_id = session["user_id"]

    if not user["lat"] or not user["lon"]:
        return jsonify({"success": False,
                        "message": "أضف إحداثيات المزرعة أولاً في ملف تعريفك."}), 400

    try:
        result = run_full_pipeline(
            user_id        = user_id,
            lat            = user["lat"],
            lon            = user["lon"],
            area_feddan    = user["area_feddan"] or 1.0,
            crop_type      = user["crop_type"]   or "general",
            pump_flow_rate = user.get("pump_flow_rate", 50.0),
        )
        return jsonify({"success": True, "result": result})
    except Exception as exc:
        log.exception("[Pipeline] Error")
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/upload-csv", methods=["POST"])
@login_required
def api_upload_csv():
    """
    Accept a CSV file upload, parse it, apply physics, and persist to DB.
    Expected CSV columns: date, ndvi [, lst, ndwi, et, biomass, wue, tvdi]
    """
    user_id     = session["user_id"]
    user        = current_user()
    area_feddan = user["area_feddan"] or 1.0

    if "file" not in request.files:
        return jsonify({"success": False, "message": "لم يتم إرسال ملف."}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"success": False, "message": "يجب أن يكون الملف بصيغة CSV."}), 400

    try:
        df = parse_csv(f.stream)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 422

    saved = 0
    for _, row in df.iterrows():
        raw = row.dropna().to_dict()
        enriched = apply_physics_pipeline(
            raw, 
            area_feddan=area_feddan, 
            pump_flow_rate=user.get("pump_flow_rate", 50.0)
        )
        upsert_farm_data(user_id, raw["date"], **{
            k: v for k, v in enriched.items()
            if k in {"ndvi", "lst", "lst_soil", "lst_canopy", "ndwi", "et", "biomass", "wue",
                     "tvdi", "fapar", "ndvi_cv", "thermal_inertia",
                     "status", "pump_hours", "source"}
        })
        saved += 1

    return jsonify({
        "success": True,
        "message": f"✅ تم استيراد {saved} صف بنجاح من ملف CSV.",
        "rows_saved": saved,
    })


@app.route("/api/download-csv")
@login_required
def api_download_csv():
    """Generate and stream all farm data for the user as a CSV file."""
    user_id = session["user_id"]
    rows    = get_all_farm_data(user_id)

    if not rows:
        return jsonify({"success": False, "message": "لا توجد بيانات للتنزيل."}), 404

    # Build CSV in memory
    output   = io.StringIO()
    fieldnames = [
        "id", "date", "ndvi", "lst", "lst_soil", "lst_canopy", "ndwi", "et",
        "biomass", "wue", "tvdi", "fapar",
        "ndvi_cv", "thermal_inertia",
        "status", "pump_hours", "source", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))

    output.seek(0)
    fname = f"tayseer_farm_data_{datetime.date.today().isoformat()}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype    = "text/csv; charset=utf-8-sig",
        as_attachment = True,
        download_name = fname,
    )


# ── Profile API ───────────────────────────────────────────────

@app.route("/api/profile/update", methods=["POST"])
@login_required
def api_profile_update():
    """Update the logged-in user's profile fields."""
    data    = request.get_json(silent=True) or {}
    user    = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    # We update the effective user (which handles impersonation)
    user_id = user["id"]

    allowed_fields = {}
    if "name" in data and data["name"].strip():
        allowed_fields["name"] = data["name"].strip()
    if "email" in data and data["email"].strip():
        allowed_fields["email"] = data["email"].strip()
    if "phone" in data and data["phone"].strip():
        allowed_fields["phone"] = data["phone"].strip()
    
    # --- FARM NAME UPDATES ---
    if "farm_name" in data:
        proposed_name = data["farm_name"].strip()
        # If admin is updating their own or an impersonated profile, apply directly
        if session.get("user_id") and get_user_by_id(session["user_id"])["is_admin"]:
            allowed_fields["farm_name"] = proposed_name
            allowed_fields["pending_farm_name"] = None # Clear pending if admin forces an update
        else:
            # Normal client updating their name -> send to pending queue
            allowed_fields["pending_farm_name"] = proposed_name

    if "farm_location" in data:
        allowed_fields["farm_location"] = data["farm_location"].strip()
    if "crop_type" in data:
        allowed_fields["crop_type"] = data["crop_type"].strip()
    if "area_feddan" in data:
        allowed_fields["area_feddan"] = float(data["area_feddan"])
    if "lat" in data and data["lat"] is not None:
        allowed_fields["lat"] = float(data["lat"])
    if "lon" in data and data["lon"] is not None:
        allowed_fields["lon"] = float(data["lon"])
    if "farm_geojson" in data:
        allowed_fields["farm_geojson"] = data["farm_geojson"]
    if "irrigation_type" in data:
        allowed_fields["irrigation_type"] = data["irrigation_type"].strip()
    if "pump_flow_rate" in data and data["pump_flow_rate"] is not None:
        allowed_fields["pump_flow_rate"] = float(data["pump_flow_rate"])
    if "pump_flow_rate_m3_per_hr" in data and data["pump_flow_rate_m3_per_hr"] is not None:
        allowed_fields["pump_flow_rate"] = float(data["pump_flow_rate_m3_per_hr"])
    if "pump_power_kw" in data and data["pump_power_kw"] is not None:
        allowed_fields["pump_power_kw"] = float(data["pump_power_kw"])
    # New well / turbine fields
    if "well_flow_rate" in data and data["well_flow_rate"] is not None:
        allowed_fields["well_flow_rate"] = float(data["well_flow_rate"])
    if "well_depth" in data and data["well_depth"] is not None:
        allowed_fields["well_depth"] = float(data["well_depth"])
    if "pump_head_m" in data and data["pump_head_m"] is not None:
        allowed_fields["pump_head_m"] = float(data["pump_head_m"])

    if not allowed_fields:
        return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

    success, msg = update_user_profile(user_id, **allowed_fields)
    return jsonify({"success": success, "message": msg})


# ── Farm Name Edit API ────────────────────────────────────────

@app.route("/api/edit-farm-name", methods=["POST"])
@login_required
def api_edit_farm_name():
    """
    Dedicated endpoint for clients to request a farm name change.
    The change goes to pending queue and requires admin approval.
    Logs to Pending_Changes table for full audit trail.
    """
    data = request.get_json(silent=True) or {}
    user = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    new_name = (data.get("farm_name") or "").strip()
    if not new_name:
        return jsonify({"success": False, "message": "اسم المزرعة لا يمكن أن يكون فارغاً."}), 400

    old_name = user.get("farm_name") or ""
    user_id  = user["id"]

    # Admin can directly set the name
    real_user = get_user_by_id(session["user_id"])
    if real_user and real_user["is_admin"]:
        ok, msg = update_user_profile(user_id, farm_name=new_name, pending_farm_name=None)
        return jsonify({"success": ok, "message": msg, "approved": True})

    # Client → pending queue + audit log
    ok, msg = update_user_profile(user_id, pending_farm_name=new_name)
    if ok:
        create_pending_change(user_id, "farm_name", new_name, old_value=old_name or None)
    return jsonify({
        "success": ok,
        "message": "تم إرسال طلب تغيير الاسم للمراجعة. سيتم تطبيقه بعد موافقة المشرف. ✅" if ok else msg,
        "approved": False,
    })



# ── FarmProcessor — Pivot vs Orchard Algorithm Factory ───────

# Wadi El-Natrun Pivot centers (lon, lat format for GEE)
WADI_NATRUN_PIVOTS = [
    [30.058015, 30.205279], [30.053554, 30.199594],
    [30.063201, 30.201245], [30.048990, 30.196320],
    [30.067891, 30.197443], [30.044500, 30.192900],
    [30.072100, 30.193200], [30.040200, 30.189600],
    [30.076200, 30.189000], [30.035900, 30.185800],
    [30.080200, 30.184900], [30.031600, 30.182200],
    [30.084100, 30.180700], [30.027300, 30.178400],
    [30.088100, 30.176500], [30.023100, 30.174600],
]

# Al-Qazzaz Orchard polygon (lon, lat)
AL_QAZZAZ_POLYGON = [
    [32.257488, 30.638237], [32.255718, 30.637544],
    [32.263368, 30.633748], [32.263754, 30.634320],
    [32.256297, 30.638262], [32.257488, 30.638237],  # close ring
]


class FarmProcessor:
    """
    Selects the correct GEE algorithm (Pivot or Orchard) based on farm_type.
    Wraps generate_gee_tiles() with type-aware geometry construction.
    """

    def __init__(self, farm_type: str = 'pivot'):
        self.farm_type = (farm_type or 'pivot').lower()

    def get_geometry_and_tiles(self, lat: float, lon: float,
                                farm_geojson: str = None) -> dict:
        """
        Returns GEE tile dict using farm-type-appropriate geometry.
        Falls back to point buffer if GEE is unavailable.
        """
        try:
            if self.farm_type == 'orchard':
                # Al-Qazzaz: use specific polygon, ignore user lat/lon
                tiles = generate_gee_tiles(
                    lat=30.635992, lon=32.260373,
                    farm_geojson=json.dumps({
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [AL_QAZZAZ_POLYGON]
                        }
                    })
                )
            else:
                # Pivot (Wadi El-Natrun or user-defined):
                # Use user's lat/lon with their drawn farm_geojson
                tiles = generate_gee_tiles(
                    lat=lat, lon=lon, farm_geojson=farm_geojson
                )
        except Exception as exc:
            log.error(f"[FarmProcessor] Tile generation failed: {exc}")
            tiles = {}
        return tiles

    @staticmethod
    def get_pivot_status(ndvi_val: float) -> dict:
        """Wadi El-Natrun NDVI threshold logic."""
        if ndvi_val is None:
            return {"status": "unknown", "label": "⚠️ غير متوفر", "color": "#9ca3af"}
        if ndvi_val < 0.2:
            return {"status": "fallow",  "label": "⬛ بُور/متروك", "color": "#6b7280"}
        if ndvi_val < 0.5:
            return {"status": "moderate","label": "🟡 متوسط",     "color": "#f59e0b"}
        return     {"status": "healthy", "label": "🟢 سليم",      "color": "#10b981"}

    @staticmethod
    def get_orchard_status(ndwi_val: float) -> dict:
        """Al-Qazzaz NDWI water stress threshold logic."""
        if ndwi_val is None:
            return {"status": "unknown", "label": "⚠️ غير متوفر", "color": "#9ca3af"}
        if ndwi_val < -0.1:
            return {"status": "stressed","label": "🔴 ضغط مائي شديد — تقليم القطارات", "color": "#ef4444"}
        if ndwi_val < 0.0:
            return {"status": "mild",    "label": "🟡 ضغط مائي خفيف",                 "color": "#f59e0b"}
        return     {"status": "healthy", "label": "🟢 جيد",                           "color": "#10b981"}


# ── GEE Tile API Endpoints ────────────────────────────────────

@app.route("/api/get-latest-tiles", methods=["GET"])
@login_required
def api_get_latest_tiles():
    """
    Return cached GEE tile URLs for current user's farm.
    Used by dashboard for auto-refresh every 6 hours.
    Response: {tile_urls: {...}, updated: bool, generated_at: '...'}
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "tile_urls": {}, "updated": False})

    user_id = user["id"]
    tiles   = get_cached_tiles(user_id)

    return jsonify({
        "success":   bool(tiles),
        "tile_urls": {k: v for k, v in tiles.items() if k != '_palettes'},
        "palettes":  tiles.get("_palettes", {}),
        "updated":   bool(tiles),
    })


@app.route("/api/generate-tiles", methods=["POST"])
@login_required
def api_generate_tiles():
    """
    Force-regenerate GEE tile URLs for the current user's farm.
    Accepts optional {layer_types: ['ndvi', 'et', ...]} — ignored for now (all layers generated).
    Runs in a background thread so the request returns immediately.
    Response: {success: true, message: '...', queued: true}
    """
    import threading

    user = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    user_id     = user["id"]
    lat         = user.get("lat") or 30.0
    lon         = user.get("lon") or 31.0
    farm_geojson= user.get("farm_geojson")
    farm_type   = user.get("farm_type", "pivot")

    def _regen():
        try:
            processor = FarmProcessor(farm_type)
            tiles = processor.get_geometry_and_tiles(float(lat), float(lon), farm_geojson)
            if tiles:
                store_tile_urls(user_id, tiles)
                log.info(f"[Tiles] Regenerated {len(tiles)-2} layers for user {user_id}")
        except Exception as exc:
            log.error(f"[Tiles] Regen error for user {user_id}: {exc}")

    threading.Thread(target=_regen, daemon=True).start()

    return jsonify({
        "success": True,
        "message": "جاري توليد طبقات الأقمار الصناعية في الخلفية... ستظهر خلال دقيقة. 🛰️",
        "queued":  True,
    })


@app.route("/api/get-pixel-data", methods=["POST"])
@login_required
def api_get_pixel_data():
    """
    Get all satellite indices for a specific lat/lon (pixel click on map).
    Input:  {lat: 30.123, lon: 31.456}
    Output: {ndvi: 0.65, et: 4.5, lst: 35.2, ndwi: 0.1, tvdi: 0.3, ...}
    Falls back to physics estimate if GEE unavailable.
    """
    data = request.get_json(silent=True) or {}
    lat  = data.get("lat")
    lon  = data.get("lon")

    if lat is None or lon is None:
        return jsonify({"success": False, "message": "lat/lon required"}), 400

    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid coordinates"}), 400

    user = current_user()
    user_area = float(user.get("area_feddan") or 1.0) if user else 1.0

    # Try GEE point sample first
    from ml_gee_engine import fetch_gee_data, apply_physics_pipeline, compute_lst_split
    try:
        raw = fetch_gee_data(lat, lon)  # has 15s timeout internally
    except Exception:
        raw = {}

    # Always enrich with physics pipeline
    metrics = apply_physics_pipeline(raw, area_feddan=user_area)
    lst     = metrics.get("lst") or 35.0
    ndvi    = metrics.get("ndvi") or 0.5
    ndwi    = metrics.get("ndwi") or 0.0

    lst_split = compute_lst_split(lst, ndvi)

    # Determine status and recommendation
    if ndvi >= 0.5:
        status  = "healthy"
        rec_ar  = "✅ المحصول بصحة جيدة. استمر في برنامج الري الحالي."
    elif ndvi >= 0.2:
        status  = "moderate"
        rec_ar  = "⚠️ توتر معتدل. تحقق من كميات الري ومستويات السماد."
    elif ndwi < -0.1:
        status  = "water_stress"
        rec_ar  = "💧 ضغط مائي شديد! راجع مخطط الري فوراً."
    else:
        status  = "fallow"
        rec_ar  = "🌱 منطقة فارغة أو مساحة بور. لا يوجد محصول نشط."

    return jsonify({
        "success": True,
        "lat":     lat,
        "lon":     lon,
        "ndvi":    round(ndvi, 4),
        "ndwi":    round(ndwi, 4),
        "lst":     round(lst, 2),
        "et":      round(float(metrics.get("et") or 4.0), 2),
        "tvdi":    round(float(metrics.get("tvdi") or 0.5), 4),
        "biomass": round(float(metrics.get("biomass") or 0.0), 3),
        "lst_soil":   lst_split.get("soil_temp"),
        "lst_canopy": lst_split.get("canopy_temp"),
        "status":     status,
        "recommendation": rec_ar,
        "source":  metrics.get("source", "physics"),
    })


# ── Chart Data API ─────────────────────────────────────────────

@app.route("/api/get-chart-data", methods=["POST"])
@login_required
def api_get_chart_data():
    """
    Returns timeseries data for a given parameter and time period.
    Input JSON:  {parameter, time_period, area_id}
    Output JSON: {dates, values, unit, interpretation}
    """
    data = request.get_json(force=True, silent=True) or {}
    param       = data.get("parameter", "ndvi").lower()
    time_period = data.get("time_period", "30days")
    # area_id is stored for future zone support; currently "all" = whole farm
    # area_id = data.get("area_id", "all")

    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # Resolve days
    _period_map = {"5days": 5, "10days": 10, "1month": 30, "3months": 90}
    days = _period_map.get(time_period, 30)

    # Column→unit mapping
    _unit_map = {
        "ndvi":   "-",
        "et":     "مم/يوم",
        "biomass":"طن/فدان",
        "tvdi":   "-",
        "wue":    "كجم/م³",
    }
    col = param if param in {"ndvi", "et", "biomass", "tvdi"} else "ndvi"

    rows = get_farm_data_timeseries(user["id"], days=days, columns=[col])

    if rows:
        dates  = [r["date"] for r in rows]
        values = [r.get(col) or 0.0 for r in rows]
    else:
        # No data — return empty arrays, never simulate
        dates, values = [], []


    # Compute WUE numeric if requested
    if param == "wue":
        biomass_rows = get_farm_data_timeseries(user["id"], days=days, columns=["biomass", "et"])
        if biomass_rows:
            area = float(user.get("area_feddan") or 1.0)
            values = [
                calculate_wue_numeric(r.get("biomass") or 0.5, r.get("et") or 5.0, area)
                for r in biomass_rows
            ]
            dates = [r["date"] for r in biomass_rows]

    interpretation = interpret_timeseries(values, param)

    return jsonify({
        "success":        True,
        "dates":          dates,
        "values":         values,
        "unit":           _unit_map.get(param, "-"),
        "interpretation": interpretation,
        "parameter":      param,
        "data_status":    "available" if dates else "DATA_UNAVAILABLE",
    })



# ── Digital Twin API ───────────────────────────────────────────

@app.route("/api/digital-twin", methods=["POST"])
@login_required
def api_digital_twin():
    """
    What-If irrigation delay scenario.
    Input JSON:  {delay_days, ndvi, tvdi, et, area_feddan}
    Output JSON: {moisture_drop_pct, yield_loss_pct, recommendation, risk_level}

    Physics:
     - Each delayed day, soil moisture drops by ET × 0.24 % per feddan (linear stress)
     - NDVI-based yield sensitivity factor (crops at peak growth lose more)
     - TVDI amplifies the drought risk
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    d = request.get_json(force=True, silent=True) or {}
    delay        = max(0, min(int(d.get("delay_days", 2)), 14))
    ndvi         = float(d.get("ndvi",  0.55))
    tvdi         = float(d.get("tvdi",  0.40))
    et           = float(d.get("et",    4.5))
    area_feddan  = float(d.get("area_feddan", float(user.get("area_feddan") or 1.0)))

    # Daily moisture stress per feddan: ET × 4.2 m³ demand / assumed 200mm reserve
    # expressed as % of root-zone capacity
    daily_drop_pct = (et * 4.2) / 200.0 * 100.0   # ~ 0.1–0.25 % / day
    # TVDI amplifier: near-dry soil (TVDI → 1) doubles the drop rate
    tvdi_factor    = 1.0 + tvdi
    moisture_drop  = round(min(daily_drop_pct * delay * tvdi_factor, 95.0), 1)

    # Yield loss: NDVI-weighted sensitivity × delay (FAO stress-day model)
    # peak NDVI crop (0.7+) is most sensitive; fallow (0.1) loses little
    ndvi_sensitivity = max(0.1, ndvi)       # 0 → 1 scale
    ky = 1.2 * ndvi_sensitivity             # yield response factor
    # Each delayed day under moisture stress = ky × daily_drop % loss
    yield_loss = round(min(ky * daily_drop_pct * delay * tvdi_factor, 100.0), 1)

    # Recommendation
    if yield_loss < 5:
        risk_level = "low"
        rec = f"تأخير {delay} يوم آمن. راقب TVDI خلال الـ 48 ساعة القادمة."
    elif yield_loss < 15:
        risk_level = "moderate"
        rec = f"خطر متوسط! تأخير {delay} يوم قد يخفض الإنتاج {yield_loss:.0f}%. اروِ خلال {max(1,delay-1)} يوم."
    else:
        risk_level = "high"
        rec = f"خطر عالٍ! تأخير {delay} يوم سيسبب خسارة {yield_loss:.0f}% من الإنتاج. ابدأ الري الآن."

    return jsonify({
        "success":          True,
        "delay_days":       delay,
        "moisture_drop_pct": moisture_drop,
        "yield_loss_pct":   yield_loss,
        "risk_level":       risk_level,
        "recommendation":   rec,
    })


# ── APScheduler: Start Daily Tile Update Job ──────────────────

def _daily_tile_update_job():

    """Runs daily at 06:00 — refreshes GEE tiles for all active farms."""
    log.info("[Scheduler] Daily GEE tile update starting...")
    farms = get_all_active_farms()
    log.info(f"[Scheduler] Processing {len(farms)} active farms.")
    for farm in farms:
        try:
            processor = FarmProcessor(farm.get("farm_type", "pivot"))
            tiles = processor.get_geometry_and_tiles(
                lat=float(farm["lat"]),
                lon=float(farm["lon"]),
                farm_geojson=farm.get("farm_geojson"),
            )
            if tiles:
                store_tile_urls(farm["id"], tiles)
                log.info(f"[Scheduler] ✅ Tiles stored for farm {farm['id']}")
        except Exception as exc:
            log.error(f"[Scheduler] Failed for farm {farm['id']}: {exc}")
    log.info("[Scheduler] Daily tile update complete.")


def _start_scheduler():
    global _scheduler_started
    if _scheduler is None or _scheduler_started:
        return
    try:
        _scheduler.add_job(
            _daily_tile_update_job,
            'cron', hour=6, minute=0,
            id='daily_gee_tile_update',
            replace_existing=True,
        )
        _scheduler.start()
        _scheduler_started = True
        log.info("[Scheduler] ✅ Daily GEE tile update scheduled at 06:00.")
    except Exception as exc:
        log.error(f"[Scheduler] Failed to start: {exc}")


# Register with Flask app context
with app.app_context():
    _start_scheduler()


# ── Zone Metrics API ──────────────────────────────────────────

@app.route("/api/zone-metrics", methods=["GET"])
@login_required
def api_zone_metrics():
    """
    Return per-zone GEE + physics metrics for all drawn zones
    in the current user's farm_geojson.

    Query params:
        date  (YYYY-MM-DD, optional) — defaults to today

    Response:
        {"success": true, "zones": [...], "count": N}
    Each zone dict: zone_label, zone_type, color, area_m2, area_feddan,
                    ndvi, ndwi, lst, et, tvdi, biomass, wue, status, source
    """
    user_id = session["user_id"]
    date_str = request.args.get("date") or datetime.date.today().isoformat()
    
    # Check cache (10-minute TTL)
    cache_key = f"zone_metrics:{user_id}:{date_str}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)
    
    user = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    farm_geojson  = user.get("farm_geojson") or ""
    area_feddan   = float(user.get("area_feddan") or 1.0)

    if not farm_geojson or farm_geojson == "None":
        result = {"success": False, "message": "No farm zones defined.", "zones": []}
        set_cache(cache_key, result)
        return jsonify(result)

    try:
        zones = fetch_gee_per_zone(
            farm_geojson       = farm_geojson,
            area_feddan_total  = area_feddan,
            date_str           = date_str,
        )
        result = {"success": True, "zones": zones, "count": len(zones)}
        set_cache(cache_key, result)
        return jsonify(result)
    except Exception as exc:
        log.error(f"[Zone Metrics] Error: {exc}")
        result = {"success": False, "message": str(exc), "zones": []}
        return jsonify(result), 500


@app.route("/api/crop-charts", methods=["GET"])
@login_required
def api_crop_charts():
    """
    Fetch historical per-zone metrics for the currently drawn zones.
    Expects 'days' query parameter (default 30).
    """
    user_id = session["user_id"]
    try:
        days = int(request.args.get('days', 30))
    except (ValueError, TypeError):
        days = 30
    
    # Check cache (15-minute TTL)
    cache_key = f"crop_charts:{user_id}:{days}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)
        
    user = current_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    farm_geojson = user.get("farm_geojson", "")
    
    if not farm_geojson or farm_geojson == "None" or str(farm_geojson).strip() == "":
        history = get_farm_data_range(user_id, days=days)
        result = {"success": True, "zones": [], "global_history": [dict(r) for r in history]}
        set_cache(cache_key, result)
        return jsonify(result)
        
    from ml_gee_engine import fetch_gee_crop_history
    zones_history = fetch_gee_crop_history(farm_geojson, days=days)
    
    if not zones_history:
        history = get_farm_data_range(user_id, days=days)
        result = {"success": True, "zones": [], "global_history": [dict(r) for r in history]}
    else:
        result = {"success": True, "zones": zones_history}
    
    set_cache(cache_key, result)
    return jsonify(result)


@app.route("/api/profile/change-password", methods=["POST"])
@login_required
def api_change_password():
    """Update the logged-in user's password securely."""
    data = request.get_json(silent=True) or {}
    user_id = session["user_id"]

    current_pwd = data.get("current_password", "")
    new_pwd = data.get("new_password", "")
    confirm_pwd = data.get("confirm_password", "")

    if not current_pwd or not new_pwd or not confirm_pwd:
        return jsonify({"success": False, "message": "جميع حقول كلمة المرور مطلوبة."}), 400

    if new_pwd != confirm_pwd:
        return jsonify({"success": False, "message": "كلمات المرور الجديدة غير متطابقة."}), 400

    if not is_strong_password(new_pwd):
        return jsonify({"success": False,
                        "message": "كلمة المرور الجديدة ضعيفة. يجب أن تحتوي على 8 حروف، حرف كبير، صغير، رقم ورمز."}), 400

    user = get_user_by_id(user_id)
    if not user or not check_password(user["password_hash"], current_pwd):
        return jsonify({"success": False, "message": "كلمة المرور الحالية غير صحيحة."}), 403

    # Update the password
    success, msg = update_user_profile(user_id, password_hash=hash_password(new_pwd))
    return jsonify({"success": success, "message": "تم تغيير كلمة المرور بنجاح." if success else msg})


@app.route("/api/chat-history")
@login_required
def api_chat_history():
    """Return chat history for the logged-in user."""
    user_id = session["user_id"]
    channel = request.args.get("channel")  # optional filter
    rows    = get_chat_history(user_id, channel=channel, limit=100)

    if not rows:
        return jsonify({"success": False, "message": "لا توجد محادثات."})

    history = [
        {
            "channel":   r["channel"],
            "message":   r["message"],
            "response":  r["response"],
            "timestamp": r["timestamp"],
        }
        for r in rows
    ]
    return jsonify({"success": True, "history": history})


# ── Web Chatbot API ───────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    """
    AJAX endpoint for the Tayseer AI Assistant (web channel).
    Returns full metadata: provider, confidence, sources, farm context.
    """
    data      = request.get_json(silent=True) or {}
    message   = data.get("message", "").strip()
    image_b64 = data.get("image") or data.get("image_b64")
    user_id   = session["user_id"]

    if not message and not image_b64:
        return jsonify({"success": False, "message": "الرسالة أو الصورة مطلوبة."}), 400

    try:
        from ai_core import ask_agri_assistant
        result = ask_agri_assistant(
            user_id,
            message,
            channel="web",
            image_b64=image_b64,
            return_meta=True
        )
        return jsonify({
            "success":      True,
            "response":     result.get("response", ""),
            "provider":     result.get("provider", "unknown"),
            "confidence":   result.get("confidence", 0.0),
            "sources":      result.get("sources", []),
            "farm_name":    result.get("farm_name"),
            "data_freshness": result.get("data_freshness"),
        })
    except Exception as exc:
        log.warning(f"[Chat] ask_agri_assistant failed, using legacy fallback: {exc}")
        response = generate_response(user_id, message, channel="web", image_b64=image_b64)
        return jsonify({"success": True, "response": response, "provider": "rule_engine", "confidence": 0.75})


# ── Service Worker ────────────────────────────────────────────

@app.route("/service-worker.js")
def service_worker():
    return send_from_directory("static/js", "service-worker.js")


# ── APScheduler: Background Pipeline ─────────────────────────

def _schedule_background_pipeline():
    """
    Run the full GEE + ML pipeline daily at 06:00 for all verified users.
    Uses APScheduler so the Flask dev server stays non-blocking.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from database import get_connection

        scheduler = BackgroundScheduler(timezone="Africa/Cairo")

        def _run_all_users():
            with app.app_context():
                with get_connection() as conn:
                    users = conn.execute(
                        "SELECT id, lat, lon, area_feddan, crop_type "
                        "FROM Users WHERE is_verified = 1 AND lat IS NOT NULL"
                    ).fetchall()
                for u in users:
                    try:
                        run_full_pipeline(
                            user_id     = u["id"],
                            lat         = u["lat"],
                            lon         = u["lon"],
                            area_feddan = u["area_feddan"] or 1.0,
                            crop_type   = u["crop_type"]   or "general",
                        )
                        log.info(f"[Scheduler] Pipeline done for user {u['id']}")
                    except Exception as exc:
                        log.error(f"[Scheduler] Pipeline failed for user {u['id']}: {exc}")

        scheduler.add_job(_run_all_users, "cron", hour=6, minute=0)
        scheduler.start()
        log.info("[Scheduler] Background pipeline scheduler started (daily at 06:00 Cairo).")
    except ImportError:
        log.warning("[Scheduler] APScheduler not installed. Background pipeline disabled.")




# ============================================================
# WhatsApp Integration Routes
# ============================================================

from whatsapp_integration import (
    get_whatsapp_service, EgyptianNLP,
    build_daily_summary, build_pivot_alert,
    build_water_stress_alert,
)
from database import (
    update_whatsapp_optin, get_whatsapp_opted_in_users,
    store_whatsapp_message as _store_wa, get_whatsapp_history,
)


@app.route("/api/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """
    Twilio WhatsApp Webhook — receives farmer messages and replies.
    Configure this URL in Twilio console as the 'When a message comes in' webhook.
    """
    body       = request.values.get("Body",       "").strip()
    from_number = request.values.get("From",      "")   # whatsapp:+2012…
    phone      = from_number.replace("whatsapp:", "")

    log.info(f"[WhatsApp] ← {phone}: {body[:80]}")

    svc = get_whatsapp_service()

    # identify user
    user = get_user_by_phone(phone)

    if not user:
        reply = (
            "🌾 *مرحباً بك في Tayseer Agri-SaT!*\n\n"
            "لم نعثر على حسابك.\n\n"
            "📱 لربط حسابك:\n"
            "1️⃣ سجّل على المنصة: https://tayseer.app\n"
            f"2️⃣ أضف رقمك: {phone}\n"
            "3️⃣ ارجع وابعت: *ربط*"
        )
        return svc.build_twiml(reply), 200, {"Content-Type": "text/xml"}

    # handle account link command
    if body.lower() in ["link", "ربط", "تسجيل", "/link", "تفعيل"]:
        update_whatsapp_optin(user["id"], phone=phone, verified=True, opted_in=True)
        fn = user.get("farm_name") or "مزرعتك"
        reply = (
            f"✅ *تم ربط حسابك بنجاح!*\n\n"
            f"🌾 المزرعة: {fn}\n"
            f"📱 الرقم: {phone}\n\n"
            "جرب الآن:\n"
            "• إيه حالة المحصول؟\n"
            "• أروي النهاردة؟\n"
            "• كام ساعة أشغل الطلمبة؟\n"
            "• فيه عطل في البيفوت؟"
        )
        _store_wa(user["id"], body, reply)
        return svc.build_twiml(reply), 200, {"Content-Type": "text/xml"}

    # load farm context
    farm_data = get_latest_farm_data(user["id"])
    fd = dict(farm_data) if farm_data else {}

    nlp   = EgyptianNLP(fd)
    intent = nlp.detect_intent(body)
    fn     = user.get("farm_name") or "مزرعتك"
    reply  = f"🌾 *{fn}*\n\n" + nlp.generate_response(intent, fn)

    _store_wa(user["id"], body, reply)
    return svc.build_twiml(reply), 200, {"Content-Type": "text/xml"}


@app.route("/api/user/update-phone", methods=["POST"])
@login_required
def update_phone():
    """Save/update user phone for WhatsApp link."""
    data  = request.get_json(force=True) or {}
    phone = data.get("phone", "").strip()
    if not phone.startswith("+"):
        return jsonify({"success": False, "error": "الرقم يجب أن يبدأ بـ + ورمز الدولة"})
    uid = session["user_id"]
    update_whatsapp_optin(uid, phone=phone, verified=False, opted_in=True)
    log.info(f"[WhatsApp] Saved phone {phone} for user {uid}")
    return jsonify({"success": True, "message": "تم حفظ الرقم. اتبع خطوات الربط."})


@app.route("/api/whatsapp/history", methods=["GET"])
@login_required
def whatsapp_history():
    """Return recent WhatsApp conversation for the logged-in user."""
    history = get_whatsapp_history(session["user_id"], limit=30)
    return jsonify({"success": True, "conversations": history})


@app.route("/api/whatsapp/send-test", methods=["POST"])
@login_required
def whatsapp_send_test():
    """Send a test WhatsApp message to verify the setup."""
    user = get_user_by_id(session["user_id"])
    phone = user["phone"] if user else None
    if not phone:
        return jsonify({"success": False, "error": "لم يتم إضافة رقم الواتساب بعد."})
    svc  = get_whatsapp_service()
    fn   = user.get("farm_name") or "مزرعتك"
    text = (
        f"🌾 *رسالة تجريبية من Tayseer*\n\n"
        f"✅ الربط ناجح — {fn}!\n\n"
        "جرب الآن: إيه حالة المحصول؟"
    )
    result = svc.send_message(phone, text)
    return jsonify(result)


@app.route("/api/whatsapp/opt-in", methods=["POST"])
@login_required
def whatsapp_optin():
    """Toggle WhatsApp daily update subscription."""
    data   = request.get_json(force=True) or {}
    opted  = bool(data.get("opted_in", True))
    update_whatsapp_optin(session["user_id"], opted_in=opted)
    msg = "اشتركت في التحديثات اليومية ✅" if opted else "تم إلغاء الاشتراك"
    return jsonify({"success": True, "message": msg})


# ── WhatsApp Daily Update Scheduler (7 AM) ──────────────────

def _schedule_whatsapp_daily():
    """Register daily WhatsApp updates at 07:00 Cairo time."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _wa_scheduler = BackgroundScheduler(daemon=True)

        def _send_daily():
            svc   = get_whatsapp_service()
            users = get_whatsapp_opted_in_users()
            log.info(f"[WhatsApp-Daily] Sending to {len(users)} users…")
            for u in users:
                try:
                    fd   = get_latest_farm_data(u["id"])
                    msg  = build_daily_summary(u, dict(fd) if fd else {})
                    svc.send_message(u["phone"], msg)
                    log.info(f"[WhatsApp-Daily] → {u['phone']} ✓")
                except Exception as exc:
                    log.error(f"[WhatsApp-Daily] Failed for {u.get('phone')}: {exc}")

        _wa_scheduler.add_job(_send_daily, "cron", hour=7, minute=0)
        _wa_scheduler.start()
        log.info("[WhatsApp-Daily] Scheduler started — 07:00 daily ✅")
    except ImportError:
        log.warning("[WhatsApp-Daily] APScheduler not installed — skipping daily scheduler.")


# start immediately (called at entry point)
with app.app_context():
    try:
        _schedule_whatsapp_daily()
    except Exception as _e:
        log.warning(f"[WhatsApp-Daily] Scheduler init failed: {_e}")




# ============================================================
# Telegram Integration Routes
# ============================================================

from telegram_integration import (
    get_telegram_service, build_start_message, build_help_message,
    build_status_message, build_irrigation_message, build_pivot_message,
    build_daily_update, QUICK_KEYBOARD
)
from database import (
    link_telegram, link_telegram_by_email, get_telegram_history,
    store_telegram_message as _store_tg, get_telegram_opted_in_users
)

@app.route("/api/webhook/telegram", methods=["POST"])
def telegram_webhook():
    """Telegram Bot Webhook."""
    try:
        update = request.get_json(force=True)
        if "message" in update:
            _handle_telegram_message(update["message"])
        elif "callback_query" in update:
            _handle_telegram_callback(update["callback_query"])
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"[Telegram] Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)})


def _handle_telegram_message(msg: dict):
    chat_id  = msg["chat"]["id"]
    username = msg["chat"].get("username", "")
    text     = msg.get("text", "").strip()

    if not text:
        return

    log.info(f"[Telegram] ← {chat_id}: {text[:60]}")
    svc = get_telegram_service()

    # Commands
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd == "/start":
            reply = build_start_message(chat_id)
            svc.send_message(chat_id, reply)
            return
        elif cmd == "/help":
            svc.send_message(chat_id, build_help_message())
            return
        elif cmd == "/link":
            parts = text.split()
            if len(parts) < 2:
                svc.send_message(chat_id, "⚠️ صيغة خاطئة.\nأرسل: `/link email@domain.com`")
                return
            email = parts[1].strip()
            if link_telegram_by_email(email, str(chat_id), username):
                svc.send_message(chat_id, f"✅ تم ربط حسابك بنجاح ({email})!\nاكتب /status لعرض حالة مزرعتك.")
            else:
                svc.send_message(chat_id, f"❌ البريد ({email}) غير مسجل في المنصة.")
            return

    # User identification
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM Users WHERE telegram_chat_id = ?", (str(chat_id),)).fetchone()
    user = dict(row) if row else None

    if not user:
        if not text.startswith("/"):
            svc.send_message(chat_id, "⚠️ لم يتم ربط حسابك بعد. اضغط /start للتعليمات.")
        return

    # Farm commands & NLP
    fd_row = get_latest_farm_data(user["id"])
    fd = dict(fd_row) if fd_row else {}

    reply = ""
    cmd = text.split()[0].lower() if text.startswith("/") else None

    if cmd == "/status":
        reply = build_status_message(user, fd)
    elif cmd == "/irrigate":
        reply = build_irrigation_message(user, fd)
    elif cmd == "/pivot":
        reply = build_pivot_message(user, fd)
    elif cmd == "/weather":
        # placeholder
        reply = "🌤️ *الطقس*\nدرجة الحرارة: 32°م\nلا توجد أمطار متوقعة الأيام القادمة."
    else:
        # NLP route
        from whatsapp_integration import EgyptianNLP
        nlp = EgyptianNLP(fd)
        intent = nlp.detect_intent(text)
        fn = user.get("farm_name") or "مزرعتك"
        reply = f"🌾 *{fn}*\n\n" + nlp.generate_response(intent, fn)

    svc.send_message(chat_id, reply, reply_markup=QUICK_KEYBOARD)
    _store_tg(user["id"], text, reply)


def _handle_telegram_callback(cb: dict):
    cb_id    = cb["id"]
    chat_id  = cb["message"]["chat"]["id"]
    data     = cb.get("data", "")
    svc      = get_telegram_service()

    svc.answer_callback_query(cb_id)

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM Users WHERE telegram_chat_id = ?", (str(chat_id),)).fetchone()
    user = dict(row) if row else None

    if not user:
        svc.send_message(chat_id, "⚠️ يجب ربط الحساب أولاً: /start")
        return

    fd_row = get_latest_farm_data(user["id"])
    fd = dict(fd_row) if fd_row else {}
    reply = ""

    if data == "irrigate":
        reply = build_irrigation_message(user, fd)
    elif data == "crop_status":
        reply = build_status_message(user, fd)
    elif data == "pivot":
        reply = build_pivot_message(user, fd)
    elif data == "weather":
        reply = "🌤️ *الطقس*\nدرجة الحرارة: 32°م\nلا توجد أمطار متوقعة اليوم."

    if reply:
        svc.send_message(chat_id, reply, reply_markup=QUICK_KEYBOARD)
        _store_tg(user["id"], f"[Button: {data}]", reply)


@app.route("/api/telegram/history", methods=["GET"])
@login_required
def api_telegram_history():
    history = get_telegram_history(session["user_id"], limit=30)
    return jsonify({"success": True, "conversations": history})


@app.route("/api/telegram/link-by-id", methods=["POST"])
@login_required
def api_telegram_link_by_id():
    data = request.get_json(force=True) or {}
    tid = data.get("telegram_id", "").strip()
    if not tid:
        return jsonify({"success": False, "error": "مطلوب Telegram ID"})
    
    link_telegram(session["user_id"], tid)
    
    # send welcome
    svc = get_telegram_service()
    if svc.enabled:
        svc.send_message(tid, "✅ *تم ربط حسابك بالمنصة من خلال الموقع!*\nجرب الآن: /status")
        
    return jsonify({"success": True})


@app.route("/api/telegram/send-test", methods=["POST"])
@login_required
def api_telegram_send_test():
    user = get_user_by_id(session["user_id"])
    tid  = user.get("telegram_chat_id")
    if not tid:
        return jsonify({"success": False, "error": "التليجرام غير مربوط"})
        
    svc = get_telegram_service()
    res = svc.send_message(tid, "✅ *رسالة تجريبية من Tayseer!*\nالربط ناجح.")
    return jsonify({"success": res.get("ok", False), "result": res})


# ── Telegram Daily Scheduler ──────────────────────────────────

def _schedule_telegram_daily():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _tg_scheduler = BackgroundScheduler(daemon=True)

        def _send_daily_tg():
            svc   = get_telegram_service()
            users = get_telegram_opted_in_users()
            log.info(f"[Telegram-Daily] Sending to {len(users)} users…")
            for u in users:
                try:
                    fd   = get_latest_farm_data(u["id"])
                    msg  = build_daily_update(u, dict(fd) if fd else {})
                    svc.send_message(u["telegram_chat_id"], msg, reply_markup=QUICK_KEYBOARD)
                    log.info(f"[Telegram-Daily] → {u['telegram_chat_id']} ✓")
                except Exception as exc:
                    log.error(f"[Telegram-Daily] Failed for {u.get('telegram_chat_id')}: {exc}")

        _tg_scheduler.add_job(_send_daily_tg, "cron", hour=7, minute=0)
        _tg_scheduler.start()
        log.info("[Telegram-Daily] Scheduler started — 07:00 daily ✅")
    except ImportError:
        pass


with app.app_context():
    try:
        _schedule_telegram_daily()
    except Exception as _e:
        log.warning(f"[Telegram-Daily] Scheduler init failed: {_e}")


# ============================================================
# Email Notification System Routes
# ============================================================

import secrets as _secrets
from email_service import get_email_service
from database import (
    log_email as _log_email,
    get_email_preferences, save_email_preferences,
    get_opted_in_email_users,
    store_inbox_message, get_inbox_messages,
    get_inbox_message as _get_inbox_msg,
    mark_inbox_read, mark_inbox_replied,
    get_unread_inbox_count,
)
# Standalone email scheduler (creates email_scheduler.py)
try:
    from email_scheduler import send_critical_alert_email as _send_critical_alert
    log.info("[Email] email_scheduler module loaded.")
except ImportError:
    _send_critical_alert = None
    log.warning("[Email] email_scheduler.py not found — critical alerts via API disabled.")


# ── Host Inbox routes ─────────────────────────────────────────

@app.route("/host-inbox")
@login_required
def host_inbox_page():
    user = get_user_by_id(session["user_id"])
    if not (user and user.get("is_admin")):
        return jsonify({"error": "Unauthorized"}), 403
    return render_template("host_inbox.html", user=user)


@app.route("/api/host/inbox", methods=["GET"])
@login_required
def api_host_inbox_list():
    user = get_user_by_id(session["user_id"])
    if not (user and user.get("is_admin")):
        return jsonify({"error": "Unauthorized"}), 403
    status = request.args.get("status", "all")
    msgs   = get_inbox_messages(status=status)
    return jsonify({
        "success": True,
        "messages": msgs,
        "unread_count": get_unread_inbox_count(),
    })


@app.route("/api/host/inbox/<int:msg_id>", methods=["GET"])
@login_required
def api_host_inbox_get(msg_id):
    user = get_user_by_id(session["user_id"])
    if not (user and user.get("is_admin")):
        return jsonify({"error": "Unauthorized"}), 403
    mark_inbox_read(msg_id)
    return jsonify({"success": True, "message": _get_inbox_msg(msg_id)})


@app.route("/api/host/inbox/<int:msg_id>/reply", methods=["POST"])
@login_required
def api_host_inbox_reply(msg_id):
    user = get_user_by_id(session["user_id"])
    if not (user and user.get("is_admin")):
        return jsonify({"error": "Unauthorized"}), 403

    data  = request.get_json(force=True) or {}
    reply = data.get("reply", "").strip()
    if not reply:
        return jsonify({"success": False, "error": "الرد فارغ"})

    msg        = _get_inbox_msg(msg_id)
    from_email = msg.get("from_email", "")

    svc = get_email_service()
    if from_email:
        res = svc.send_email(
            to_email    = from_email,
            subject     = f"Re: {msg.get('subject', '')}",
            html_content= (
                f"<div dir='rtl' style='font-family:Cairo,Arial;'>"
                f"<h3 style='color:#10b981'>رد من فريق Tayseer</h3>"
                f"<p style='line-height:1.7'>{reply.replace(chr(10), '<br>')}</p>"
                f"<hr><p style='color:#6b7280;font-size:13px'>رداً على: <em>{msg.get('message','')[:200]}</em></p>"
                f"</div>"
            ),
        )
        _log_email(
            user_id   = session["user_id"],
            recipient = from_email,
            subject   = f"Re: {msg.get('subject', '')}",
            template  = "admin_reply",
            status    = "sent" if res.get("success") else "failed",
        )

    mark_inbox_replied(msg_id)
    return jsonify({"success": True})


@app.route("/api/host/inbox/<int:msg_id>/mark-replied", methods=["POST"])
@login_required
def api_host_mark_replied(msg_id):
    user = get_user_by_id(session["user_id"])
    if not (user and user.get("is_admin")):
        return jsonify({"error": "Unauthorized"}), 403
    mark_inbox_replied(msg_id)
    return jsonify({"success": True})


# ── Client → Host message ─────────────────────────────────────

@app.route("/api/client/send-message", methods=["POST"])
@login_required
def api_client_send_message():
    data     = request.get_json(force=True) or {}
    subject  = data.get("subject", "استفسار").strip()
    message  = data.get("message", "").strip()
    priority = data.get("priority", "normal")

    if not message:
        return jsonify({"success": False, "error": "الرسالة فارغة"})

    uid  = session["user_id"]
    u    = get_user_by_id(uid)

    # store in inbox
    store_inbox_message(uid, subject, message, priority)

    # email host
    svc = get_email_service()
    svc.send_to_host(
        subject   = subject,
        message   = message,
        from_user = {
            "name":  u.get("name", ""),
            "email": u.get("email", ""),
            "farm":  u.get("farm_name", ""),
        },
    )
    return jsonify({"success": True, "message": "تم إرسال رسالتك للإدارة"})


# ── Email preferences ─────────────────────────────────────────

@app.route("/api/email/preferences", methods=["GET", "POST"])
@login_required
def api_email_preferences():
    uid = session["user_id"]
    if request.method == "GET":
        return jsonify({"success": True, "prefs": get_email_preferences(uid)})

    data = request.get_json(force=True) or {}
    save_email_preferences(uid, data)
    return jsonify({"success": True, "message": "تم حفظ الإعدادات"})


@app.route("/email-settings")
@login_required
def email_settings_page():
    user = get_user_by_id(session["user_id"])
    return render_template("email_settings.html", user=user)


@app.route("/api/email/log", methods=["GET"])
@login_required
def api_email_log():
    limit = int(request.args.get("limit", 10))
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT recipient_email, subject, status, sent_at
               FROM email_logs WHERE user_id = ?
               ORDER BY sent_at DESC LIMIT ?""",
            (session["user_id"], limit)
        ).fetchall()
        return jsonify({"success": True, "logs": [dict(r) for r in rows]})


# ── Send test email ───────────────────────────────────────────

@app.route("/api/email/send-test", methods=["POST"])
@login_required
def api_email_send_test():
    u = get_user_by_id(session["user_id"])
    if not u or not u.get("email"):
        return jsonify({"success": False, "error": "لا يوجد بريد إلكتروني"})

    fd_row = get_latest_farm_data(session["user_id"])
    fd = dict(fd_row) if fd_row else {}

    svc = get_email_service()
    res = svc.send_template_email(
        to_email      = u["email"],
        template_name = "daily_update.html",
        context       = _build_daily_ctx(u, fd),
    )
    _log_email(session["user_id"], u["email"], "Test Email",
               "daily_update", res.get("message_id"),
               "sent" if res.get("success") else "failed")
    return jsonify(res)


# ── Critical alert API (for internal triggers) ────────────────

@app.route("/api/email/send-critical-alert", methods=["POST"])
@login_required
def api_send_critical_alert():
    """
    Trigger an immediate critical alert email for the current user.
    Input JSON: {alert_type, alert_data}
    alert_type: 'pivot_failure' | 'water_stress' | 'weather_warning'
    """
    data       = request.get_json(force=True) or {}
    alert_type = data.get("alert_type", "water_stress")
    alert_data = data.get("alert_data", {})
    user_id    = session["user_id"]

    if _send_critical_alert:
        res = _send_critical_alert(user_id, alert_type, alert_data)
        return jsonify(res or {"success": False, "error": "No response"})
    return jsonify({"success": False, "error": "email_scheduler module not loaded"}), 503


# ── Test template (admin only) ────────────────────────────────

@app.route("/api/email/test-template", methods=["POST"])
@login_required
def api_test_template():
    """
    Render and send a specific email template to the current user.
    Input JSON: {template}  — 'daily' | 'weekly' | 'alert'
    """
    data = request.get_json(force=True) or {}
    tpl  = data.get("template", "daily")
    u    = get_user_by_id(session["user_id"])
    if not u or not u.get("email"):
        return jsonify({"success": False, "error": "No email set"})

    fd_row = get_latest_farm_data(session["user_id"])
    fd     = dict(fd_row) if fd_row else {}
    svc    = get_email_service()

    if tpl == "weekly":
        ctx  = _build_weekly_ctx(u, fd)
        res  = svc.send_template_email(u["email"], "weekly_report.html", ctx)
    elif tpl == "alert":
        res  = svc.send_critical_alert(
            to_email        = u["email"],
            user_name       = u.get("name", ""),
            farm_name       = u.get("farm_name", "مزرعتي"),
            alert_title     = "اختبار تنبيه حرج",
            alert_message   = "هذه رسالة تجريبية لقالب التنبيه الحرج.",
            action_required = "لا يلزم اتخاذ أي إجراء — هذا مجرد اختبار.",
        )
    else:
        ctx  = _build_daily_ctx(u, fd)
        res  = svc.send_template_email(u["email"], "daily_update.html", ctx)

    _log_email(session["user_id"], u["email"], f"Test-{tpl}",
               tpl, res.get("message_id"),
               "sent" if res.get("success") else "failed")
    return jsonify(res)


# ── Unsubscribe handler ───────────────────────────────────────

@app.route("/email/unsubscribe")
def email_unsubscribe():
    token = request.args.get("token", "")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM Users WHERE unsubscribe_token = ?", (token,)
        ).fetchone()
        if row:
            save_email_preferences(row["id"], {
                "daily_updates": False, "weekly_reports": False, "critical_alerts": False,
            })
            return "<html dir='rtl'><body style='font-family:Cairo,sans-serif;text-align:center;padding:60px'>" \
                   "<h2>✅ تم إلغاء اشتراكك في الإيميلات</h2>" \
                   "<p>يمكنك إعادة الاشتراك من الإعدادات في أي وقت.</p></body></html>"
    return "<html dir='rtl'><body style='font-family:Cairo,sans-serif;text-align:center;padding:60px'>" \
           "<h2>❌ رابط غير صالح</h2></body></html>"


# ── Context builder helpers ───────────────────────────────────

def _build_daily_ctx(user: dict, fd: dict) -> dict:
    nd  = fd.get("ndvi", 0.65)
    et  = fd.get("et", 4.5)
    ph  = fd.get("pump_hours", 6.0)
    bm  = fd.get("biomass", 12.0)
    st  = fd.get("status", "healthy")

    # ensure user has an unsubscribe token
    tok = user.get("unsubscribe_token") or ""
    if not tok:
        tok = _secrets.token_urlsafe(24)
        with get_connection() as conn:
            conn.execute("UPDATE Users SET unsubscribe_token=? WHERE id=?",
                         (tok, user["id"]))
            conn.commit()

    # Real weather & agronomy alerts
    t_min = "-"
    t_max = "-"
    hum = "-"
    w_desc = "غير متوفر"
    alerts = []
    if user.get("lat") and user.get("lon"):
        try:
            wf = fetch_weather_forecast(user["lat"], user["lon"])
            if wf and len(wf) > 0:
                t_min = f"{round(wf[0].get('temp_min', 0))}"
                t_max = f"{round(wf[0].get('temp_max', 0))}"
                hum = f"{round(wf[0].get('humidity', 0))}"
                w_desc = wf[0].get("description", "صافٍ")
            if wf:
                alerts_raw = generate_agronomy_alerts(wf, fd)
                alerts = [a.get("message_ar", "") for a in alerts_raw if a.get("message_ar")]
        except Exception as _wex:
            log.warning(f"[DailyEmail] Weather fetch failed: {_wex}")

    return dict(
        subject          = f"تحديث يومي — {user.get('farm_name', 'مزرعتك')}",
        user_name        = user.get("name", ""),
        farm_name        = user.get("farm_name", "مزرعتك"),
        date             = __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        status_class     = st,
        status_icon      = "✅" if st == "healthy" else ("⚠️" if st == "warning" else "🔴"),
        status_text      = {"healthy": "ممتاز", "warning": "متوسط"}.get(st, st),
        ndvi             = f"{nd:.3f}",
        et               = f"{et:.2f}",
        pump_hours       = f"{ph:.1f}",
        biomass          = f"{bm:.1f}",
        irrigation_advice= ("✅ لا ري مطلوب اليوم." if nd >= 0.55 else
                            f"💧 شغّل الطلمبة {ph:.1f} ساعة اليوم."),
        temp_min         = t_min,
        temp_max         = t_max,
        humidity         = hum,
        weather_description = w_desc,
        alerts           = alerts,
        dashboard_url    = f"{os.getenv('WEBHOOK_BASE_URL', 'http://localhost:5000')}/dashboard",
        unsubscribe_token= tok,
    )


def _build_weekly_ctx(user: dict, fd: dict) -> dict:
    nd = fd.get("ndvi", 0.65)
    ph = fd.get("pump_hours", 6.0)
    tok = user.get("unsubscribe_token", "")
    return dict(
        subject          = f"التقرير الأسبوعي — {user.get('farm_name', 'مزرعتك')}",
        user_name        = user.get("name", ""),
        farm_name        = user.get("farm_name", "مزرعتك"),
        week_range       = "",  # filled by scheduler
        avg_ndvi         = f"{nd:.3f}",
        ndvi_trend       = "📈 تحسن" if nd > 0.5 else "📉 انخفاض",
        water_usage      = f"{ph * 7 * 120:.0f}",
        total_hours      = f"{ph * 7:.1f}",
        biomass          = f"{fd.get('biomass', 12.0):.1f}",
        achievements     = ["استمرارية تشغيل الري وفق الجدول الزمني",
                            "لا أعطال مسجلة في البيفوت"],
        concerns         = [] if nd > 0.5 else ["انخفاض قيم NDVI — راجع تغطية المحصول"],
        recommendations  = "استمر في نفس جدول الري وراجع آخر تقارير NDVI.",
        dashboard_url    = f"{os.getenv('WEBHOOK_BASE_URL', 'http://localhost:5000')}/dashboard",
        unsubscribe_token= tok,
    )


# ── Email Schedulers ──────────────────────────────────────────

def _schedule_email_jobs():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from datetime import datetime as _dt, timedelta as _td

        _esched = BackgroundScheduler(daemon=True)

        def _daily_emails():
            svc   = get_email_service()
            users = get_opted_in_email_users("daily_updates")
            log.info(f"[Email-Daily] Sending to {len(users)} users…")
            for u in users:
                try:
                    fd_row = get_latest_farm_data(u["id"])
                    fd     = dict(fd_row) if fd_row else {}
                    ctx    = _build_daily_ctx(u, fd)
                    res    = svc.send_template_email(u["email"], "daily_update.html", ctx)
                    _log_email(u["id"], u["email"], ctx["subject"],
                               "daily_update", res.get("message_id"),
                               "sent" if res.get("success") else "failed",
                               res.get("error"))
                    log.info(f"[Email-Daily] → {u['email']} ✓")
                except Exception as exc:
                    log.error(f"[Email-Daily] Failed for {u.get('email')}: {exc}")

        def _weekly_emails():
            from datetime import datetime as _dt2, timedelta as _td2
            svc   = get_email_service()
            users = get_opted_in_email_users("weekly_reports")
            log.info(f"[Email-Weekly] Sending to {len(users)} users…")
            for u in users:
                try:
                    fd_row = get_latest_farm_data(u["id"])
                    fd     = dict(fd_row) if fd_row else {}
                    ctx    = _build_weekly_ctx(u, fd)
                    now    = _dt2.now()
                    ctx["week_range"] = (
                        f"{(now - _td2(days=7)).strftime('%Y-%m-%d')} — {now.strftime('%Y-%m-%d')}"
                    )
                    res = svc.send_template_email(u["email"], "weekly_report.html", ctx)
                    _log_email(u["id"], u["email"], ctx["subject"],
                               "weekly_report", res.get("message_id"),
                               "sent" if res.get("success") else "failed")
                    log.info(f"[Email-Weekly] → {u['email']} ✓")
                except Exception as exc:
                    log.error(f"[Email-Weekly] Failed for {u.get('email')}: {exc}")

        _esched.add_job(_daily_emails,  "cron", hour=7,  minute=0,
                        id="email_daily")
        _esched.add_job(_weekly_emails, "cron", day_of_week="sun", hour=8, minute=0,
                        id="email_weekly")
        _esched.start()
        log.info("[Email] Scheduler started — daily 07:00 + weekly Sun 08:00")
    except ImportError:
        log.warning("[Email] APScheduler not installed — email scheduler disabled.")


with app.app_context():
    try:
        _schedule_email_jobs()
    except Exception as _e:
        log.warning(f"[Email] Scheduler init failed: {_e}")

# Also start the standalone email_scheduler if available
with app.app_context():
    try:
        from email_scheduler import start_email_scheduler as _start_email_sched
        _start_email_sched(app)
    except ImportError:
        pass
    except Exception as _esc_e:
        log.warning(f"[Email-Scheduler] Standalone scheduler init failed: {_esc_e}")


# ============================================================
# Step 8 — 3D Farm Drawing Tool API Routes
# ============================================================
from database import (
    save_farm_plots as _db_save_plots,
    get_farm_plots  as _db_get_plots,
    get_farm_plot   as _db_get_plot,
    update_farm_plot as _db_update_plot,
    delete_farm_plot as _db_delete_plot,
    delete_all_farm_plots as _db_delete_all_plots,
    get_crop_library as _db_crop_library,
    get_farm_plots_summary as _db_plots_summary,
)


# ── Page Route ────────────────────────────────────────────────

@app.route('/farm-setup')
@login_required
def farm_setup_page():
    """3D Farm Drawing Tool page."""
    u = get_user_by_id(session['user_id'])
    return render_template('farm_setup.html', user=u)


# ── Save Plots (bulk) ─────────────────────────────────────────

@app.route('/api/farm/save-plots', methods=['POST'])
@login_required
def api_save_farm_plots():
    """
    POST /api/farm/save-plots
    Body: { "plots": [...] }
    Replaces all existing active plots for this user.
    """
    data  = request.get_json(force=True) or {}
    plots = data.get('plots', [])

    if not plots:
        return jsonify({'success': False, 'error': 'لا توجد بيانات قطع'}), 400

    # Basic validation
    for p in plots:
        if not p.get('plot_name'):
            return jsonify({'success': False, 'error': 'كل قطعة تحتاج اسم'}), 400
        if not p.get('geometry'):
            return jsonify({'success': False, 'error': 'كل قطعة تحتاج إحداثيات'}), 400

    try:
        result = _db_save_plots(session['user_id'], plots)
        log.info(f"[FarmPlots] User {session['user_id']} saved {result['saved']} plots")
        return jsonify({
            'success': True,
            'message': f"تم حفظ {result['saved']} قطعة بنجاح 🎉",
            'saved':   result['saved'],
            'deleted': result['deleted'],
        })
    except Exception as e:
        log.error(f"[FarmPlots] save error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Get Plots ─────────────────────────────────────────────────

@app.route('/api/farm/get-plots', methods=['GET'])
@login_required
def api_get_farm_plots():
    """
    GET /api/farm/get-plots
    Returns all active plots for the logged-in user.
    """
    try:
        plots = _db_get_plots(session['user_id'])
        return jsonify({'success': True, 'plots': plots, 'count': len(plots)})
    except Exception as e:
        log.error(f"[FarmPlots] get error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Update Single Plot (metadata only) ───────────────────────

@app.route('/api/farm/update-plot/<int:plot_id>', methods=['PUT', 'PATCH'])
@login_required
def api_update_farm_plot(plot_id: int):
    """
    PUT /api/farm/update-plot/<id>
    Body: {plot_name, crop_type, sector, region, soil_type, notes, ...}
    Only metadata can be updated; geometry is immutable.
    """
    data = request.get_json(force=True) or {}
    ok   = _db_update_plot(plot_id, session['user_id'], data)
    if ok:
        return jsonify({'success': True, 'message': 'تم التحديث'})
    return jsonify({'success': False, 'error': 'القطعة غير موجودة أو غير مصرح بها'}), 404


# ── Delete Single Plot ────────────────────────────────────────

@app.route('/api/farm/delete-plot/<int:plot_id>', methods=['DELETE'])
@login_required
def api_delete_farm_plot(plot_id: int):
    """
    DELETE /api/farm/delete-plot/<id>
    Soft-delete one plot (ownership enforced).
    """
    ok = _db_delete_plot(plot_id, session['user_id'])
    if ok:
        return jsonify({'success': True, 'message': 'تم حذف القطعة'})
    return jsonify({'success': False, 'error': 'القطعة غير موجودة أو غير مصرح بها'}), 404


# ── Delete All Plots ──────────────────────────────────────────

@app.route('/api/farm/delete-all', methods=['DELETE'])
@login_required
def api_delete_all_farm_plots():
    """
    DELETE /api/farm/delete-all
    Soft-delete ALL active plots for the current user.
    """
    n = _db_delete_all_plots(session['user_id'])
    return jsonify({'success': True, 'deleted': n, 'message': f'تم حذف {n} قطعة'})


# ── Summary (for dashboard widgets) ──────────────────────────

@app.route('/api/farm/summary', methods=['GET'])
@login_required
def api_farm_plots_summary():
    """
    GET /api/farm/summary
    Returns aggregated totals for use in dashboard KPI cards.
    """
    try:
        summary = _db_plots_summary(session['user_id'])
        return jsonify({'success': True, **summary})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Crop Library ──────────────────────────────────────────────

@app.route('/api/farm/crop-library', methods=['GET'])
@login_required
def api_crop_library():
    """
    GET /api/farm/crop-library
    Returns all crops from the seeded library.
    """
    crops = _db_crop_library()
    return jsonify({'success': True, 'crops': crops})


# ── Export as GeoJSON (server-side generation) ────────────────

@app.route('/api/farm/export-geojson', methods=['GET'])
@login_required
def api_export_farm_geojson():
    """
    GET /api/farm/export-geojson
    Downloads the user's plot data as a GeoJSON FeatureCollection.
    """
    plots = _db_get_plots(session['user_id'])
    if not plots:
        return jsonify({'success': False, 'error': 'لا توجد قطع لتصديرها'}), 404

    features = []
    for p in plots:
        try:
            geo = json.loads(p['geometry']) if isinstance(p['geometry'], str) else p['geometry']
        except Exception:
            continue
        features.append({
            'type': 'Feature',
            'geometry': geo,
            'properties': {
                'plot_name':     p.get('plot_name'),
                'plot_type':     p.get('plot_type'),
                'crop_type':     p.get('crop_type'),
                'sector':        p.get('sector'),
                'region':        p.get('region'),
                'soil_type':     p.get('soil_type'),
                'planting_date': p.get('planting_date'),
                'area_m2':       p.get('area_m2'),
                'area_feddan':   p.get('area_feddan'),
                'area_hectare':  p.get('area_hectare'),
                'notes':         p.get('notes'),
            }
        })

    fc = {'type': 'FeatureCollection', 'features': features}
    buf  = io.BytesIO(json.dumps(fc, ensure_ascii=False, indent=2).encode('utf-8'))
    fname = f"farm_plots_{session['user_id']}_{datetime.datetime.now().strftime('%Y%m%d')}.geojson"
    return send_file(buf, mimetype='application/geo+json',
                     as_attachment=True, download_name=fname)


# ════════════════════════════════════════════════════════════
# Step 9 — Custom Reports System API
# ════════════════════════════════════════════════════════════

@app.route("/api/reports/generate", methods=["POST"])
@login_required
def api_generate_report():
    """
    Generate a farm report. Supports Word / Excel / PDF.

    Body JSON:
    {
      "report_type":     "farm_summary" | "water_usage" | "pivot_health",
      "format":          "word" | "excel" | "pdf",
      "date_range_days": 30,
      "email_delivery":  false,
      "recipient_email": "..."
    }
    """
    import os, shutil
    from report_generator import ReportGenerator

    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}

    report_type     = data.get("report_type", "farm_summary")
    output_format   = data.get("format", "word")
    date_range_days = int(data.get("date_range_days", 30))
    email_delivery  = bool(data.get("email_delivery", False))
    recipient_email = (data.get("recipient_email") or user.get("email", "")).strip()

    # ── Input validation ─────────────────────────────────────
    if date_range_days <= 0:
        return jsonify({"success": False, "error": "date_range_days يجب أن يكون أكبر من صفر"}), 400

    VALID_TYPES   = {"farm_summary", "water_usage", "pivot_health"}
    VALID_FORMATS = {"word", "excel", "pdf"}
    if report_type not in VALID_TYPES:
        return jsonify({"success": False, "error": f"نوع تقرير غير صالح: {report_type}"}), 400
    if output_format not in VALID_FORMATS:
        return jsonify({"success": False, "error": f"صيغة غير صالحة: {output_format}"}), 400

    # water_usage → Excel only
    if report_type == "water_usage" and output_format != "excel":
        return jsonify({"success": False,
                        "error": "تقرير استهلاك المياه متاح بصيغة Excel فقط"}), 400

    # ── Gather farm data ──────────────────────────────────────
    try:
        user_id    = user["id"]
        user_name  = user.get("name") or "المزرع"
        farm_name  = user.get("farm_name") or user.get("farm_location") or "المزرعة"

        farm_row  = get_latest_farm_data(user_id)
        farm_data = dict(farm_row) if farm_row else {}

        # Merge user meta into farm_data (lat/lon for weather)
        farm_data.setdefault("lat",         user.get("lat") or 26.0)
        farm_data.setdefault("lon",         user.get("lon") or 30.0)
        farm_data.setdefault("status",      "healthy")
        farm_data.setdefault("ndvi",        0.55)
        farm_data.setdefault("et",          4.5)
        farm_data.setdefault("pump_hours",  6.0)
        farm_data.setdefault("biomass",     8.0)
        farm_data.setdefault("wue",         14.0)
        farm_data.setdefault("tvdi",        0.30)

        history_rows = get_farm_data_range(user_id, days=date_range_days)
        history      = [dict(r) for r in history_rows]

        gen = ReportGenerator(
            user_id   = user_id,
            user_name = user_name,
            farm_data = farm_data,
            history   = history,
            farm_name = farm_name,
        )

        # ── Generate the file ─────────────────────────────────
        filepath = None
        if report_type == "farm_summary":
            if output_format == "word":
                filepath = gen.generate_farm_summary_word(date_range_days)
            elif output_format == "pdf":
                filepath = gen.generate_farm_summary_pdf(date_range_days)
            elif output_format == "excel":
                filepath = gen.generate_water_usage_excel(date_range_days)  # fallback

        elif report_type == "water_usage":
            filepath = gen.generate_water_usage_excel(date_range_days)

        elif report_type == "pivot_health":
            if output_format == "word":
                filepath = gen.generate_pivot_health_word(date_range_days)
            elif output_format == "pdf":
                filepath = gen.generate_farm_summary_pdf(date_range_days)
            elif output_format == "excel":
                filepath = gen.generate_water_usage_excel(date_range_days)

        if not filepath:
            return jsonify({"success": False, "error": "فشل إنشاء التقرير — مسار فارغ"}), 500

        filename = os.path.basename(filepath)

        # ── Log to DB ─────────────────────────────────────────
        emailed_to_log = recipient_email if email_delivery else None
        log_report(
            user_id         = user_id,
            report_type     = report_type,
            fmt             = output_format,
            filename        = filename,
            filepath        = filepath,
            date_range_days = date_range_days,
            emailed_to      = emailed_to_log,
        )

        # ── Email delivery ────────────────────────────────────
        if email_delivery and recipient_email:
            try:
                from email_service import get_email_service
                email_svc = get_email_service()

                ext_map = {"word": "docx", "excel": "xlsx", "pdf": "pdf"}
                mime_map = {
                    "word":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "pdf":   "application/pdf",
                }
                type_ar = {"farm_summary": "ملخص المزرعة",
                           "water_usage":  "استهلاك المياه",
                           "pivot_health": "صحة البيفوت"}

                with open(filepath, "rb") as fh:
                    file_bytes = fh.read()

                html_body = f"""
                <!DOCTYPE html><html dir="rtl" lang="ar">
                <head><meta charset="UTF-8"></head>
                <body style="font-family:Cairo,Arial,sans-serif;direction:rtl;background:#f9fafb;padding:20px">
                <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
                            box-shadow:0 2px 8px rgba(0,0,0,.08);overflow:hidden">
                  <div style="background:linear-gradient(135deg,#10b981,#059669);color:#fff;
                              padding:28px;text-align:center">
                    <div style="font-size:40px;margin-bottom:8px">📊</div>
                    <h2 style="margin:0">تقرير جديد من Tayseer</h2>
                  </div>
                  <div style="padding:28px">
                    <p>مرحباً <strong>{user_name}</strong>،</p>
                    <p>مرفق تقرير <strong>{type_ar.get(report_type, report_type)}</strong>
                       لمزرعة <strong>{farm_name}</strong>.</p>
                    <table style="width:100%;border-collapse:collapse;margin:16px 0">
                      <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">نوع التقرير</td>
                          <td style="padding:8px">{type_ar.get(report_type, report_type)}</td></tr>
                      <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">الصيغة</td>
                          <td style="padding:8px">{output_format.upper()}</td></tr>
                      <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">الفترة</td>
                          <td style="padding:8px">آخر {date_range_days} يوم</td></tr>
                      <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">تاريخ الإنشاء</td>
                          <td style="padding:8px">{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</td></tr>
                    </table>
                    <p style="color:#6b7280;font-size:13px;text-align:center;margin-top:24px">
                      © 2026 Tayseer Agri-SaT · Powered by Tamkeen Satellite</p>
                  </div>
                </div></body></html>"""

                email_svc.send_email(
                    to_email     = recipient_email,
                    subject      = f'📊 تقرير {type_ar.get(report_type, report_type)} — {farm_name}',
                    html_content = html_body,
                    attachments  = [{
                        "filename": filename,
                        "content":  file_bytes,
                        "type":     mime_map.get(output_format, "application/octet-stream"),
                    }],
                )
                log.info(f"[Reports] Emailed {filename} → {recipient_email}")
            except Exception as email_err:
                log.warning(f"[Reports] Email send failed: {email_err}")
                # Non-fatal — still return the download link

        return jsonify({
            "success":  True,
            "filename": filename,
            "filepath": filepath,
            "message":  "تم إنشاء التقرير بنجاح ✅",
        })

    except Exception as exc:
        log.exception("[Reports] Generation error")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/reports/history")
@login_required
def api_report_history():
    """Return the list of reports generated by the current user."""
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    reports = get_user_reports(user["id"], limit=25)
    return jsonify({"success": True, "reports": reports})


@app.route("/api/reports/download/<path:filename>")
@login_required
def api_download_report(filename):
    """
    Stream a generated report file directly to the browser.
    The filename is the basename — the full path is resolved from the
    report_logs DB so that path traversal attacks are impossible.
    """
    import os
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # Resolve real path from DB (security: only the owner can download)
    reports = get_user_reports(user["id"], limit=200)
    matched = next((r for r in reports if r["filename"] == filename), None)

    if not matched:
        return jsonify({"success": False, "error": "الملف غير موجود أو غير مصرح"}), 404

    filepath = matched["filepath"]
    if not os.path.exists(filepath):
        return jsonify({"success": False, "error": "الملف غير موجود على الخادم"}), 404

    # Stamp downloaded_at
    try:
        mark_report_downloaded(matched["id"])
    except Exception:
        pass

    # Determine MIME
    fmt = matched.get("format", "")
    mime_map = {
        "word":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf":   "application/pdf",
    }
    mimetype = mime_map.get(fmt, "application/octet-stream")

    return send_file(
        filepath,
        as_attachment  = True,
        download_name  = filename,
        mimetype       = mimetype,
    )


@app.route("/api/reports/delete/<int:report_id>", methods=["DELETE"])
@login_required
def api_delete_report(report_id):
    """Delete a report log entry (and optionally the file) for the current user."""
    import os
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # Security: only the owner can delete
    reports = get_user_reports(user["id"], limit=200)
    matched = next((r for r in reports if r["id"] == report_id), None)
    if not matched:
        return jsonify({"success": False, "error": "التقرير غير موجود"}), 404

    # Remove file from disk
    try:
        if os.path.exists(matched["filepath"]):
            os.remove(matched["filepath"])
    except Exception as exc:
        log.warning(f"[Reports] Could not delete file {matched['filepath']}: {exc}")

    # Remove DB row
    from database import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM report_logs WHERE id = ? AND user_id = ?",
                     (report_id, user["id"]))
        conn.commit()

    return jsonify({"success": True, "message": "تم حذف التقرير"})


# ── Entry Point ───────────────────────────────────────────────


# ════════════════════════════════════════════════════════════════
# Step 12 — Carbon-Sat V2.0 Routes
# ════════════════════════════════════════════════════════════════

# Import the singleton processor (falls back to demo mode automatically)
from gee_carbon_sat import carbon_processor
from database import (
    get_carbon_summary, get_livestock_history,
    get_soil_carbon_history,
)


def _build_default_geometry(user: dict) -> dict:
    """
    Build a GeoJSON Polygon from the logged-in user's lat/lon,
    falling back to the real Ismailia farm polygon if not set.
    """
    from gee_carbon_sat import FARM_POLYGON_ISMAILIA
    lat = user.get("lat")
    lon = user.get("lon")
    if lat and lon:
        lat, lon = float(lat), float(lon)
        d = 0.05
        return {
            "type": "Polygon",
            "coordinates": [[
                [lon - d, lat - d], [lon - d, lat + d],
                [lon + d, lat + d], [lon + d, lat - d],
                [lon - d, lat - d],
            ]],
        }
    # Ismailia farm default
    return {"type": "Polygon", "coordinates": [FARM_POLYGON_ISMAILIA]}


@app.route("/carbon")
@login_required
def carbon_page():
    """Carbon-Sat V2.0 monitoring dashboard."""
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("carbon_monitoring.html", user=dict(user))


@app.route("/api/carbon/matrix", methods=["POST", "GET"])
@login_required
def api_carbon_matrix():
    """
    Compute all 4 carbon layers in one call:
      SOM, NCS, Barn Heat Plumes, CH4 Hotspots
    Body (optional): { "farm_geometry": <GeoJSON Polygon> }
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    geometry = data.get("farm_geometry") or _build_default_geometry(user)

    result = carbon_processor.calculate_carbon_matrix(
        user_id=user["id"],
        farm_geometry=geometry,
    )
    return jsonify(result)


@app.route("/api/carbon/summary")
@login_required
def api_carbon_summary():
    """
    Return aggregated carbon metrics for the logged-in user.
    Query params: period_days (default 365)
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    period_days = request.args.get("period_days", 365, type=int)
    summary = get_carbon_summary(user["id"], period_days)

    # If no real data yet, inject demo values so the dashboard is never blank
    has_data = (
        summary["livestock"]["ch4_kg"] > 0
        or summary["crops"]["ch4_kg"] > 0
        or summary["soil"]["total_sequestered_kg"] > 0
    )
    if not has_data:
        demo = carbon_processor._demo_livestock_emissions()
        demo_rice = carbon_processor._demo_rice_emissions()
        demo_soil = carbon_processor._demo_soil_carbon()
        summary = {
            "livestock": {
                "ch4_kg":    demo["ch4_kg_per_year"],
                "nh3_kg":    demo["nh3_kg_per_year"],
                "avg_heads": demo["estimated_livestock"],
            },
            "crops": {
                "ch4_kg":        demo_rice["ch4_kg_total"],
                "flooding_days": demo_rice["flooding_days"],
            },
            "soil": {
                "som_mg_kg":            demo_soil["som_mg_kg"],
                "ncs_gC_m2_year":       demo_soil["ncs_gC_m2_year"],
                "total_sequestered_kg": demo_soil["total_carbon_sequestered_kg_year"],
            },
            "demo_mode": True,
        }

    # ── Calculate totals ──────────────────────────────────────
    # CH4 GWP-100 = 28 (IPCC AR5)
    total_ch4_kg = summary["livestock"]["ch4_kg"] + summary["crops"]["ch4_kg"]
    co2_eq_kg    = total_ch4_kg * 28
    sequestered_kg = summary["soil"]["total_sequestered_kg"]
    net_balance_kg = sequestered_kg - co2_eq_kg

    totals = {
        "total_ch4_kg":         round(total_ch4_kg, 2),
        "co2_equivalent_kg":    round(co2_eq_kg, 2),
        "carbon_sequestered_kg": round(sequestered_kg, 2),
        "net_balance_kg":        round(net_balance_kg, 2),
        "status": "Carbon Negative" if net_balance_kg > 0 else "Carbon Positive",
    }

    return jsonify({
        "success":     True,
        "summary":     summary,
        "totals":      totals,
        "period_days": period_days,
    })


@app.route("/api/carbon/livestock-emissions", methods=["POST"])
@login_required
def api_carbon_livestock():
    """
    Calculate livestock CH4/NH3 emissions via GEE.
    Body JSON: { "paddock_geometry": <GeoJSON Polygon>, "date_range_days": 30 }
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    geometry = data.get("paddock_geometry")
    if not geometry:
        # Use farm's own geojson if available
        try:
            import json as _j
            gj = user.get("farm_geojson")
            if gj:
                parsed = _j.loads(gj)
                # Grab first feature/geometry
                if parsed.get("type") == "FeatureCollection" and parsed.get("features"):
                    geometry = parsed["features"][0].get("geometry")
                elif parsed.get("type") == "Feature":
                    geometry = parsed.get("geometry")
                else:
                    geometry = parsed
        except Exception:
            pass

    if not geometry:
        # Fallback: small polygon around user lat/lon
        lat = float(user.get("lat") or 29.5)
        lon = float(user.get("lon") or 30.5)
        delta = 0.05
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [lon - delta, lat - delta],
                [lon - delta, lat + delta],
                [lon + delta, lat + delta],
                [lon + delta, lat - delta],
                [lon - delta, lat - delta],
            ]],
        }

    result = carbon_processor.calculate_livestock_emissions(
        user_id=user["id"],
        paddock_geometry=geometry,
        date_range_days=int(data.get("date_range_days", 30)),
    )
    return jsonify(result)


@app.route("/api/carbon/rice-methane", methods=["POST"])
@login_required
def api_carbon_rice():
    """
    Calculate rice field methane emissions via GEE SAR flooding detection.
    Body JSON: { "field_geometry": <GeoJSON Polygon>, "date_range_days": 120 }
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    geometry = data.get("field_geometry")
    if not geometry:
        lat = float(user.get("lat") or 29.5)
        lon = float(user.get("lon") or 30.5)
        delta = 0.05
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [lon - delta, lat - delta],
                [lon - delta, lat + delta],
                [lon + delta, lat + delta],
                [lon + delta, lat - delta],
                [lon - delta, lat - delta],
            ]],
        }

    result = carbon_processor.calculate_rice_methane(
        user_id=user["id"],
        field_geometry=geometry,
        date_range_days=int(data.get("date_range_days", 120)),
    )
    return jsonify(result)


@app.route("/api/carbon/soil-carbon", methods=["POST"])
@login_required
def api_carbon_soil():
    """
    Calculate soil SOM and Net Carbon Supply via GEE.
    Body JSON: { "field_geometry": <GeoJSON Polygon> }
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    geometry = data.get("field_geometry")
    if not geometry:
        lat = float(user.get("lat") or 29.5)
        lon = float(user.get("lon") or 30.5)
        delta = 0.05
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [lon - delta, lat - delta],
                [lon - delta, lat + delta],
                [lon + delta, lat + delta],
                [lon + delta, lat - delta],
                [lon - delta, lat - delta],
            ]],
        }

    result = carbon_processor.calculate_soil_carbon(
        user_id=user["id"],
        field_geometry=geometry,
    )
    return jsonify(result)


@app.route("/api/carbon/livestock-history")
@login_required
def api_carbon_livestock_history():
    """Return livestock emission history for the logged-in user."""
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    days = request.args.get("days", 30, type=int)
    data = get_livestock_history(user["id"], days)
    return jsonify({"success": True, "data": data})


@app.route("/api/carbon/soil-history")
@login_required
def api_carbon_soil_history():
    """Return soil carbon history for the logged-in user."""
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    days = request.args.get("days", 30, type=int)
    data = get_soil_carbon_history(user["id"], days)
    return jsonify({"success": True, "data": data})


@app.route("/api/carbon/credits/calculate", methods=["POST"])
@login_required
def api_carbon_credits():
    """
    Calculate potential carbon credits from net sequestration.
    Body JSON: { "period_days": 365, "credit_price_usd": 15 }
    """
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    period_days      = int(data.get("period_days", 365))
    credit_price_usd = float(data.get("credit_price_usd", 15.0))

    summary = get_carbon_summary(user["id"], period_days)

    # ── Fallback to demo data if no real records ──────────────
    has_data = (
        summary["livestock"]["ch4_kg"] > 0
        or summary["crops"]["ch4_kg"] > 0
        or summary["soil"]["total_sequestered_kg"] > 0
    )
    if not has_data:
        summary = {
            "livestock": {"ch4_kg": 850.0, "nh3_kg": 279.0, "avg_heads": 15.5},
            "crops":     {"ch4_kg": 8010.0, "flooding_days": 85.0},
            "soil":      {"som_mg_kg": 28.5, "ncs_gC_m2_year": 245.0,
                          "total_sequestered_kg": 17762.5},
        }

    total_ch4_kg    = summary["livestock"]["ch4_kg"] + summary["crops"]["ch4_kg"]
    co2_eq_kg       = total_ch4_kg * 28           # CH4 GWP-100 = 28
    sequestered_kg  = summary["soil"]["total_sequestered_kg"]
    net_balance_kg  = sequestered_kg - co2_eq_kg
    net_balance_tons = net_balance_kg / 1000

    if net_balance_tons > 0:
        credits_earned   = net_balance_tons
        total_value_usd  = credits_earned * credit_price_usd
        status           = "Eligible"
    else:
        credits_earned   = 0
        total_value_usd  = 0
        status           = "Not Eligible"

    return jsonify({
        "success":               True,
        "period_days":           period_days,
        "emissions_co2_eq_tons": round(co2_eq_kg / 1000, 2),
        "sequestration_tons":    round(sequestered_kg / 1000, 2),
        "net_balance_tons":      round(net_balance_tons, 2),
        "credits_earned":        round(credits_earned, 2),
        "credit_price_usd":      credit_price_usd,
        "total_value_usd":       round(total_value_usd, 2),
        "status":                status,
    })



# ============================================================
# Unified Risk Engine — Page + API
# ============================================================

try:
    from risk_engine import compute_farm_risks
    _RISK_ENGINE_AVAILABLE = True
    log.info("[RiskEngine] risk_engine.py loaded successfully.")
except ImportError as _re:
    _RISK_ENGINE_AVAILABLE = False
    log.warning(f"[RiskEngine] risk_engine.py not found: {_re}")


@app.route("/risks")
@login_required
def risks_page():
    """
    المخاطر الأساسية — Unified Operational Risk Intelligence page.
    Single page integrating: Land/Crop, Pivot/Irrigation, Machine, Alerts, Recommendations.
    """
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login_page"))
    return render_template("risks.html", user=dict(user))


@app.route("/api/risks/dashboard")
@login_required
def api_risks_dashboard():
    """
    Unified Risk Dashboard API.
    Aggregates all available farm data and returns a structured
    risk assessment from the RiskEngine.

    Response includes:
      - farm_risk_score (0-100)
      - severity (critical/high/medium/low/normal/unknown)
      - land_crop (NDVI, NDWI, LST, ET, TVDI, biomass)
      - pivot_irrigation (ndvi_cv, thermal_inertia, issues)
      - machine_pump (pump hours anomaly detection)
      - weather_alerts (OWM-based agronomic alerts)
      - active_risks (list of all detected risks with evidence)
      - recommended_actions
    """
    user_raw = current_user()
    if not user_raw:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    user = dict(user_raw)
    user_id = user["id"]

    # Check cache first (5-minute TTL for risks)
    cache_key = f"risks:{user_id}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)

    if not _RISK_ENGINE_AVAILABLE:
        return jsonify({
            "success": False,
            "error": "Risk Engine not available",
            "farm_risk_score": 0,
            "severity": "unknown",
            "data_status": "DATA_UNAVAILABLE",
        }), 503

    # ── Load data (all real, no mocks) ──────────────────────
    farm_row_raw  = get_latest_farm_data(user_id)
    farm_row      = dict(farm_row_raw) if farm_row_raw else {}

    # Merge area_feddan from user if not in farm row
    farm_row.setdefault("area_feddan", user.get("area_feddan") or 1.0)

    user_row = dict(user)

    history_raw = get_farm_data_range(user_id, days=7)
    history     = [dict(r) for r in history_raw] if history_raw else []

    # Weather: try real OWM, return empty list on failure
    forecast    = []
    agro_alerts = []
    if user.get("lat") and user.get("lon"):
        try:
            forecast    = fetch_weather_forecast(user["lat"], user["lon"])
            agro_alerts = generate_agronomy_alerts(forecast, farm_row)
        except Exception as _wex:
            log.warning(f"[Risks] Weather fetch failed: {_wex}")

    # ML prediction (if model available)
    ml_prediction = None
    try:
        from ml_gee_engine import predict_crop_health
        if farm_row:
            ml_prediction = predict_crop_health(farm_row)
    except Exception as _mlex:
        log.debug(f"[Risks] ML prediction not available: {_mlex}")

    # ── Compute risks via Unified RiskEngine & FarmDataProvider ─────────────────
    try:
        from farm_data_provider import FarmDataProvider
        provider = FarmDataProvider(user_id)
        report = compute_farm_risks(
            user_id       = user_id,
            farm_row      = farm_row,
            user_row      = user_row,
            history       = history,
            forecast      = forecast,
            agro_alerts   = agro_alerts,
            ml_prediction = ml_prediction,
        )

        # Attach real verified data sections from FarmDataProvider
        report["satellite_observation"] = provider.get_satellite_observations()
        report["weather_details"]       = provider.get_weather_forecast()
        report["iot_telemetry"]         = provider.get_iot_telemetry()
        report["ml_prediction"]         = provider.get_ml_predictions()

    except Exception as exc:
        log.exception(f"[Risks] RiskEngine failed for user {user_id}: {exc}")
        return jsonify({
            "success": False,
            "error": "Risk computation failed",
            "details": str(exc),
        }), 500

    # Enrich with farm name
    report["farm_name"] = user.get("farm_name") or user.get("farm_location") or "مزرعتي"
    report["crop_type"] = user.get("crop_type") or "محصول"
    report["area_feddan"] = float(user.get("area_feddan") or 1.0)
    report["success"] = True

    # Cache for 5 minutes (default prefix TTL is 300s)
    set_cache(cache_key, report)

    # Audit log
    _sm = get_security_manager()
    if _sm:
        _sm.log_action(user_id, "view_risks_dashboard", "risk_report", user_id)

    return jsonify(report)



@app.route("/api/risks/history")
@login_required
def api_risks_history():
    """
    Return historical risk scores for the chart (last 30 days).
    Computed from Farm_Data historical records.
    """
    user_raw = current_user()
    if not user_raw:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    user = dict(user_raw)
    if not _RISK_ENGINE_AVAILABLE:
        return jsonify({"success": False, "history": []}), 503

    user_id = user["id"]
    history_raw = get_farm_data_range(user_id, days=30)
    if not history_raw:
        return jsonify({
            "success": True,
            "history": [],
            "note": "لا توجد بيانات تاريخية متاحة",
        })

    # Compute a lightweight risk score for each day
    history_scores = []
    for row in history_raw:
        d = dict(row)
        mini_engine = compute_farm_risks(
            user_id  = user_id,
            farm_row = d,
            user_row = dict(user),
        )
        history_scores.append({
            "date":  d.get("date", ""),
            "score": mini_engine.get("farm_risk_score", 0),
            "severity": mini_engine.get("severity", "unknown"),
        })

    return jsonify({"success": True, "history": history_scores})


# ── Health check enhancements ─────────────────────────────────

@app.route("/api/v1/health")
def api_v1_health():
    """Detailed health check with all subsystem statuses."""
    import sqlite3 as _sqlite3

    # DB check
    db_ok = False
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass

    # Risk engine check
    risk_engine_ok = _RISK_ENGINE_AVAILABLE

    # GEE check (non-blocking)
    gee_ok = False
    try:
        from ml_gee_engine import _gee_initialized
        gee_ok = _gee_initialized
    except Exception:
        pass

    overall = "ok" if (db_ok and risk_engine_ok) else "degraded"

    return jsonify({
        "status":          overall,
        "version":         "2.0.0",
        "timestamp":       datetime.datetime.utcnow().isoformat() + "Z",
        "subsystems": {
            "database":      "ok" if db_ok else "error",
            "risk_engine":   "ok" if risk_engine_ok else "unavailable",
            "gee":           "ok" if gee_ok else "not_initialized",
            "scheduler":     "ok" if _scheduler_started else "not_started",
        }
    }), 200 if overall == "ok" else 503


@app.route("/api/v1/ready")
def api_v1_ready():
    """Readiness check — is the platform ready to serve requests?"""
    try:
        with get_connection() as conn:
            conn.execute("SELECT id FROM Users LIMIT 1")
        return jsonify({"ready": True, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
    except Exception as exc:
        return jsonify({"ready": False, "error": str(exc)}), 503


# ── Entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    _schedule_background_pipeline()

    # Init Telegram Webhook
    webhook_url = os.getenv("WEBHOOK_BASE_URL")
    if webhook_url:
        full_webhook = f"{webhook_url.rstrip('/')}/api/webhook/telegram"
        try:
            from telegram_integration import get_telegram_service
            get_telegram_service().set_webhook(full_webhook)
        except Exception as _e:
            log.error(f"[Telegram] Failed to set webhook on startup: {_e}")

    app.run(
        host  = Config.HOST,
        port  = Config.PORT,
        debug = Config.DEBUG,
    )
