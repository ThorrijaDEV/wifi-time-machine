"""Terminal visualizations.

Pure rendering functions (no DB/CLI coupling) so they are unit-testable.
"""

from __future__ import annotations

import shutil

from wtm.util import fmt_time_hm

# Signal-level glyphs for the timeline heatmap.
_GLYPHS = ("·", "▂", "▄", "▆", "█")  # index 0 = weakest
_LEVELS = (-80, -70, -60, -50)  # ascending thresholds for glyph tiers


def _glyph(signal: int) -> str:
    level = 0
    for thresh in _LEVELS:
        if signal >= thresh:
            level += 1
    return _GLYPHS[level]


def terminal_width(fallback: int = 100) -> int:
    try:
        return shutil.get_terminal_size((fallback, 24)).columns
    except OSError:
        return fallback


def nice_bucket(seconds: float) -> int:
    """Round a seconds value up to a 'nice' bucket size."""
    for step in (60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400):
        if seconds <= step:
            return step
    return 86400


def render_timeline(
    observations: list[tuple[int, str, str | None, int | None]],
    bucket: int | None = None,
    width: int | None = None,
    label_width: int = 22,
) -> str:
    """Visibility heatmap: rows = networks, columns = time buckets.

    ``observations``: ``(observed_at_epoch, bssid, ssid, signal_dbm)``.
    Cell glyph encodes the average signal within the bucket; a blank cell
    means the network was not observed in that bucket.
    """
    if not observations:
        return "(no observations in the selected window)"
    t0 = min(o[0] for o in observations)
    t1 = max(o[0] for o in observations)
    span = max(t1 - t0, 1)
    if width is None:
        width = max(20, terminal_width() - label_width - 2)
    width = min(width, 120)
    if bucket is None:
        bucket = nice_bucket(span / width)
    else:
        bucket = max(1, bucket)
    ncols = min(width, int(span / bucket) + 1)

    # Group observations per network.
    nets: dict[str, tuple[str, list[int]]] = {}
    for ts, bssid, ssid, signal in observations:
        if signal is None:
            continue
        entry = nets.setdefault(bssid, (ssid or bssid, []))
        entry[1].append((ts, signal))
    if not nets:
        return "(no observations with signal data in the selected window)"

    # Bucket boundaries aligned to the earliest observation.
    start = t0 - (t0 % bucket)
    col_of = lambda ts: min((ts - start) // bucket, ncols - 1)

    # First-seen order (most recent first looks best at top is debatable;
    # stable first-seen order keeps the reading consistent).
    order = sorted(nets.items(), key=lambda kv: min(o[0] for o in kv[1][1]))

    lines: list[str] = []
    # Time axis header.
    header = [" " * label_width]
    prev_label = None
    for c in range(ncols):
        t = start + c * bucket
        if c % 5 == 0 or c == ncols - 1:
            if prev_label != t:
                header.append(fmt_time_hm(t).rjust(1))
                prev_label = t
            else:
                header.append(" ")
        else:
            header.append(" ")
    lines.append("".join(header))

    for bssid, (label, pts) in order:
        cells = [""] * ncols
        sums: dict[int, list[int]] = {}
        for ts, signal in pts:
            sums.setdefault(col_of(ts), []).append(signal)
        for col, signals in sums.items():
            cells[col] = _glyph(sum(int(s) for s in signals) // len(signals))
        label = label[: label_width - 1]
        lines.append(label.rjust(label_width) + " " + "".join(cells))

    lines.append(
        f"buckets: {bucket}s · glyphs: {_GLYPHS[4]}≥-50 {_GLYPHS[3]}≥-60 "
        f"{_GLYPHS[2]}≥-70 {_GLYPHS[1]}≥-80 {_GLYPHS[0]}weaker · "
        f"blank = not observed"
    )
    return "\n".join(lines)


def render_sparkline(values: list[int], width: int = 40) -> str:
    """Compact signal-over-time sparkline using block characters."""
    if not values:
        return "(no data)"
    if len(values) == 1:
        return _GLYPHS[0]
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1)
    step = max(1, len(values) // width) if len(values) > width else 1
    sampled = values[::step][:width]
    out = []
    for v in sampled:
        norm = (v - lo) / span
        idx = min(7, int(norm * 8))
        out.append("▁▂▃▄▅▆▇█"[idx])
    return "".join(out)


def render_bars(items: list[tuple[str, int, str]], width: int = 40) -> str:
    """Horizontal bars: ``(label, value, suffix)`` tuples."""
    if not items:
        return "(no data)"
    max_val = max(v for _, v, _ in items) or 1
    max_label = max(len(l) for l, _, _ in items)
    lines = []
    for label, value, suffix in items:
        bar = "█" * max(1, round(value / max_val * width)) if value else ""
        lines.append(f"{label.rjust(max_label)}  {bar} {value}{suffix}")
    return "\n".join(lines)
