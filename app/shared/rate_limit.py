import time
from collections import defaultdict, deque


DEFAULT_LIMIT = 120
DEFAULT_WINDOW_SECONDS = 60
MUTATION_LIMIT = 60

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(key: str, *, limit: int = DEFAULT_LIMIT, window_seconds: int = DEFAULT_WINDOW_SECONDS) -> tuple[bool, int]:
    now = time.monotonic()
    bucket = _BUCKETS[key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False, 0
    bucket.append(now)
    return True, max(0, limit - len(bucket))


def limit_for_method(method: str) -> int:
    return MUTATION_LIMIT if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} else DEFAULT_LIMIT
