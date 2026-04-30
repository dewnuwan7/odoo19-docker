# -*- coding: utf-8 -*-
"""
Redis-based LRU cache implementation for Odoo 19 ORM cache.

License: LGPL-3
"""

__all__ = ['RedisLRU']

import logging
import pickle
import types
import functools

_logger = logging.getLogger(__name__)


def to_str(obj, depth=0, max_depth=5, _seen=None):
    """
    Convert a Python object to a string representation suitable for cache keys.
    Uses a conservative approach to prevent recursion issues with complex objects.

    Args:
        obj: Any Python object
        depth: Current recursion depth (internal use)
        max_depth: Maximum recursion depth (reduced to 5 for safety)
        _seen: Set of object ids already seen (internal use)

    Returns:
        str: String representation of the object
    """
    # Initialize seen set on first call
    if _seen is None:
        _seen = set()

    # Prevent excessive depth early
    if depth >= max_depth:
        return f"<depth:{type(obj).__name__}@{id(obj)}>"

    # Prevent infinite recursion by tracking object identity
    obj_id = id(obj)
    if obj_id in _seen:
        return f"<ref:{type(obj).__name__}@{obj_id}>"

    # Handle None, booleans, numbers, and strings directly (most common cases)
    if obj is None or isinstance(obj, (bool, int, float, str, bytes)):
        try:
            return str(obj)
        except Exception:
            return f"<val:{type(obj).__name__}@{obj_id}>"

    try:
        # Add to seen set for containers
        if isinstance(obj, (tuple, list, frozenset, set, dict, functools.partial)):
            _seen.add(obj_id)

        if isinstance(obj, (tuple, list)):
            # Limit collection size to prevent huge keys
            if len(obj) > 50:
                result = f"<{type(obj).__name__}:{len(obj)}@{obj_id}>"
            else:
                items = [to_str(i, depth + 1, max_depth, _seen) for i in obj]
                result = '[' + ",".join(items) + ']'
            _seen.discard(obj_id)
            return result
        elif isinstance(obj, (frozenset, set)):
            if len(obj) > 50:
                result = f"<{type(obj).__name__}:{len(obj)}@{obj_id}>"
            else:
                # Sets are unordered, so we need to sort for consistent keys
                items = sorted(to_str(i, depth + 1, max_depth, _seen) for i in obj)
                result = '{' + ",".join(items) + '}'
            _seen.discard(obj_id)
            return result
        elif isinstance(obj, dict):
            if len(obj) > 50:
                result = f"<dict:{len(obj)}@{obj_id}>"
            else:
                items = [to_str((k, v), depth + 1, max_depth, _seen) for k, v in sorted(obj.items())]
                result = '{' + ",".join(items) + '}'
            _seen.discard(obj_id)
            return result
        elif isinstance(obj, functools.partial):
            # Handle functools.partial explicitly
            func_str = to_str(obj.func, depth + 1, max_depth, _seen)
            args_str = to_str(obj.args, depth + 1, max_depth, _seen)
            keywords_str = to_str(obj.keywords, depth + 1, max_depth, _seen)
            result = f"<partial:{func_str}({args_str}, {keywords_str})>"
            _seen.discard(obj_id)
            return result
        else:
            # For other objects, try hash first (most reliable), then repr, then fallback
            try:
                # Try to hash it - if hashable, use type + hash
                obj_hash = hash(obj)
                return f"{type(obj).__name__}:{obj_hash}"
            except TypeError:
                # Not hashable, try repr with length limit
                try:
                    obj_repr = repr(obj)
                    if len(obj_repr) > 100:
                        obj_repr = obj_repr[:100] + "..."
                    return obj_repr
                except RecursionError:
                    return f"<repr_recursion:{type(obj).__name__}@{obj_id}>"
                except Exception:
                    # Ultimate fallback: use type and id
                    return f"<obj:{type(obj).__name__}@{obj_id}>"
    except RecursionError:
        return f"<recursion:{type(obj).__name__}@{obj_id}>"
    except Exception:
        # Fallback for any other errors
        return f"<error:{type(obj).__name__}@{obj_id}>"



