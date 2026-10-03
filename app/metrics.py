from collections import defaultdict

RECEIVED = "mail.received"
DUPLICATES = "mail.duplicates"
SENT = "mail.sent"
FAILED = "mail.failed"
RETRIES = "mail.retries"
LATENCY = "mail.send.latency"
PENDING = "mail.queue.pending"
OLDEST_AGE = "mail.queue.oldest.age"
DLQ = "mail.dlq"

_COUNTERS: dict[str, int] = defaultdict(int)
_GAUGES: dict[str, float] = {}


def inc(name: str, n: int = 1) -> None:
    _COUNTERS[name] += n


def set_gauge(name: str, value: float) -> None:
    _GAUGES[name] = float(value)


def snapshot() -> dict[str, float]:
    return {**_COUNTERS, **_GAUGES}


def reset() -> None:
    _COUNTERS.clear()
    _GAUGES.clear()
