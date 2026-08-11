"""SQLite storage layer: schema, recording, queries."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from wtm.scan import ScanResult
from wtm.util import now_epoch

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS networks (
    bssid       TEXT PRIMARY KEY,
    ssid        TEXT,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    times_seen  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    bssid       TEXT NOT NULL REFERENCES networks(bssid) ON DELETE CASCADE,
    ssid        TEXT,
    channel     INTEGER,
    frequency   INTEGER,
    signal      INTEGER,
    security    TEXT,
    observed_at INTEGER NOT NULL,
    backend     TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_bssid ON observations(bssid, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_time  ON observations(observed_at);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    scans      INTEGER NOT NULL DEFAULT 0,
    label      TEXT
);
"""


def default_db_path() -> Path:
    """``$XDG_DATA_HOME/wifi-time-machine/wtm.db`` (or ``WTM_DB`` override)."""
    env = os.environ.get("WTM_DB")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "wifi-time-machine" / "wtm.db"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) and initialize the database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record_scan(
    conn: sqlite3.Connection,
    results: list[ScanResult],
    observed_at: int | None = None,
    session_id: int | None = None,
) -> dict[str, int]:
    """Store one scan. Returns ``{stored, new_networks}`` counts."""
    observed_at = observed_at or now_epoch()
    stored = 0
    new_networks = 0
    for r in results:
        row = conn.execute(
            "SELECT bssid FROM networks WHERE bssid = ?", (r.bssid,)
        ).fetchone()
        if row is None:
            new_networks += 1
        conn.execute(
            """
            INSERT INTO networks (bssid, ssid, first_seen, last_seen, times_seen)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(bssid) DO UPDATE SET
                ssid = COALESCE(NULLIF(excluded.ssid, ''), networks.ssid),
                last_seen = excluded.last_seen,
                times_seen = networks.times_seen + 1
            """,
            (r.bssid, r.ssid, observed_at, observed_at),
        )
        conn.execute(
            """
            INSERT INTO observations
                (session_id, bssid, ssid, channel, frequency, signal,
                 security, observed_at, backend)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                r.bssid,
                r.ssid,
                r.channel,
                r.frequency,
                r.signal,
                r.security,
                observed_at,
                r.backend,
            ),
        )
        stored += 1
    conn.commit()
    return {"stored": stored, "new_networks": new_networks}


def start_session(conn: sqlite3.Connection, label: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (started_at, label) VALUES (?, ?)",
        (now_epoch(), label),
    )
    conn.commit()
    return cur.lastrowid


def end_session(conn: sqlite3.Connection, session_id: int, scans: int) -> None:
    conn.execute(
        "UPDATE sessions SET ended_at = ?, scans = ? WHERE id = ?",
        (now_epoch(), scans, session_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_networks(
    conn: sqlite3.Connection,
    sort: str = "last",
    filter_term: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Known networks with first/last seen, counts and average signal."""
    sort_cols = {
        "first": "n.first_seen",
        "last": "n.last_seen",
        "seen": "n.times_seen",
        "signal": "avg_signal",
    }
    order = sort_cols.get(sort, "n.last_seen") + (
        " ASC" if sort == "first" else " DESC"
    )
    sql = f"""
        SELECT n.bssid, n.ssid, n.first_seen, n.last_seen, n.times_seen,
               ROUND(AVG(o.signal), 1) AS avg_signal,
               MAX(o.signal) AS best_signal,
               MAX(o.channel) AS last_channel
        FROM networks n
        LEFT JOIN observations o ON o.bssid = n.bssid
        WHERE (? IS NULL OR n.ssid LIKE '%' || ? || '%'
               OR n.bssid LIKE '%' || ? || '%')
        GROUP BY n.bssid
        ORDER BY {order}
        LIMIT ?
    """
    return conn.execute(sql, (filter_term, filter_term, filter_term, limit)).fetchall()


def observations(
    conn: sqlite3.Connection,
    bssid: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if bssid:
        where.append("(o.bssid = ? OR o.ssid = ?)")
        params += [bssid, bssid]
    if since is not None:
        where.append("o.observed_at >= ?")
        params.append(since)
    if until is not None:
        where.append("o.observed_at <= ?")
        params.append(until)
    sql = f"""
        SELECT o.observed_at, o.bssid, o.ssid, o.channel, o.frequency,
               o.signal, o.security, o.backend
        FROM observations o
        WHERE {' AND '.join(where)}
        ORDER BY o.observed_at DESC
        LIMIT ?
    """
    return conn.execute(sql, (*params, limit)).fetchall()


def channel_counts(
    conn: sqlite3.Connection,
    since: int | None = None,
    until: int | None = None,
) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if since is not None:
        where.append("observed_at >= ?")
        params.append(since)
    if until is not None:
        where.append("observed_at <= ?")
        params.append(until)
    sql = f"""
        SELECT channel, COUNT(*) AS n, ROUND(AVG(signal), 1) AS avg_signal
        FROM observations
        WHERE {' AND '.join(where)}
        GROUP BY channel
        ORDER BY n DESC, channel
    """
    return conn.execute(sql, params).fetchall()


def network_events(
    conn: sqlite3.Connection,
    since: int | None = None,
    until: int | None = None,
) -> list[sqlite3.Row]:
    """Appearance/disappearance events computed from consecutive scans."""
    where, params = ["1=1"], []
    if since is not None:
        where.append("observed_at >= ?")
        params.append(since)
    if until is not None:
        where.append("observed_at <= ?")
        params.append(until)
    sql = f"""
        WITH ordered AS (
            SELECT o.bssid, o.ssid, o.observed_at,
                   LAG(o.bssid) OVER (
                       PARTITION BY o.bssid ORDER BY o.observed_at
                   ) IS NULL AS is_first,
                   LEAD(o.bssid) OVER (
                       PARTITION BY o.bssid ORDER BY o.observed_at
                   ) IS NULL AS is_last
            FROM observations o
            WHERE {' AND '.join(where)}
        )
        SELECT bssid, ssid, observed_at, is_first, is_last FROM ordered
        WHERE is_first OR is_last
    """
    return conn.execute(sql, params).fetchall()


def timeline_observations(
    conn: sqlite3.Connection,
    since: int | None = None,
    until: int | None = None,
    limit: int = 2000,
) -> list[sqlite3.Row]:
    """All observations in a window, ordered by time (for heatmap rendering)."""
    where, params = ["1=1"], []
    if since is not None:
        where.append("observed_at >= ?")
        params.append(since)
    if until is not None:
        where.append("observed_at <= ?")
        params.append(until)
    sql = f"""
        SELECT o.observed_at, o.bssid, o.ssid, o.signal
        FROM observations o
        WHERE {' AND '.join(where)}
        ORDER BY o.observed_at ASC
        LIMIT ?
    """
    return conn.execute(sql, (*params, limit)).fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    n_networks = conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
    n_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    first, last = conn.execute(
        "SELECT MIN(observed_at), MAX(observed_at) FROM observations"
    ).fetchone()
    return {
        "networks": n_networks,
        "observations": n_obs,
        "sessions": n_sessions,
        "first_observation": first,
        "last_observation": last,
    }
