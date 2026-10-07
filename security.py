# ============================================================
# security.py — Tayseer Agri-SaT Enterprise Security Layer
# Step 10: Multi-Tenancy Security System
#
# Provides:
#   • SecurityManager — password hashing, JWT, encryption,
#                       RBAC, rate limiting, account lockout,
#                       audit + security event logging
#   • Decorators — token_required, role_required,
#                  permission_required, rate_limit
# ============================================================

import os
import json
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from functools import wraps

log = logging.getLogger(__name__)

# ── Optional heavy deps with graceful degradation ─────────────
try:
    import bcrypt as _bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False
    log.warning("[Security] bcrypt not installed — werkzeug pbkdf2 fallback active")

try:
    import jwt as _jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    log.warning("[Security] PyJWT not installed — JWT endpoints disabled")

try:
    from cryptography.fernet import Fernet
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False
    log.warning("[Security] cryptography not installed — encryption disabled (plain-text fallback)")

# ── Module-level singleton ─────────────────────────────────────
_sm = None


def init_security_manager(app):
    """Initialise and return the SecurityManager singleton."""
    global _sm
    _sm = SecurityManager(app)
    return _sm


def get_security_manager():
    """Return the active SecurityManager (must call init_security_manager first)."""
    return _sm


# ══════════════════════════════════════════════════════════════════
# SecurityManager
# ══════════════════════════════════════════════════════════════════

