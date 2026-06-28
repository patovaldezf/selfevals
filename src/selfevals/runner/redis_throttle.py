"""Distributed token bucket backed by an atomic Redis Lua script.

`AsyncTokenBucket` (in `throttle.py`) caps requests/minute *per process*. With N
sharded workers each holding their own bucket, the effective rate against the
provider is `N x requests_per_minute` — which blows the real Anthropic/OpenAI
quota and storms 429s. `RedisTokenBucket` moves the bucket state into Redis under
a key shared by every worker of the same `(workspace, provider)`, so the cap is
GLOBAL across the fleet.

It satisfies the same `acquire` contract as `AsyncTokenBucket` (the `TokenBucket`
Protocol in `throttle.py`), so it is a drop-in for `RateLimitedAdapter`.

The clock is Redis's own server clock (`redis.call('TIME')` inside the Lua
script), NOT the local monotonic clock: N workers on N hosts must agree on "now"
for the refill math to be coherent — local clocks aren't comparable across hosts
and monotonic clocks aren't comparable across processes at all. Using `TIME`
inside a script is safe on Redis >= 5 (the pinned floor): effects replication is
the default, so the script's *writes* (HSET/EXPIRE) are replicated, not the
non-deterministic script body. This is the classic rate-limiter idiom.

On Redis failure the bucket fails OPEN (default): `acquire` lets the request
through rather than stalling the run. A limiter is a protective optimization, not
a correctness gate — the worst case of failing open is some 429s, which the inner
`RetryingAdapter` already absorbs. Failing closed would turn a Redis blip into a
total outage of the primary workload. Set `fail_open=False` to opt into a hard
cap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

logger = logging.getLogger(__name__)

# Token-bucket as an atomic Lua script. Returns 0 when it consumed the tokens, or
# the number of MILLISECONDS the caller should wait (without consuming). State is
# a hash {tokens, ts} where ts is the server clock in ms.
_TOKEN_BUCKET_LUA: Final[str] = """
-- KEYS[1] = bucket key (hash: { tokens, ts })  ts = server time in ms
-- ARGV[1] = rate_per_sec   (tokens added per second, float)
-- ARGV[2] = capacity       (max tokens the bucket holds, float)
-- ARGV[3] = requested      (tokens this call wants, float, normally 1.0)
-- ARGV[4] = ttl_seconds    (expiry refresh for the key, integer)
--
-- Returns: 0          -> acquired (tokens consumed, state written)
--          <wait_ms>  -> NOT acquired; sleep wait_ms and retry (no consume)

local rate      = tonumber(ARGV[1])
local capacity  = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local ttl       = tonumber(ARGV[4])

-- Server clock: one reference for every worker. TIME = { seconds, micros }.
local t   = redis.call('TIME')
local now = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)

-- Load prior state; a fresh bucket starts full so the first request is free.
local data    = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens  = tonumber(data[1])
local last_ms = tonumber(data[2])
if tokens == nil or last_ms == nil then
  tokens  = capacity
  last_ms = now
end

-- Lazy refill: add elapsed_seconds * rate, clamp to capacity. Guard a clock that
-- appears to go backwards (NTP step / replica skew) so we never remove tokens.
local elapsed = (now - last_ms) / 1000.0
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + (elapsed * rate))

local wait_ms = 0
-- Epsilon mirrors the in-process bucket: float refill can leave us a hair below
-- the threshold forever, so treat "within epsilon" as enough.
if tokens >= (requested - 1e-9) then
  tokens = tokens - requested
else
  local deficit = requested - tokens
  -- ceil so we never wake a touch too early and loop with wait ~ 0.
  wait_ms = math.ceil((deficit / rate) * 1000.0)
  -- Do NOT decrement on a miss: the caller sleeps and retries; consuming here
  -- would let concurrent waiters double-charge the bucket.
end

-- Persist refilled tokens + the timestamp we refilled against, on BOTH paths, so
-- the next call doesn't recompute the refill from a stale ts (double-counting).
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)

