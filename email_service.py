# ============================================================
# email_service.py — Tayseer Agri-SaT Email Notification System
#
# Supports: Gmail/SMTP (dev), SendGrid (prod), AWS SES (prod)
# Features:
#   • HTML/plain-text dual-part emails
#   • Jinja2 template rendering from templates/emails/
#   • Attachments (PDF, Excel, etc.)
#   • Host inbox forwarding
#   • Full send/bounce/error logging
# ============================================================

import os
import logging
import smtplib
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ── Jinja2 template environment ─────────────────────────────
_jinja_env = Environment(
    loader=FileSystemLoader(
        os.path.join(os.path.dirname(__file__), "templates", "emails")
    ),
    autoescape=select_autoescape(["html", "xml"]),
)


# ╔═══════════════════════════════════════════════════════════╗
# ║  EmailService                                            ║
# ╚═══════════════════════════════════════════════════════════╝

class EmailService:
    """
    Universal email sender with provider auto-detection.
    Priority: SendGrid → SMTP  (SES placeholder ready).
    """

    def __init__(self):
        self.provider    = os.getenv("EMAIL_PROVIDER", "smtp").lower()
        self.from_email  = os.getenv("FROM_EMAIL",    "noreply@tayseer.app")
        self.from_name   = os.getenv("FROM_NAME",     "Tayseer Agri-SaT")
        self.host_email  = os.getenv("HOST_EMAIL",    "admin@tayseer.app")
        self.support_email = os.getenv("SUPPORT_EMAIL", "support@tayseer.app")
        self.base_url    = os.getenv("WEBHOOK_BASE_URL", "https://tayseer.app")
        self.enabled     = True

        if self.provider == "sendgrid":
            _key = os.getenv("SENDGRID_API_KEY", "")
            if not _key:
                log.warning("[Email] SENDGRID_API_KEY not set — falling back to SMTP")
                self.provider = "smtp"
            else:
                try:
                    import sendgrid as _sg
                    self._sg_client = _sg.SendGridAPIClient(_key)
                except ImportError:
                    log.warning("[Email] sendgrid package not installed — falling back to SMTP")
                    self.provider = "smtp"

        if self.provider == "smtp":
            self._smtp_host = os.getenv("SMTP_SERVER", "smtp.gmail.com")
            self._smtp_port = int(os.getenv("SMTP_PORT", "587"))
            self._smtp_user = os.getenv("SMTP_USERNAME", "")
            self._smtp_pass = os.getenv("SMTP_PASSWORD", "")
            if not self._smtp_user:
                log.warning("[Email] SMTP_USERNAME not set — emails will be simulated")
                self.enabled = False

        log.info(f"[Email] Service ready — provider={self.provider}, enabled={self.enabled}")

    # ── Core send ──────────────────────────────────────────────

    def send_email(self, to_email: str | list, subject: str,
                   html_content: str, text_content: str = None,
                   attachments: list = None) -> dict:
        """
        Send an email.

        Parameters
        ----------
        to_email    : recipient address or list of addresses
        subject     : email subject
        html_content: HTML body
        text_content: optional plain-text fallback
        attachments : list of {filename, content (bytes), type}

        Returns
        -------
        dict with keys: success, message_id, error
        """
        if not self.enabled:
            log.debug(f"[Email-Demo] → {to_email} : {subject}")
            return {"success": True, "demo": True, "message_id": "demo"}

        try:
            if self.provider == "sendgrid":
                return self._send_sendgrid(to_email, subject, html_content,
                                           text_content, attachments)
            return self._send_smtp(to_email, subject, html_content,
                                   text_content, attachments)
        except Exception as exc:
            log.error(f"[Email] Send failed ({to_email}): {exc}")
            return {"success": False, "error": str(exc)}

    def _send_smtp(self, to_email, subject, html_content,
                   text_content, attachments):
        """SMTP fallback — supports Gmail, custom SMTP."""
        if isinstance(to_email, list):
            to_email = ", ".join(to_email)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{self.from_name} <{self.from_email}>"
        msg["To"]      = to_email

        if text_content:
            msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if attachments:
            for att in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att["content"])
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                 f"attachment; filename={att['filename']}")
                msg.attach(part)

        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(self._smtp_user, self._smtp_pass)
            srv.send_message(msg)

        return {"success": True, "message_id": None}

    def _send_sendgrid(self, to_email, subject, html_content,
                       text_content, attachments):
        """SendGrid API send."""
        from sendgrid.helpers.mail import (
            Mail, Email, To, Content, Attachment,
            FileContent, FileName, FileType, Disposition,
        )

        if isinstance(to_email, list):
            recipients = [To(e) for e in to_email]
        else:
            recipients = To(to_email)

        mail = Mail(
            from_email=Email(self.from_email, self.from_name),
            to_emails=recipients,
            subject=subject,
            html_content=Content("text/html", html_content),
        )
        if text_content:
            mail.add_content(Content("text/plain", text_content))

        if attachments:
            for att in attachments:
                a = Attachment()
                a.file_content  = FileContent(base64.b64encode(att["content"]).decode())
                a.file_name     = FileName(att["filename"])
                a.file_type     = FileType(att.get("type", "application/octet-stream"))
                a.disposition   = Disposition("attachment")
                mail.add_attachment(a)

        resp = self._sg_client.send(mail)
        return {
            "success": resp.status_code in (200, 201, 202),
            "message_id": resp.headers.get("X-Message-Id"),
            "status_code": resp.status_code,
        }

    # ── Template rendering ─────────────────────────────────────

    def send_template_email(self, to_email: str, template_name: str,
                             context: dict, attachments: list = None) -> dict:
        """Render a Jinja2 template from templates/emails/ and send."""
        try:
            tmpl = _jinja_env.get_template(template_name)
            html = tmpl.render(**context, base_url=self.base_url,
                               support_email=self.support_email)
        except Exception as exc:
            log.error(f"[Email] Template render error ({template_name}): {exc}")
            html = f"<p>{context.get('subject', 'Farm Update')}</p>"

        subject = context.get("subject", "Tayseer Update")
        return self.send_email(to_email, subject, html, attachments=attachments)

    # ── Host inbox forward ─────────────────────────────────────

    def send_to_host(self, subject: str, message: str,
                     from_user: dict = None) -> dict:
        """Forward a client message to the host admin inbox email."""
        user_block = ""
        if from_user:
            user_block = f"""
            <table style="background:#f3f4f6;padding:14px;border-radius:8px;margin:16px 0;width:100%;border-collapse:collapse;" dir="rtl">
                <tr><td style="padding:4px 8px;color:#6b7280;width:80px">الاسم:</td>
                    <td style="padding:4px 8px;font-weight:700">{from_user.get('name','')}</td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280">البريد:</td>
                    <td style="padding:4px 8px">{from_user.get('email','')}</td></tr>
                <tr><td style="padding:4px 8px;color:#6b7280">المزرعة:</td>
                    <td style="padding:4px 8px">{from_user.get('farm','')}</td></tr>
            </table>"""

        html = f"""<!DOCTYPE html><html dir="rtl" lang="ar">
        <head><meta charset="UTF-8">
        <style>body{{font-family:Cairo,Arial,sans-serif;direction:rtl;background:#f9fafb;margin:0;padding:20px}}
        .box{{max-width:620px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
        .hdr{{background:linear-gradient(135deg,#10b981,#059669);color:#fff;padding:24px;text-align:center}}
        .body{{padding:28px}}</style></head>
        <body><div class="box">
        <div class="hdr"><h2 style="margin:0">📧 رسالة جديدة من عميل</h2>
        <p style="margin:6px 0 0;opacity:.85">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p></div>
        <div class="body">{user_block}
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-top:8px">
        <h3 style="margin-top:0;color:#1f2937">{subject}</h3>
        <p style="line-height:1.7;color:#374151">{message.replace(chr(10),'<br>')}</p></div>
        <p style="margin-top:20px;font-size:13px;color:#6b7280;text-align:center">
        للرد: استخدم لوحة الإدارة أو راسل العميل مباشرة على {from_user.get('email','') if from_user else ''}
        </p></div></div></body></html>"""

        return self.send_email(
            to_email=self.host_email,
            subject=f"[Tayseer] رسالة جديدة: {subject}",
            html_content=html,
        )

    # ── Critical alert shortcut ────────────────────────────────

    def send_critical_alert(self, to_email: str, user_name: str,
                             farm_name: str, alert_title: str,
                             alert_message: str, action_required: str,
                             details: list = None) -> dict:
        return self.send_template_email(
            to_email=to_email,
            template_name="critical_alert.html",
            context=dict(
                subject=f"🚨 تحذير عاجل — {farm_name}",
                user_name=user_name,
                farm_name=farm_name,
                alert_title=alert_title,
                alert_message=alert_message,
                action_required=action_required,
                details=details or [],
                dashboard_url=f"{self.base_url}/dashboard",
                support_phone="+201234567890",
                support_email=self.support_email,
            ),
        )


# ── Singleton ─────────────────────────────────────────────────
_email_svc: EmailService = None

def get_email_service() -> EmailService:
    global _email_svc
    if _email_svc is None:
        _email_svc = EmailService()
    return _email_svc
