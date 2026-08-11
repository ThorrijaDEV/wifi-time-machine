"""Database tests: schema, upsert behaviour, and queries."""

from pathlib import Path

import pytest

from wtm import db as dbmod
from wtm.db import connect, default_db_path, record_scan
from wtm.scan import ScanResult


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "test.db")
    yield db
    db.close()


def make_scan(**overrides):
    base = dict(
        bssid="aa:bb:cc:dd:ee:01",
        ssid="HOME-NET",
        channel=36,
        frequency=5180,
        signal=-42,
        security="wpa2-psk",
    )
    base.update(overrides)
    return ScanResult(**base)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_schema_created(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"sessions", "observations", "networks", "meta"} <= tables


def test_default_db_path_is_file():
    path = default_db_path()
    assert str(path).endswith(".db")
    assert "wifi-time-machine" in str(path)


# ---------------------------------------------------------------------------
# record_scan
# ---------------------------------------------------------------------------


def test_first_scan_inserts_network_and_observation(conn):
    res = record_scan(conn, [make_scan()])
    assert res == {"stored": 1, "new_networks": 1}

    row = conn.execute("SELECT * FROM networks").fetchone()
    assert row["bssid"] == "aa:bb:cc:dd:ee:01"
    assert row["ssid"] == "HOME-NET"
    assert row["first_seen"] == row["last_seen"]
    assert row["times_seen"] == 1

    o = conn.execute("SELECT * FROM observations").fetchone()
    assert o["bssid"] == "aa:bb:cc:dd:ee:01"
    assert o["signal"] == -42
    assert o["ssid"] == "HOME-NET"
    assert o["security"] == "wpa2-psk"
    assert o["channel"] == 36
    assert o["frequency"] == 5180


def test_second_scan_updates_network_and_appends_observation(conn):
    record_scan(conn, [make_scan(signal=-42)], observed_at=1000)
    res = record_scan(conn, [make_scan(signal=-50)], observed_at=2000)
    assert res == {"stored": 1, "new_networks": 0}

    net = conn.execute("SELECT * FROM networks").fetchone()
    assert net["first_seen"] != net["last_seen"]
    assert net["times_seen"] == 2

    signals = [
        r["signal"]
        for r in conn.execute(
            "SELECT signal FROM observations ORDER BY observed_at"
        )
    ]
    assert signals == [-42, -50]


def test_ssid_and_security_are_refreshed(conn):
    record_scan(conn, [make_scan()])
    record_scan(conn, [make_scan(ssid="HOME-NET-X", security="wpa3-sae")])
    net = conn.execute("SELECT * FROM networks").fetchone()
    assert net["ssid"] == "HOME-NET-X"
    obs = conn.execute(
        "SELECT security FROM observations ORDER BY observed_at"
    ).fetchall()
    assert [o["security"] for o in obs] == ["wpa2-psk", "wpa3-sae"]


def test_seen_only_increments_visibility(conn):
    # Network visible in scan 1, gone in scan 2 -> times_seen stays 1.
    record_scan(conn, [make_scan()])
    record_scan(conn, [])
    net = conn.execute("SELECT * FROM networks").fetchone()
    assert net["times_seen"] == 1


def test_multi_network_scan(conn):
    record_scan(
        conn,
        [
            make_scan(bssid="aa:bb:cc:dd:ee:01", ssid="NET-A"),
            make_scan(bssid="aa:bb:cc:dd:ee:02", ssid="NET-B"),
        ],
    )
    assert conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def test_list_networks(conn):
    record_scan(
        conn,
        [
            make_scan(bssid="aa:bb:cc:dd:ee:01", ssid="NET-A", signal=-50),
            make_scan(bssid="aa:bb:cc:dd:ee:02", ssid="NET-B", signal=-80),
        ],
    )
    rows = dbmod.list_networks(conn, sort="signal")
    assert [r["ssid"] for r in rows] == ["NET-A", "NET-B"]  # strongest first
    assert rows[0]["avg_signal"] == -50.0


def test_observations_within_window(conn):
    record_scan(conn, [make_scan(signal=-42)], observed_at=1000)
    record_scan(conn, [make_scan(signal=-55)], observed_at=2000)
    rows = conn.execute(
        "SELECT * FROM observations WHERE observed_at >= ?", (1500,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["signal"] == -55


def test_query_with_params_escaped(conn):
    record_scan(conn, [make_scan(ssid="O'Reilly-NET")])
    rows = conn.execute(
        "SELECT * FROM networks WHERE ssid = ?", ("O'Reilly-NET",)
    ).fetchall()
    assert len(rows) == 1


def test_corrupt_db_raises(conn, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database")
    with pytest.raises(Exception):
        connect(bad)


def test_meta_schema_version(conn):
    version = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    assert version == "1"