return wait_ms
"""

# Bounds pathological churn. Each loop sleeps the server-returned wait, so this is
# only a backstop — with capacity >= requested (schema guarantees capacity >= 1
# and we request 1.0) acquire converges in one or two iterations.
_MAX_ACQUIRE_ATTEMPTS: Final[int] = 10_000


def _redis_error_types() -> tuple[type[BaseException], ...]:
    """Redis/client errors that may be handled by the limiter boundary.

    ``redis`` is an optional extra, so this cannot import Redis exceptions at
    module import time.
    """
    error_types: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        from redis.exceptions import RedisError
    except ImportError:
        return tuple(error_types)
    error_types.append(RedisError)
    return tuple(error_types)


def _script_lookup_error_types() -> tuple[type[BaseException], ...]:
    return (*_redis_error_types(), RuntimeError)


def _is_noscript_error(exc: BaseException) -> bool:
    try:
        from redis.exceptions import NoScriptError
    except ImportError:
        return "NOSCRIPT" in str(exc)
    return isinstance(exc, NoScriptError) or "NOSCRIPT" in str(exc)


class RedisTokenBucket:
    """Global token bucket: an atomic Lua script over Redis-shared state.

    Same `acquire` contract as `AsyncTokenBucket`, so it drops into
    `RateLimitedAdapter`. See the module docstring for the clock and fail-open
    rationale.
    """

    def __init__(
        self,
        *,
        redis_url: str,
        key: str,
        rate_per_sec: float,
        capacity: float,
        fail_open: bool = True,
        key_ttl_seconds: int = 3600,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        client: Any = None,
    ) -> None:
        """`client` is an injection seam for tests (an async stand-in with
        eval/evalsha/script_load); production leaves it None and we build a real
        `redis.asyncio` client from `redis_url`."""
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._key = key
        self._rate = rate_per_sec
        self._capacity = capacity
        self._fail_open = fail_open
        self._ttl = key_ttl_seconds
        self._sleep = sleep
        self._failed_open_once = False
        if client is not None:
            self._client: Any = client
        else:
            try:
                import redis.asyncio as async_redis
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "Redis rate limiting requires the redis extra: "
                    "pip install 'selfevals[redis]'"
                ) from exc
            # decode_responses=False: the script returns a numeric ms wait; we
            # coerce with float() at the boundary and don't need string decoding.
            self._client = async_redis.Redis.from_url(redis_url)
        self._sha: str | None = None

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available globally, then consume them."""
        for _ in range(_MAX_ACQUIRE_ATTEMPTS):
            try:
                wait_ms = await self._eval(tokens)
            except _redis_error_types():  # redis down / timeout / connection error
                if self._fail_open:
                    if not self._failed_open_once:
                        self._failed_open_once = True
                        logger.warning(
                            "redis rate limiter unreachable; failing open "
                            "(requests pass through, 429 retry is the backstop)"
                        )
                    return
                raise
            if wait_ms <= 0:
                return  # tokens consumed server-side
            await self._sleep(wait_ms / 1000.0)
        # Defensive: unreachable given capacity >= tokens.
        return

    async def _eval(self, tokens: float) -> float:
        """Run the Lua script, returning milliseconds to wait (0 == acquired).

        Uses EVALSHA (script cached by Redis) and falls back to EVAL + caches the
        SHA on NOSCRIPT — the standard pattern, so no SCRIPT LOAD up front.
        """
        argv = [self._rate, self._capacity, tokens, self._ttl]
        if self._sha is not None:
            try:
                reply = await self._client.evalsha(self._sha, 1, self._key, *argv)
                return float(reply)
            except _script_lookup_error_types() as exc:
                if not _is_noscript_error(exc):
                    raise
                self._sha = None  # fall through to EVAL
        reply = await self._client.eval(_TOKEN_BUCKET_LUA, 1, self._key, *argv)
        try:
            self._sha = await self._client.script_load(_TOKEN_BUCKET_LUA)
        except _redis_error_types():  # caching the sha is best-effort
            self._sha = None
        return float(reply)

    async def aclose(self) -> None:
        """Close the underlying client (best-effort)."""
        close = getattr(self._client, "aclose", None)
        if callable(close):
            await close()
