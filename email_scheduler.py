# ============================================================
# email_scheduler.py — Tayseer Agri-SaT Email Scheduler
#
# Responsibilities:
#   • Daily farm update emails (7:00 AM)
#   • Weekly report emails (Sunday 8:00 AM)
#   • Immediate critical alert dispatcher
#   • Email send logging
#
# Usage in app.py:
#   from email_scheduler import start_email_scheduler
#   start_email_scheduler(app)
#
# Standalone test:
#   python email_scheduler.py --test-daily
# ============================================================

import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# ── Lazy imports to avoid circular deps ──────────────────────

def _get_svc():
    from email_service import get_email_service
    return get_email_service()

def _db():
    from database import get_connection
    return get_connection()

def _get_opted_in(col: str):
    from database import get_opted_in_email_users
    return get_opted_in_email_users(col)

def _get_latest(uid: int):
    from database import get_latest_farm_data
    return get_latest_farm_data(uid)

def _log_email(user_id, recipient, subject, template, msg_id, status, error=None):
    from database import log_email
    log_email(user_id, recipient, subject, template, msg_id, status, error)


# ── Context builders ─────────────────────────────────────────

def build_daily_context(user: dict, fd: dict) -> dict:
    """Build Jinja2 render context for daily_update.html."""
    import secrets
    nd  = fd.get("ndvi",       0.65)
    et  = fd.get("et",         4.5)
    ph  = fd.get("pump_hours", 6.0)
    bm  = fd.get("biomass",    12.0)
    st  = fd.get("status",     "healthy")
    base = os.getenv("WEBHOOK_BASE_URL", "http://localhost:5000")

    # Ensure unsubscribe token
    tok = user.get("unsubscribe_token") or ""
    if not tok:
        tok = secrets.token_urlsafe(24)
        try:
            with _db() as conn:
                conn.execute("UPDATE Users SET unsubscribe_token=? WHERE id=?", (tok, user["id"]))
                conn.commit()
        except Exception as e:
            log.warning(f"[EmailSched] Could not write unsubscribe token: {e}")

    return dict(
        subject           = f"تحديث يومي — {user.get('farm_name', 'مزرعتك')}",
        user_name         = user.get("name", ""),
        farm_name         = user.get("farm_name", "مزرعتك"),
        date              = datetime.now().strftime("%Y-%m-%d"),
        status_class      = st,
        status_icon       = "✅" if st == "healthy" else ("⚠️" if st == "warning" else "🔴"),
        status_text       = {"healthy": "ممتاز", "warning": "متوسط"}.get(st, st),
        ndvi              = f"{nd:.3f}",
        et                = f"{et:.2f}",
        pump_hours        = f"{ph:.1f}",
        biomass           = f"{bm:.1f}",
        irrigation_advice = (
            "✅ لا ري مطلوب اليوم — التربة رطبة بما يكفي."
            if nd >= 0.55 else
            f"💧 شغّل الطلمبة {ph:.1f} ساعة اليوم للحفاظ على رطوبة التربة."
        ),
        temp_min          = 18,
        temp_max          = 32,
        humidity          = 55,
        weather_description = "صافٍ جزئياً",
        alerts            = [],
        dashboard_url     = f"{base}/dashboard",
        unsubscribe_token = tok,
    )


def build_weekly_context(user: dict, fd: dict) -> dict:
    """Build Jinja2 render context for weekly_report.html."""
    nd  = fd.get("ndvi",       0.65)
    ph  = fd.get("pump_hours", 6.0)
    base = os.getenv("WEBHOOK_BASE_URL", "http://localhost:5000")
    now = datetime.now()
    tok = user.get("unsubscribe_token", "")

    return dict(
        subject          = f"التقرير الأسبوعي — {user.get('farm_name', 'مزرعتك')}",
        user_name        = user.get("name", ""),
        farm_name        = user.get("farm_name", "مزرعتك"),
        week_range       = f"{(now - timedelta(days=7)).strftime('%Y-%m-%d')} — {now.strftime('%Y-%m-%d')}",
        avg_ndvi         = f"{nd:.3f}",
        ndvi_trend       = "📈 تحسن" if nd > 0.5 else "📉 انخفاض",
        water_usage      = f"{ph * 7 * 120:.0f}",
        total_hours      = f"{ph * 7:.1f}",
        biomass          = f"{fd.get('biomass', 12.0):.1f}",
        achievements     = [
            "استمرارية تشغيل الري وفق الجدول الزمني",
            "لا أعطال مسجلة في البيفوت خلال الأسبوع",
        ],
        concerns         = (
            []
            if nd > 0.5
            else ["انخفاض قيم NDVI — راجع تغطية المحصول وكميات الري"]
        ),
        recommendations  = (
            "استمر في نفس جدول الري مع متابعة مؤشر NDVI يومياً."
        ),
        dashboard_url    = f"{base}/dashboard",
        unsubscribe_token= tok,
    )