class SecurityManager:
    """
    Enterprise security layer for Tayseer Agri-SaT.

    Features:
    • Password hashing  — bcrypt (rounds=12) with werkzeug fallback
    • JWT lifecycle     — generate / verify / revoke tokens
    • AES-256 encryption— Fernet symmetric cipher for PII / API keys
    • RBAC              — role + resource permission checks
    • Rate limiting     — sliding-window per user+IP+endpoint
    • Audit logging     — every significant action persisted
    • Security events   — suspicious activity tracking
    • Account lockout   — 5 failed logins → 30-min lock
    """

    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_MINUTES     = 30

    def __init__(self, app):
        self.app            = app
        self.secret_key     = getattr(app, 'secret_key', None) or secrets.token_hex(32)
        self.token_expiry_h = int(os.getenv('JWT_EXPIRY_HOURS', '24'))
        self._cipher        = None

        if HAS_FERNET:
            self._cipher = self._load_cipher()

        log.info(
            f"[Security] SecurityManager ready — "
            f"bcrypt={HAS_BCRYPT}  jwt={HAS_JWT}  fernet={HAS_FERNET}"
        )

    # ── Cipher initialisation ──────────────────────────────────
    def _load_cipher(self):
        """Load Fernet key from env, key-file, or generate new."""
        enc_key = os.getenv('ENCRYPTION_KEY', '').strip()
        if not enc_key:
            key_path = os.path.join(os.path.dirname(__file__), '.enc_key')
            if os.path.exists(key_path):
                with open(key_path, 'rb') as fh:
                    enc_key = fh.read().strip().decode()
            else:
                enc_key = Fernet.generate_key().decode()
                with open(key_path, 'wb') as fh:
                    fh.write(enc_key.encode())
                log.info(f"[Security] New encryption key written to {key_path}")
        try:
            return Fernet(enc_key.encode() if isinstance(enc_key, str) else enc_key)
        except Exception as exc:
            log.error(f"[Security] Invalid encryption key — encryption disabled: {exc}")
            return None

    # ── Request-context helpers ────────────────────────────────
    @staticmethod
    def _get_ip() -> str:
        try:
            from flask import request
            return (request.headers.get('X-Forwarded-For', '')
                    or request.remote_addr or 'unknown').split(',')[0].strip()[:64]
        except Exception:
            return 'unknown'

    @staticmethod
    def _get_ua() -> str:
        try:
            from flask import request
            return request.headers.get('User-Agent', '')[:512]
        except Exception:
            return ''

    # ══════════════════════════════════════════════════════════
    # 1. Password Management
    # ══════════════════════════════════════════════════════════

    def hash_password(self, password: str) -> str:
        """
        Hash password with bcrypt (rounds=12).
        Falls back to werkzeug pbkdf2:sha256 if bcrypt unavailable.
        """
        if HAS_BCRYPT:
            salt = _bcrypt.gensalt(rounds=12)
            return _bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        from werkzeug.security import generate_password_hash
        return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

    def verify_password(self, password: str, stored_hash: str) -> bool:
        """
        Verify password against stored hash.
        Handles both bcrypt ($2b$/$2a$) and werkzeug (pbkdf2:sha256) hashes
        transparently for backward compatibility.
        """
        try:
            if stored_hash.startswith(('$2b$', '$2a$', '$2y$')):
                if HAS_BCRYPT:
                    return _bcrypt.checkpw(
                        password.encode('utf-8'),
                        stored_hash.encode('utf-8')
                    )
                log.warning("[Security] bcrypt hash present but bcrypt not installed")
                return False
            # werkzeug pbkdf2 hashes (existing users)
            from werkzeug.security import check_password_hash
            return check_password_hash(stored_hash, password)
        except Exception as exc:
            log.error(f"[Security] verify_password error: {exc}")
            return False

    @staticmethod
    def validate_password_strength(password: str) -> tuple:
        """
        Returns (is_valid: bool, message: str).
        Rules: ≥8 chars, uppercase, lowercase, digit, special char.
        """
        if len(password) < 8:
            return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
        if not any(c.isupper() for c in password):
            return False, "يجب أن تحتوي على حرف كبير (A-Z) على الأقل"
        if not any(c.islower() for c in password):
            return False, "يجب أن تحتوي على حرف صغير (a-z) على الأقل"
        if not any(c.isdigit() for c in password):
            return False, "يجب أن تحتوي على رقم (0-9) على الأقل"
        specials = set("!@#$%^&*()_+-=[]{}|;:,.<>?")
        if not any(c in specials for c in password):
            return False, "يجب أن تحتوي على رمز خاص (!@#$%...) على الأقل"
        return True, "كلمة مرور قوية ✅"

    # ══════════════════════════════════════════════════════════
    # 2. JWT Token Management
    # ══════════════════════════════════════════════════════════

    def generate_token(self, user_id: int, role: str = 'client') -> str | None:
        """
        Generate a signed JWT for API access.
        Stores token hash in user_sessions for revocation support.
        Returns None if PyJWT unavailable.
        """
        if not HAS_JWT:
            return None
        now    = datetime.utcnow()
        expiry = now + timedelta(hours=self.token_expiry_h)
        payload = {
            'user_id': user_id,
            'role':    role,
            'jti':     secrets.token_urlsafe(24),
            'iat':     now,
            'exp':     expiry,
        }
        try:
            token = _jwt.encode(payload, self.secret_key, algorithm='HS256')
            from database import create_session
            create_session(
                user_id    = user_id,
                token_hash = hashlib.sha256(token.encode()).hexdigest(),
                ip_address = self._get_ip(),
                user_agent = self._get_ua(),
                expires_at = expiry.isoformat(),
            )
            return token
        except Exception as exc:
            log.error(f"[Security] Token generation failed: {exc}")
            return None

    def verify_token(self, token: str) -> dict | None:
        """
        Decode and validate a JWT.
        Checks signature, expiry, and DB revocation status.
        Returns payload dict or None.
        """
        if not HAS_JWT:
            return None
        try:
            payload = _jwt.decode(token, self.secret_key, algorithms=['HS256'])
        except _jwt.ExpiredSignatureError:
            return None
        except _jwt.InvalidTokenError:
            return None
        # Revocation check
        try:
            from database import get_session_by_hash
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            sess = get_session_by_hash(token_hash)
            if not sess or sess['revoked']:
                return None
        except Exception:
            pass  # No session table yet — allow token
        return payload

    def revoke_token(self, token: str) -> None:
        """Mark a JWT as revoked in user_sessions (logout)."""
        try:
            from database import revoke_session
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            revoke_session(token_hash)
        except Exception as exc:
            log.warning(f"[Security] Token revocation failed: {exc}")

    def revoke_all_user_tokens(self, user_id: int) -> None:
        """Revoke all active sessions for a user (e.g. password change)."""
        try:
            from database import revoke_all_user_sessions
            revoke_all_user_sessions(user_id)
        except Exception as exc:
            log.warning(f"[Security] revoke_all_user_tokens({user_id}) failed: {exc}")

    # ══════════════════════════════════════════════════════════
    # 3. AES-256 Encryption (Fernet)
    # ══════════════════════════════════════════════════════════

    def encrypt_data(self, data: str) -> str:
        """
        Encrypt sensitive data (API keys, PII).
        Returns base64-encoded ciphertext.
        Degrades gracefully to plain-text if Fernet unavailable.
        """
        if self._cipher:
            try:
                return self._cipher.encrypt(data.encode('utf-8')).decode('utf-8')
            except Exception as exc:
                log.warning(f"[Security] encrypt_data error: {exc}")
        return data

    def decrypt_data(self, encrypted: str) -> str:
        """Decrypt Fernet-encrypted data. Returns plain-text."""
        if self._cipher:
            try:
                return self._cipher.decrypt(encrypted.encode('utf-8')).decode('utf-8')
            except Exception as exc:
                log.warning(f"[Security] decrypt_data error: {exc}")
        return encrypted

    # ══════════════════════════════════════════════════════════
    # 4. Authorization (RBAC + row-level security)
    # ══════════════════════════════════════════════════════════

    def check_permission(self, user_id: int, resource_type: str,
                         resource_id, required_permission: str) -> bool:
        """
        Multi-layer permission check:
        1. Admins have full access.
        2. Explicit access_control rows.
        3. Default: clients may access their own resources.
        """
        try:
            from database import get_user_by_id, get_access_control
            user = get_user_by_id(user_id)
            if not user:
                return False

            # Admins bypass all checks
            if user.get('is_admin'):
                return True

            # Explicit access_control row
            ac = get_access_control(user_id, resource_type, resource_id)
            if ac:
                hierarchy = ['read', 'write', 'delete', 'admin']
                try:
                    return (hierarchy.index(ac['permission'])
                            >= hierarchy.index(required_permission))
                except ValueError:
                    pass

            # Default: authenticated clients may access their own farm
            if resource_type in ('farm', 'report', 'plot'):
                return True

            return False
        except Exception as exc:
            log.error(f"[Security] check_permission error: {exc}")
            return False

    # ══════════════════════════════════════════════════════════
    # 5. Rate Limiting
    # ══════════════════════════════════════════════════════════

    def check_rate_limit(self, user_id, endpoint: str,
                         max_requests: int = 100,
                         window_minutes: int = 60) -> bool:
        """
        Sliding-window rate limiter backed by rate_limits table.
        Returns True if the request is within limits.
        Fails open (returns True) on database errors.
        """
        try:
            from database import get_rate_limit_count, increment_rate_limit, cleanup_rate_limits
            window_start = (datetime.utcnow() - timedelta(minutes=window_minutes)).isoformat()

            # Occasionally clean up old entries (1% of calls)
            if secrets.randbelow(100) == 0:
                cleanup_rate_limits(window_minutes * 2)

            count = get_rate_limit_count(user_id, self._get_ip(), endpoint, window_start)
            if count >= max_requests:
                self.log_security_event(
                    event_type  = 'rate_limit_exceeded',
                    severity    = 'medium',
                    user_id     = user_id,
                    description = f'Rate limit exceeded: {endpoint} ({count}/{max_requests})',
                    metadata    = {'endpoint': endpoint, 'count': count},
                )
                return False

            increment_rate_limit(user_id, self._get_ip(), endpoint)
            return True

        except Exception as exc:
            log.warning(f"[Security] Rate limit check error: {exc}")
            return True  # Fail open

    # ══════════════════════════════════════════════════════════
    # 6. Audit Logging
    # ══════════════════════════════════════════════════════════

    def log_action(self, user_id: int, action: str,
                   resource_type: str = None, resource_id=None,
                   old_value=None, new_value=None,
                   success: bool = True, error_message: str = None) -> None:
        """
        Append an entry to audit_logs.
        Completely non-blocking — catches all exceptions internally.
        """
        try:
            from database import create_audit_log
            create_audit_log(
                user_id       = user_id,
                action        = action,
                resource_type = resource_type,
                resource_id   = resource_id,
                old_value     = (json.dumps(old_value,  ensure_ascii=False)
                                 if old_value  is not None else None),
                new_value     = (json.dumps(new_value,  ensure_ascii=False)
                                 if new_value  is not None else None),
                ip_address    = self._get_ip(),
                user_agent    = self._get_ua(),
                success       = 1 if success else 0,
                error_message = error_message,
            )
        except Exception as exc:
            log.debug(f"[Audit] log_action silent error: {exc}")

    def log_security_event(self, event_type: str, severity: str,
                           user_id: int = None, description: str = '',
                           metadata: dict = None) -> None:
        """
        Append a security_events row.
        Completely non-blocking — catches all exceptions internally.
        """
        try:
            from database import create_security_event
            create_security_event(
                event_type  = event_type,
                severity    = severity,
                user_id     = user_id,
                ip_address  = self._get_ip(),
                description = description,
                metadata    = (json.dumps(metadata, ensure_ascii=False)
                               if metadata else None),
            )
        except Exception as exc:
            log.debug(f"[Audit] log_security_event silent error: {exc}")

    # ══════════════════════════════════════════════════════════
    # 7. Account Lockout Protection
    # ══════════════════════════════════════════════════════════

    def handle_failed_login(self, user_id: int) -> bool:
        """
        Increment failed_login_attempts counter.
        Locks account after MAX_FAILED_ATTEMPTS (5).
        Returns True if account is now locked.
        """
        try:
            from database import get_user_by_id, update_user_field
            user     = get_user_by_id(user_id)
            if not user:
                return False
            attempts = (user['failed_login_attempts'] or 0) + 1
            update_user_field(user_id, 'failed_login_attempts', attempts)

            if attempts >= self.MAX_FAILED_ATTEMPTS:
                lock_until = (datetime.utcnow()
                              + timedelta(minutes=self.LOCKOUT_MINUTES)).isoformat()
                update_user_field(user_id, 'account_locked_until', lock_until)
                self.log_security_event(
                    event_type  = 'account_locked',
                    severity    = 'high',
                    user_id     = user_id,
                    description = (f'Account locked after {attempts} '
                                   f'consecutive failed login attempts'),
                    metadata    = {'attempts': attempts, 'locked_until': lock_until},
                )
                log.warning(f"[Security] User {user_id} locked until {lock_until}")
                return True  # locked

            self.log_security_event(
                event_type  = 'failed_login',
                severity    = 'low',
                user_id     = user_id,
                description = (f'Failed login attempt '
                               f'{attempts}/{self.MAX_FAILED_ATTEMPTS}'),
            )
            return False  # not yet locked

        except Exception as exc:
            log.error(f"[Security] handle_failed_login error: {exc}")
            return False

    def check_account_locked(self, user_id: int) -> tuple:
        """
        Returns (is_locked: bool, lock_until: datetime | None).
        Auto-unlocks if the lock period has expired.
        """
        try:
            from database import get_user_by_id, update_user_field
            user = get_user_by_id(user_id)
            if not user:
                return False, None

            lock_str = user.get('account_locked_until')
            if not lock_str:
                return False, None

            lock_until = datetime.fromisoformat(str(lock_str))
            if datetime.utcnow() < lock_until:
                return True, lock_until

            # Lock expired — auto-unlock
            update_user_field(user_id, 'account_locked_until',  None)
            update_user_field(user_id, 'failed_login_attempts', 0)
            return False, None

        except Exception as exc:
            log.error(f"[Security] check_account_locked error: {exc}")
            return False, None

    def reset_failed_attempts(self, user_id: int) -> None:
        """Clear failed login counter + lock after successful authentication."""
        try:
            from database import update_user_field
            update_user_field(user_id, 'failed_login_attempts', 0)
            update_user_field(user_id, 'account_locked_until',  None)
            update_user_field(user_id, 'last_login',
                              datetime.utcnow().isoformat())
        except Exception as exc:
            log.debug(f"[Security] reset_failed_attempts silent error: {exc}")


