"""Visualization rendering tests (pure string functions)."""

from wtm.viz import (
    _glyph,
    nice_bucket,
    render_bars,
    render_sparkline,
    render_timeline,
)


def test_glyph_levels():
    assert _glyph(-1) == "█"
    assert _glyph(-50) == "█"
    assert _glyph(-55) == "▆"
    assert _glyph(-65) == "▄"
    assert _glyph(-75) == "▂"
    assert _glyph(-85) == "·"


def test_nice_bucket():
    assert nice_bucket(0) == 60
    assert nice_bucket(5) == 60
    assert nice_bucket(60) == 60
    assert nice_bucket(61) == 120
    assert nice_bucket(200) == 300
    assert nice_bucket(3600) == 3600
    assert nice_bucket(9000) == 14400


def test_render_timeline_columns():
    observations = [
        (0, "aa:bb:cc:dd:ee:01", "NET-A", -42),
        (60, "aa:bb:cc:dd:ee:01", "NET-A", -45),
        (120, "aa:bb:cc:dd:ee:01", "NET-A", -48),
        (30, "aa:bb:cc:dd:ee:02", "NET-B", -80),
        (90, "aa:bb:cc:dd:ee:02", "NET-B", -85),
        (150, "aa:bb:cc:dd:ee:03", "NET-C", -60),
    ]
    lines = render_timeline(observations, bucket=60).splitlines()
    assert lines[0].startswith(" " * 22)  # header row, label gutter
    # header + 3 network rows + legend
    assert len(lines) == 5
    assert "NET-A" in lines[1]
    assert "NET-B" in lines[2]
    assert "NET-C" in lines[3]
    glyph_count = lambda s: sum(s.count(g) for g in "·▂▄▆█")
    assert glyph_count(lines[1]) == 3  # seen in all buckets
    assert glyph_count(lines[2]) == 2
    assert glyph_count(lines[3]) == 1
    assert lines[4].startswith("buckets: 60s")


def test_render_timeline_single_row():
    observations = [(0, "aa:bb:cc:dd:ee:01", "NET-A", -42)]
    lines = render_timeline(observations, bucket=60).splitlines()
    assert len(lines) == 3  # header + row + legend
    assert "NET-A" in lines[1]


def test_render_timeline_empty():
    out = render_timeline([], bucket=60)
    assert "no observations" in out


def test_render_timeline_all_signal_none():
    out = render_timeline([(0, "aa:bb:cc:dd:ee:01", "NET-A", None)], bucket=60)
    assert "no observations" in out


def test_render_sparkline():
    assert render_sparkline([-30, -50, -70, -90]) == "█▆▃▁"
    assert render_sparkline([]) == "(no data)"
    assert render_sparkline([-50]) == "·"


def test_render_sparkline_scales():
    s = render_sparkline([-120, -110, -90, -70, -50, -30, -1])
    assert s == "▁▁▃▄▅▇█"


def test_render_bars():
    out = render_bars([("NET-A", 5, "")], width=10)
    assert out.startswith("NET-A")
    assert out.rstrip().endswith("5")
    assert out.count("█") == 10

    assert render_bars([], width=10) == "(no data)"

    # zero value renders no bar
    out = render_bars([("NET-A", 0, "")], width=10)
    assert "█" not in out
