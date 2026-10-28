"""Password hashing and a small in-memory rate limiter."""
import base64
import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque

_ITERATIONS = 600_000  # PBKDF2-HMAC-SHA256 work factor (OWASP recommendation)


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Used to burn the same time when a username does not exist (no user enumeration by timing).
DUMMY_HASH = hash_password("not-a-real-password", iterations=_ITERATIONS)


class RateLimiter:
    """Sliding-window limiter: at most `limit` events per `window` seconds for each key.

    Kept in process memory, so with several servers each one counts separately. That is
    acceptable for brute-force and abuse protection here; a shared store would be needed
    for exact global limits.
    """

    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window_seconds, clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._events[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def allow(self, key: str) -> bool:
        """Record an attempt; False when the key is over its limit."""
        with self._lock:
            now = self._clock()
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def blocked(self, key: str) -> bool:
        """True if the key is at its limit (without recording a new event)."""
        with self._lock:
            return len(self._prune(key, self._clock())) >= self.limit

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
