# ============================================================
# cache_manager.py — Caching & Performance Layer
#
# Responsibilities:
#   • In-memory caching for expensive operations
#   • Cache invalidation helpers
#   • Memoization decorators
# ============================================================

import time
from functools import wraps
from datetime import datetime, timedelta

# Simple in-memory cache (production: use Redis)
_cache = {}
_cache_timestamps = {}

CACHE_TTL = {
    "weather": 3600,          # 1 hour
    "farm_data": 600,         # 10 minutes
    "user": 1800,             # 30 minutes
    "gee_tiles": 7200,        # 2 hours
    "crop_charts": 900,       # 15 minutes
    "zone_metrics": 600,      # 10 minutes
}


def get_cache(key):
    """Get value from cache if not expired."""
    if key in _cache:
        timestamp = _cache_timestamps.get(key, time.time())
        ttl = CACHE_TTL.get(key.split(":")[0], 300)  # Default 5 min
        if time.time() - timestamp < ttl:
            return _cache[key]
        else:
            # Cache expired
            del _cache[key]
            del _cache_timestamps[key]
    return None


def set_cache(key, value):
    """Store value in cache with timestamp."""
    _cache[key] = value
    _cache_timestamps[key] = time.time()


def invalidate_cache(pattern=None):
    """Clear cache entries matching pattern or all if pattern is None."""
    if pattern is None:
        _cache.clear()
        _cache_timestamps.clear()
    else:
        keys_to_delete = [k for k in _cache.keys() if pattern in k]
        for key in keys_to_delete:
            del _cache[key]
            del _cache_timestamps[key]


def cache_result(cache_key_prefix):
    """Decorator to cache function results."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Build cache key from function name + args
            cache_key = f"{cache_key_prefix}:{str(args)}:{str(kwargs)}"
            
            cached = get_cache(cache_key)
            if cached is not None:
                return cached
            
            result = f(*args, **kwargs)
            set_cache(cache_key, result)
            return result
        return wrapper
    return decorator


def cache_user_data(user_id, ttl_seconds=1800):
    """Decorator to cache per-user data."""
    def decorator(f):
        @wraps(f)
        def wrapper(uid, *args, **kwargs):
            cache_key = f"user_data:{uid}:{f.__name__}"
            
            cached = get_cache(cache_key)
            if cached is not None:
                return cached
            
            result = f(uid, *args, **kwargs)
            set_cache(cache_key, result)
            return result
        return wrapper
    return decorator
