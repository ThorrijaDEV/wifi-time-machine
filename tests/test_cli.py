"""CLI tests with mocked scanning backends (no real hardware needed)."""

import json
from types import SimpleNamespace

import pytest

from wtm import cli
from wtm.scan import BackendUnavailable, ScanResult
from wtm.util import parse_time


def make_result(**overrides):
    base = dict(
        bssid="aa:bb:cc:dd:ee:01",
        ssid="HOME-NET",
        channel=36,
        frequency=5180,
        signal=-42,
        security="wpa2-psk",
        backend="iw",
    )
    base.update(overrides)
    return ScanResult(**base)


def run(args, monkeypatch, tmp_path):
    """Invoke main() with a temp db and mocked scan backend."""
    db = tmp_path / "test.db"

    def fake_scan(backend=None):
        return "iw", [
            make_result(signal=-42),
            make_result(bssid="aa:bb:cc:dd:ee:02", ssid="OFFICE", channel=6, signal=-70),
        ]

    monkeypatch.setattr("wtm.scan.scan", fake_scan)
    monkeypatch.setattr("wtm.scan.detect_backend", lambda: "iw")
    return cli.main(["--db", str(db), *args]), db


def test_scan_table(capsys, monkeypatch, tmp_path):
    code, _ = run(["scan"], monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "HOME-NET" in out
    assert "2 networks observed via iw" in out


def test_scan_json(capsys, monkeypatch, tmp_path):
    code, _ = run(["scan", "--json"], monkeypatch, tmp_path)
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert len(data) == 2
    assert data[0]["ssid"] == "HOME-NET"
    assert data[0]["bssid"] == "aa:bb:cc:dd:ee:01"


def test_scan_store(capsys, monkeypatch, tmp_path):
    code, db = run(["scan", "--store"], monkeypatch, tmp_path)
    assert code == cli.EXIT_OK
    assert "stored 2 observations (2 new networks)" in capsys.readouterr().out
    assert db.exists()


def test_scan_no_backend(monkeypatch, tmp_path):
    def boom(backend=None):
        raise BackendUnavailable("no scanning backend found (iw, iwd, nmcli)")

    monkeypatch.setattr("wtm.scan.scan", boom)
    code = cli.main(
        ["--db", str(tmp_path / "t.db"), "scan"],
    )
    assert code == cli.EXIT_NO_BACKEND


def test_networks_empty(capsys, monkeypatch, tmp_path):
    code, _ = run(["networks"], monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "no networks recorded yet" in out


def test_store_then_networks(capsys, monkeypatch, tmp_path):
    run(["scan", "--store"], monkeypatch, tmp_path)
    code, _ = run(["networks"], monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "HOME-NET" in out
    assert "OFFICE" in out


def test_history_and_timeline_and_channels(capsys, monkeypatch, tmp_path):
    run(["scan", "--store"], monkeypatch, tmp_path)
    capsys.readouterr()
    for sub in ["history", "timeline", "channels", "status", "export"]:
        code, _ = run([sub], monkeypatch, tmp_path)
        assert code == cli.EXIT_OK, sub
    out = capsys.readouterr().out
    assert "aa:bb:cc:dd:ee:01" in out


def test_export_csv_file(capsys, monkeypatch, tmp_path):
    run(["scan", "--store"], monkeypatch, tmp_path)
    capsys.readouterr()  # flush scan table output
    out_file = tmp_path / "obs.csv"
    code, _ = run(
        ["export", "--format", "csv", "--output", str(out_file)],
        monkeypatch,
        tmp_path,
    )
    assert code == cli.EXIT_OK
    content = out_file.read_text()
    assert "HOME-NET" in content
    assert "sanitize" in capsys.readouterr().err


def test_export_sanitized(capsys, monkeypatch, tmp_path):
    run(["scan", "--store"], monkeypatch, tmp_path)
    capsys.readouterr()  # flush scan table output
    code, _ = run(["export", "--sanitize"], monkeypatch, tmp_path)
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "aa:bb:cc:dd:ee:01" not in out
    assert '"bssid":' in out


def test_bad_time_spec(capsys, monkeypatch, tmp_path):
    code, _ = run(["history", "--since", "not-a-time"], monkeypatch, tmp_path)
    assert code == cli.EXIT_ERROR
    assert "error" in capsys.readouterr().err


def test_bad_db_path_is_clean_error(capsys, monkeypatch, tmp_path):
    db = tmp_path / "broken.db"
    db.write_bytes(b"garbage")
    monkeypatch.setattr("wtm.scan.scan", lambda b=None: ("iw", []))
    code = cli.main(["--db", str(db), "scan", "--store"])
    assert code == cli.EXIT_ERROR


def test_main_unknown_command():
    assert cli.main(["frobnicate"]) != cli.EXIT_OK


def test_record_respects_limit(capsys, monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    calls = {"n": 0}
    monkeypatch.setattr("wtm.cli.time.sleep", lambda _: None)

    def fake_scan(backend=None):
        calls["n"] += 1
        return "iw", [make_result(signal=-40 - calls["n"])]

    monkeypatch.setattr("wtm.scan.scan", fake_scan)
    code = cli.main(["--db", str(db), "record", "--interval", "5", "--limit", "3"])
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    # 1 backend probe + 3 recording scans
    assert calls["n"] == 4
    assert "scan 3" in out
    assert "session finished: 3 scans" in out
    assert "1 new networks" in out  # same BSSID every scan


def test_record_interrupt_ends_session(capsys, monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    n = {"n": 0}
    monkeypatch.setattr("wtm.cli.time.sleep", lambda _: None)

    def fake_scan(backend=None):
        n["n"] += 1
        if n["n"] > 1:
            raise KeyboardInterrupt
        return "iw", [make_result()]

    monkeypatch.setattr("wtm.scan.scan", fake_scan)
    code = cli.main(["--db", str(db), "record", "--interval", "5"])
    assert code == cli.EXIT_OK
    assert "session finished: 0 scans" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# time parsing
# ---------------------------------------------------------------------------


def test_parse_time_relative(monkeypatch):
    monkeypatch.setattr("wtm.util.now_epoch", lambda: 1_000_000)
    assert parse_time("30m") == 1_000_000 - 1800
    assert parse_time("2h") == 1_000_000 - 7200
    assert parse_time("1d") == 1_000_000 - 86400
    assert parse_time("1w") == 1_000_000 - 604800
    assert parse_time("10s") == 1_000_000 - 10


def test_parse_time_absolute():
    from datetime import datetime, timezone

    base = int(datetime(2026, 8, 11, tzinfo=timezone.utc).timestamp())
    assert parse_time("2026-08-11") == base
    assert parse_time("2026-08-11T10:30") == base + 10 * 3600 + 30 * 60
    assert parse_time("2026-08-11 10:30:00") == base + 10 * 3600 + 30 * 60
    assert parse_time("2026/08/11") == base


def test_parse_time_hhmm_uses_today(monkeypatch):
    now = 1783900000
    parsed = parse_time("23:59", now=now)
    assert -172800 < parsed - now < 172800  # same calendar day, local tz allowed


def test_parse_time_invalid():
    with pytest.raises(ValueError):
        parse_time("garbage")
    assert parse_time(None) is None
    assert parse_time("  ") is None