# ── Scheduled job functions ───────────────────────────────────

def send_daily_farm_updates():
    """
    Job: Send daily farm-update emails to opted-in users.
    Scheduled: every day at 07:00 (Cairo / server time).
    """
    log.info(f"[Email-Daily] Starting daily email run at {datetime.now()}")
    svc   = _get_svc()
    users = _get_opted_in("daily_updates")
    log.info(f"[Email-Daily] {len(users)} recipients.")

    for u in users:
        try:
            fd_row = _get_latest(u["id"])
            fd     = dict(fd_row) if fd_row else {}
            ctx    = build_daily_context(u, fd)
            res    = svc.send_template_email(u["email"], "daily_update.html", ctx)
            _log_email(
                u["id"], u["email"], ctx["subject"],
                "daily_update", res.get("message_id"),
                "sent" if res.get("success") else "failed",
                res.get("error"),
            )
            log.info(f"[Email-Daily] ✅ → {u['email']}")
        except Exception as exc:
            log.error(f"[Email-Daily] ❌ {u.get('email')}: {exc}")


def send_weekly_reports():
    """
    Job: Send weekly farm-summary emails to opted-in users.
    Scheduled: every Sunday at 08:00.
    """
    log.info(f"[Email-Weekly] Starting weekly email run at {datetime.now()}")
    svc   = _get_svc()
    users = _get_opted_in("weekly_reports")
    log.info(f"[Email-Weekly] {len(users)} recipients.")

    for u in users:
        try:
            fd_row = _get_latest(u["id"])
            fd     = dict(fd_row) if fd_row else {}
            ctx    = build_weekly_context(u, fd)
            res    = svc.send_template_email(u["email"], "weekly_report.html", ctx)
            _log_email(
                u["id"], u["email"], ctx["subject"],
                "weekly_report", res.get("message_id"),
                "sent" if res.get("success") else "failed",
                res.get("error"),
            )
            log.info(f"[Email-Weekly] ✅ → {u['email']}")
        except Exception as exc:
            log.error(f"[Email-Weekly] ❌ {u.get('email')}: {exc}")


def send_critical_alert_email(user_id: int, alert_type: str, alert_data: dict):
    """
    Immediate dispatcher: send a critical alert email right now.

    Parameters
    ----------
    user_id    : ID of the affected user
    alert_type : 'pivot_failure' | 'water_stress' | 'weather_warning'
    alert_data : dict with relevant fields (ndvi, pivot_name, tower, etc.)

    Example
    -------
    send_critical_alert_email(
        user_id=7,
        alert_type='water_stress',
        alert_data={'ndvi': 0.22, 'et': 6.5, 'percentage': 70, 'pump_hours': 8.0}
    )
    """
    from database import get_user_by_id, get_email_preferences
    from email_service import get_email_service

    user  = get_user_by_id(user_id)
    if not user:
        log.error(f"[CriticalAlert] User {user_id} not found")
        return

    prefs = get_email_preferences(user_id)
    if not prefs.get("critical_alerts", True):
        log.info(f"[CriticalAlert] User {user_id} has disabled critical alerts — skipping")
        return

    svc  = get_email_service()
    farm = user.get("farm_name", "مزرعتك")
    base = os.getenv("WEBHOOK_BASE_URL", "http://localhost:5000")

    # ── Build context based on alert type ──────────────────────
    if alert_type == "pivot_failure":
        ctx = dict(
            subject        = f"🚨 تحذير عاجل — عطل في البيفوت — {farm}",
            farm_name      = farm,
            alert_title    = f"عطل في {alert_data.get('pivot_name','البيفوت')} — برج {alert_data.get('tower','?')}",
            alert_message  = f"تم اكتشاف {alert_data.get('fault_type','عطل')} في برج {alert_data.get('tower','?')}. التحقق الفوري مطلوب.",
            action_required= alert_data.get("action", "فحص البيفوت فوراً"),
            details        = alert_data.get("details", []),
            dashboard_url  = f"{base}/dashboard",
            support_phone  = os.getenv("SUPPORT_PHONE", "+201234567890"),
        )
    elif alert_type == "water_stress":
        ndvi = alert_data.get("ndvi", 0.2)
        ph   = alert_data.get("pump_hours", 8.0)
        pct  = alert_data.get("percentage", 60)
        ctx = dict(
            subject        = f"⚠️ إجهاد مائي حاد — {farm}",
            farm_name      = farm,
            alert_title    = "المحصول يعاني من إجهاد مائي شديد",
            alert_message  = f"انخفض NDVI إلى {ndvi:.2f}. {pct}% من المحصول يحتاج ري فوري.",
            action_required= f"شغّل الطلمبة {ph:.0f} ساعة فوراً واحرص على فتح جميع الرشاشات",
            details        = [
                f"NDVI الحالي: {ndvi:.3f}",
                f"ET: {alert_data.get('et', 0):.2f} مم/يوم",
                f"TVDI: {alert_data.get('tvdi', 'N/A')}",
                f"ساعات الري المطلوبة: {ph:.0f} ساعة",
            ],
            dashboard_url  = f"{base}/dashboard",
            support_phone  = os.getenv("SUPPORT_PHONE", "+201234567890"),
        )
    elif alert_type == "weather_warning":
        ctx = dict(
            subject        = f"🌪️ تحذير طقس — {farm}",
            farm_name      = farm,
            alert_title    = f"تحذير طقس: {alert_data.get('event', 'حدث مناخي')}",
            alert_message  = alert_data.get("description", "حالة طقس غير عادية متوقعة."),
            action_required= alert_data.get("action", "راجع جدول الري واحرص على حماية المحصول"),
            details        = alert_data.get("details", []),
            dashboard_url  = f"{base}/dashboard",
            support_phone  = os.getenv("SUPPORT_PHONE", "+201234567890"),
        )
    else:
        log.warning(f"[CriticalAlert] Unknown alert_type: {alert_type}")
        return

    res = svc.send_template_email(user["email"], "critical_alert.html", ctx)
    _log_email(
        user_id, user["email"], ctx["subject"],
        "critical_alert", res.get("message_id"),
        "sent" if res.get("success") else "failed",
    )
    log.info(f"[CriticalAlert] {'✅' if res.get('success') else '❌'} → {user['email']} ({alert_type})")
    return res


