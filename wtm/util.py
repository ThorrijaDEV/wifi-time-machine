"""Small shared helpers: time parsing, BSSID handling, frequency mapping."""

from __future__ import annotations

import datetime as _dt
import hashlib
import re

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(r"^(\d+)([smhdw])$")


def now_epoch() -> int:
    """Current time as Unix epoch seconds (UTC)."""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def parse_time(spec: str | None, now: int | None = None) -> int | None:
    """Parse a user time spec into epoch seconds.

    Accepts: ``2026-08-11``, ``2026-08-11T10:30``, ``2026-08-11 10:30``,
    ``10:30`` (today), or relative specs such as ``30m``, ``2h``, ``1d``, ``1w``.
    Returns None for an empty spec.
    """
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None
    now = now or now_epoch()
    m = _RELATIVE_RE.match(spec)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return now - int(m.group(1)) * mult
    text = spec.replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%H:%M"):
        try:
            dt = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            dt = dt.replace(
                year=_dt.datetime.fromtimestamp(now).year,
                month=_dt.datetime.fromtimestamp(now).month,
                day=_dt.datetime.fromtimestamp(now).day,
            )
        return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())
    raise ValueError(f"cannot parse time spec: {spec!r}")


def fmt_time(epoch: int) -> str:
    """Format epoch seconds as local ``YYYY-MM-DD HH:MM:SS``."""
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def fmt_time_hm(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch).strftime("%H:%M")


def ago(epoch: int, now: int | None = None) -> str:
    """Human duration like ``3h12m`` since epoch."""
    delta = max(0, (now or now_epoch()) - epoch)
    h, rem = divmod(delta, 3600)
    m, _s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# BSSID / frequency helpers
# ---------------------------------------------------------------------------

_BSSID_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def normalize_bssid(bssid: str) -> str:
    """Normalize to lowercase ``aa:bb:cc:dd:ee:ff``; raises ValueError if invalid."""
    if not _BSSID_RE.match(bssid):
        raise ValueError(f"invalid BSSID: {bssid!r}")
    return bssid.lower()


def hash_bssid(bssid: str) -> str:
    """Privacy-preserving identifier: first 12 hex chars of SHA-256 (non-reversible)."""
    return hashlib.sha256(normalize_bssid(bssid).encode("ascii")).hexdigest()[:12]


def freq_to_channel(freq_mhz: int) -> int:
    """Map a center frequency in MHz to its 802.11 channel number."""
    if freq_mhz is None:
        return None
    if 2412 <= freq_mhz <= 2484:
        if freq_mhz == 2484:
            return 14
        return (freq_mhz - 2407) // 5
    if 5000 <= freq_mhz <= 5900:
        return (freq_mhz - 5000) // 5
    if 5925 <= freq_mhz <= 7125:
        return (freq_mhz - 5925) // 5
    return None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