class RedisLRU(object):
    """
    Redis-backed LRU cache for Odoo 19 ORM.

    Uses Redis Lua scripts to maintain cache generation tracking
    and atomic operations.
    """

    def __init__(self, redis_conn, namespace, expire):
        """
        Initialize Redis LRU cache.

        Args:
            redis_conn: Redis connection instance
            namespace: Cache namespace (typically database name)
            expire: Cache expiration time in seconds (default: 7 days)
        """
        self.redis = redis_conn
        self.namespace = namespace
        self.namespace_generation = 0
        self.expire = expire or 604800  # default 7 days in seconds

        try:
            # Initialize cache generation counter
            generation_key = f"{self.namespace}_generation"
            version = self.redis.get(generation_key)
            if not version:
                self.redis.incr(generation_key)
                _logger.debug(f"Initialized cache generation for {namespace}")
        except Exception as e:
            _logger.error(f"Error initializing RedisLRU: {e}")
            raise

    def _get_cache_key(self, obj):
        """Generate Redis cache key with generation stamp."""
        key = to_str(obj)
        generation = self.redis.get(f"{self.namespace}_generation") or 0
        return f"{self.namespace}_{generation}_{key}"

    def __contains__(self, obj):
        """Check if key exists in cache."""
        try:
            key = to_str(obj)
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            cache_key = f"{self.namespace}_{generation}_{key}"
            return bool(self.redis.exists(cache_key))
        except Exception as e:
            _logger.error(f"Error checking cache key: {e}")
            return False

    def __getitem__(self, obj):
        """Retrieve value from cache."""
        try:
            key = to_str(obj)
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            cache_key = f"{self.namespace}_{generation}_{key}"
            res = self.redis.get(cache_key)
            if res:
                return pickle.loads(res)
        except pickle.UnpicklingError as e:
            _logger.error(f"Error unpickling cache value: {e}")
            raise TypeError(f"Failed to unpickle cache value: {e}")
        except Exception as e:
            _logger.error(f"Error retrieving cache key: {e}")
        raise KeyError(obj)

    def __setitem__(self, obj, val):
        """Store value in cache."""
        try:
            # Skip caching function types
            if isinstance(val, types.FunctionType):
                self.__delitem__(obj)
                return

            key = to_str(obj)
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            cache_key = f"{self.namespace}_{generation}_{key}"
            pickled_val = pickle.dumps(val)
            self.redis.setex(cache_key, self.expire, pickled_val)
        except Exception as e:
            _logger.error(f"Error setting cache key: {e}")

    def __delitem__(self, obj):
        """Delete value from cache."""
        try:
            key = to_str(obj)
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            cache_key = f"{self.namespace}_{generation}_{key}"
            self.redis.delete(cache_key)
        except Exception as e:
            _logger.error(f"Error deleting cache key: {e}")

    def get(self, obj):
        """Get value from cache, returns None if not found."""
        try:
            return self.__getitem__(obj)
        except KeyError:
            return None

    def set(self, obj, val=None):
        """Set value in cache."""
        self.__setitem__(obj, val)

    def pop(self, obj):
        """Remove and return value from cache."""
        try:
            res = self.__getitem__(obj)
            self.__delitem__(obj)
            return res
        except KeyError:
            return None

    def clear(self):
        """Clear the cache by incrementing generation counter."""
        try:
            generation_key = f"{self.namespace}_generation"
            self.redis.incr(generation_key)
            _logger.debug(f"Cleared cache for {self.namespace}")
        except Exception as e:
            _logger.error(f"Error clearing cache: {e}")

    def __len__(self):
        """Return the number of items in the cache."""
        try:
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            pattern = f"{self.namespace}_{generation}_*"
            # Use SCAN for better performance than KEYS
            count = 0
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
                count += len(keys)
                if cursor == 0:
                    break
            return count
        except Exception as e:
            _logger.error(f"Error getting cache size: {e}")
            return 0

    def __iter__(self):
        """Iterate over cache keys."""
        try:
            generation = self.redis.get(f"{self.namespace}_generation") or 0
            pattern = f"{self.namespace}_{generation}_*"
            # Use SCAN for better performance than KEYS
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    # Extract the original key from the cache key
                    # Format: {namespace}_{generation}_{original_key}
                    key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                    prefix = f"{self.namespace}_{generation}_"
                    if key_str.startswith(prefix):
                        yield key_str[len(prefix):]
                if cursor == 0:
                    break
        except Exception as e:
            _logger.error(f"Error iterating cache keys: {e}")