# ── Scheduler startup ─────────────────────────────────────────

_email_scheduler = None


def start_email_scheduler(app=None):
    """
    Register and start the APScheduler email jobs.
    Call once at application startup.

    Parameters
    ----------
    app : Flask application instance (optional, used for app_context)
    """
    global _email_scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        _email_scheduler = BackgroundScheduler(daemon=True, timezone="Africa/Cairo")

        if app:
            def _daily_with_ctx():
                with app.app_context():
                    send_daily_farm_updates()

            def _weekly_with_ctx():
                with app.app_context():
                    send_weekly_reports()
        else:
            _daily_with_ctx  = send_daily_farm_updates
            _weekly_with_ctx = send_weekly_reports

        _email_scheduler.add_job(
            _daily_with_ctx,
            "cron", hour=7, minute=0,
            id="email_daily_updates",
            replace_existing=True,
        )
        _email_scheduler.add_job(
            _weekly_with_ctx,
            "cron", day_of_week="sun", hour=8, minute=0,
            id="email_weekly_reports",
            replace_existing=True,
        )

        _email_scheduler.start()
        log.info(
            "[Email-Scheduler] ✅ Started — "
            "Daily @ 07:00  |  Weekly (Sun) @ 08:00"
        )
        return _email_scheduler

    except ImportError:
        log.warning("[Email-Scheduler] APScheduler not installed — email jobs disabled.")
        return None
    except Exception as exc:
        log.error(f"[Email-Scheduler] Failed to start: {exc}")
        return None


def stop_email_scheduler():
    """Gracefully stop the scheduler (e.g., on app shutdown)."""
    global _email_scheduler
    if _email_scheduler and _email_scheduler.running:
        _email_scheduler.shutdown(wait=False)
        log.info("[Email-Scheduler] Stopped.")


# ── Standalone test ───────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if "--test-daily" in sys.argv:
        print("🔄 Running daily email test (dry-run)...")
        users = _get_opted_in("daily_updates")
        print(f"   Found {len(users)} opted-in users")
        for u in users[:3]:
            fd_row = _get_latest(u["id"])
            ctx    = build_daily_context(u, dict(fd_row) if fd_row else {})
            print(f"   → Would send to: {u['email']}")
            print(f"     Subject: {ctx['subject']}")
            print(f"     NDVI: {ctx['ndvi']}  ET: {ctx['et']}  Pump: {ctx['pump_hours']}h")

    elif "--test-weekly" in sys.argv:
        print("🔄 Running weekly report test (dry-run)...")
        users = _get_opted_in("weekly_reports")
        print(f"   Found {len(users)} opted-in users")
        for u in users[:3]:
            ctx = build_weekly_context(u, {})
            print(f"   → Would send to: {u['email']}, subject: {ctx['subject']}")

    elif "--fire-daily" in sys.argv:
        print("🚀 Firing daily updates NOW...")
        send_daily_farm_updates()

    elif "--fire-weekly" in sys.argv:
        print("🚀 Firing weekly reports NOW...")
        send_weekly_reports()

    else:
        print("Usage: python email_scheduler.py [--test-daily | --test-weekly | --fire-daily | --fire-weekly]")
