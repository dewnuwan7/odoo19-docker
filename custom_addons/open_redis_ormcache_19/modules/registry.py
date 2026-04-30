# -*- coding: utf-8 -*-
"""
Registry cache enhancement for Redis storage in Odoo 19

This module patches the Registry class to use Redis as the backend
for ORM cache instead of the default in-memory cache.

Odoo 19 Compatibility:
- Updated Registry import path for Odoo 19
- Enhanced error handling for improved stability
- Support for modern Redis client versions
"""

import logging

from odoo.tools import config
from odoo.modules.registry import Registry

from ..tools.RedisLRU import RedisLRU

_logger = logging.getLogger(__name__)


# Store the original init method
_original_init = Registry.init


def _patched_init(self, db_name):
    """
    Patched init method that replaces __caches with Redis-backed caches.

    This method calls the original init, then replaces the LRU cache instances
    in self.__caches with RedisLRU instances if Redis is configured.
    
    Args:
        db_name: Database name
    """
    # Call the original init method first
    _original_init(self, db_name)

    # Check if Redis is configured
    redis_url = config.get('ormcache_redis_url')
    if not redis_url:
        _logger.debug(
            "ormcache_redis_url not configured for database %s. Using default in-memory cache.",
            db_name
        )
        return

    try:
        # Import redis here (delayed import) to ensure it's fully initialized
        import redis

        expire_time = config.get('ormcache_redis_expire', 604800)  # default 7 days

        _logger.info(
            "redis module loaded from %s (version: %s)",
            getattr(redis, "__file__", "unknown"),
            getattr(redis, "__version__", "unknown"),
        )

        # Determine which client class is available. Some environments might
        # shadow the official `redis` package, so we fall back gracefully.
        client_cls = None

        # Try importing from redis.client directly to handle cases where
        # __init__ doesn't expose the classes.
        try:
            from redis import client as redis_client
            client_cls = (
                getattr(redis_client, "Redis", None)
                or getattr(redis_client, "StrictRedis", None)
            )
        except Exception as import_err:
            _logger.info("redis.client import failed: %s", import_err)

        if not client_cls:
            client_cls = (
                getattr(redis, "Redis", None)
                or getattr(redis, "StrictRedis", None)
                or getattr(getattr(redis, "client", None), "Redis", None)
                or getattr(getattr(redis, "client", None), "StrictRedis", None)
            )

        if not client_cls:
            # Last-resort fallback: use module-level from_url if present.
            from_url_fn = (
                getattr(redis, "from_url", None)
                or getattr(getattr(redis, "client", None), "from_url", None)
            )
            if from_url_fn:
                _logger.warning(
                    "Redis client class not found; falling back to redis.from_url. "
                    "Check for shadowed modules if this is unexpected."
                )
                r = from_url_fn(redis_url, decode_responses=False)
            else:
                raise ImportError(
                    "Redis client class not found. Ensure the `redis` package is installed and not shadowed."
                )

        # Create Redis connection using the detected client class
        if client_cls:
            _logger.debug(f"Connecting to Redis at {redis_url} with client {client_cls.__name__}")
            r = client_cls.from_url(redis_url, decode_responses=False)

        # Test connection
        _logger.debug("Testing Redis connection with ping()")
        r.ping()
        _logger.debug("Redis connection successful!")

        # Replace all LRU caches in __caches with Redis-backed caches
        # Access using name mangling: _Registry__caches
        caches_dict = self._Registry__caches
        
        for cache_name in list(caches_dict.keys()):
            try:
                caches_dict[cache_name] = RedisLRU(
                    r,
                    f"{db_name}_{cache_name}",  # namespace includes cache_name for isolation
                    expire_time
                )
            except Exception as cache_err:
                _logger.error(
                    "Failed to initialize Redis cache for '%s': %s. Keeping default cache.",
                    cache_name,
                    cache_err
                )
                # Keep the original cache if RedisLRU initialization fails
                pass

        _logger.info(
            "Redis ORM cache initialized for database '%s' with TTL %ss (caches: %s)",
            db_name,
            expire_time,
            ', '.join(caches_dict.keys())
        )
    except Exception as e:
        _logger.error(
            "Failed to connect to Redis for database '%s': %s. Falling back to in-memory cache.",
            db_name,
            e,
            exc_info=True
        )


# Apply the monkey patch if Redis is configured
if config.get('ormcache_redis_url'):
    Registry.init = _patched_init
    _logger.info("Redis ORM cache successfully registered for Odoo 19")