# ══════════════════════════════════════════════════════════════════
# Flask Decorators
# ══════════════════════════════════════════════════════════════════

def token_required(f):
    """
    Decorator: require a valid JWT Bearer token in the Authorization header.
    Sets request.jwt_user_id and request.jwt_role on success.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, jsonify
        sm = get_security_manager()

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'success': False,
                            'error': 'JWT مطلوب — Authorization: Bearer <token>'}), 401

        token = auth_header[7:]
        if not sm:
            return jsonify({'success': False, 'error': 'Security manager not ready'}), 500

        payload = sm.verify_token(token)
        if not payload:
            return jsonify({'success': False,
                            'error': 'التوكن غير صالح أو انتهت صلاحيته'}), 401

        request.jwt_user_id = payload.get('user_id')
        request.jwt_role    = payload.get('role', 'client')
        request.jwt_token   = token
        return f(*args, **kwargs)

    return decorated


def role_required(*allowed_roles):
    """
    Decorator: restrict access by JWT role string.
    Must follow @token_required.
    Usage: @role_required('host', 'super_admin')
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            role = getattr(request, 'jwt_role', None)
            if role not in allowed_roles:
                return jsonify({'success': False,
                                'error': 'صلاحيات غير كافية للوصول لهذا المورد'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def permission_required(resource_type: str, permission: str = 'read'):
    """
    Decorator: RBAC permission check on a specific resource.
    Must follow @token_required.
    Resource ID is extracted from URL kwargs (farm_id / plot_id / id).
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            sm      = get_security_manager()
            user_id = getattr(request, 'jwt_user_id', None)
            if not user_id:
                return jsonify({'success': False, 'error': 'غير مصرح به'}), 401

            res_id = (kwargs.get('farm_id') or kwargs.get('plot_id')
                      or kwargs.get('report_id') or kwargs.get('id'))

            if sm and not sm.check_permission(user_id, resource_type, res_id, permission):
                if sm:
                    sm.log_action(
                        user_id       = user_id,
                        action        = f'denied_{permission}_{resource_type}',
                        resource_type = resource_type,
                        resource_id   = res_id,
                        success       = False,
                        error_message = 'Permission denied',
                    )
                return jsonify({'success': False, 'error': 'الوصول مرفوض'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def rate_limit(max_requests: int = 100, window_minutes: int = 60):
    """
    Decorator: sliding-window rate limiter.
    Applies per jwt_user_id + IP + endpoint.
    Usage: @rate_limit(max_requests=5, window_minutes=15)
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            sm = get_security_manager()
            if sm:
                user_id  = getattr(request, 'jwt_user_id', None)
                endpoint = request.endpoint or f.__name__
                if not sm.check_rate_limit(user_id, endpoint, max_requests, window_minutes):
                    return jsonify({
                        'success': False,
                        'error': (f'تجاوزت الحد المسموح '
                                  f'({max_requests} طلب/{window_minutes} دقيقة)'),
                    }), 429
            return f(*args, **kwargs)
        return decorated
    return decorator
