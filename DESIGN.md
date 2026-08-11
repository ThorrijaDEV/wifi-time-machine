# Wi-Fi Time Machine — Design Document

## 1. Goal

A Linux-first CLI tool that records observations of the surrounding Wi-Fi
environment into a local SQLite database and lets the user explore how that
environment changes over time.

> Not "another Wi-Fi scanner". A **time machine**:
> "What did the wireless environment around me look like yesterday,
> an hour ago, or 10 minutes ago?"

## 2. Target environment

- Linux with a Wi-Fi interface in managed mode.
- Verified on this machine: Arch Linux, kernel 7.0, `iwd` (iNet Wireless
  Daemon) + `systemd-networkd`, interface `wlan0`, Realtek adapter.
- No NetworkManager is running on the primary target; NetworkManager is
  supported as an alternative backend for other machines.
- Must work **without root**.

## 3. Architecture

```
CLI (argparse) ──► scanner backends ──► SQLite ──► queries ──► visualizations
    │                  (probed)           │
    │                                     │
    └──────────► export (json/csv, optional sanitization)
```

Layers:

- `wtm.scan` — backend probing + scanning + parsing (pure functions).
- `wtm.db` — schema, recording, queries.
- `wtm.viz` — terminal visualizations (pure rendering).
- `wtm.export` — JSON/CSV export with optional BSSID hashing.
- `wtm.cli` — command wiring, error handling, UX.

Principles:

- Python 3.10+, **stdlib only** (zero runtime dependencies).
- Prefer existing Linux interfaces over reimplementing Wi-Fi scanning.
- Honest data labeling: measured vs inferred.

## 4. Scanning mechanism

Backends, probed in order at runtime; the first that works is used:

| Backend | Trigger | Data source | Requires |
|---|---|---|---|
| `iwd` (primary on target) | `iwctl station <iface> scan` | `iw dev <iface> scan dump` | `iwctl`, iwd running, D-Bus access (user in `wheel`/`network`) |
| `nmcli` (portable) | `nmcli dev wifi rescan` | `nmcli -t -f ... dev wifi list` | NetworkManager running |
| `iwd-cache` (fallback) | none (kernel cache) | `iw dev <iface> scan dump` | `iw` |

The `iwd` backend works unprivileged on the target machine (verified):
`iwctl` triggers a fresh scan via D-Bus, `iw dev scan dump` then returns the
complete BSS list with BSSID, SSID, frequency, channel, signal and security
IEs.

Interface auto-detection: first wireless interface from `iw dev` / `nmcli`.

## 5. SQLite schema

File: `~/.local/share/wifi-time-machine/wtm.db` (override: `WTM_DB`).
Timestamps: Unix epoch seconds (UTC). Schema version in `meta`.

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE networks (
    bssid       TEXT PRIMARY KEY,        -- normalized 'aa:bb:cc:dd:ee:ff'
    ssid        TEXT,
    first_seen  INTEGER NOT NULL,        -- epoch s
    last_seen   INTEGER NOT NULL,
    times_seen  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE observations (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    bssid       TEXT NOT NULL REFERENCES networks(bssid) ON DELETE CASCADE,
    ssid        TEXT,                    -- snapshot at observation time
    channel     INTEGER,
    frequency   INTEGER,                 -- MHz, as reported by firmware
    signal      INTEGER,                 -- dBm, measured by hardware
    security    TEXT,                    -- inferred from IEs / reported
    observed_at INTEGER NOT NULL,
    backend     TEXT                     -- which backend produced it
);
CREATE INDEX idx_obs_bssid ON observations(bssid, observed_at);
CREATE INDEX idx_obs_time  ON observations(observed_at);

CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    scans       INTEGER NOT NULL DEFAULT 0,
    label       TEXT
);
```

## 6. Observation model

- One `observations` row per BSS per scan.
- `networks` is a rolling summary: first/last seen, appearance count.
- `ssid` can legitimately be NULL/empty (hidden networks) — always stored.

Data honesty:

- **Measured:** signal strength (dBm), frequency, timestamps.
- **Reported by firmware:** channel, BSSID.
- **Inferred:** security type (parsed from RSN/WPA information elements),
  SSID (probe response), channel when not explicitly present (derived from
  frequency).
- RSSI is **never** converted to physical distance.

## 7. CLI structure

```
wtm scan [--json]               one-shot scan (does not store, unless --store)
wtm record [--interval S] [--duration S] [--label T] [--limit N]
                                continuous recording loop
wtm networks [--sort {first|last|seen|signal}] [--filter T] [--limit N]
                                known networks summary
wtm history [--network B] [--since T] [--until T] [--limit N]
                                raw observations
wtm timeline [--since T] [--until T] [--top N] [--bucket SEC]
                                signature visualization: visibility heatmap
wtm channels [--since T] [--until T]
                                channel activity bars
wtm export [--format {json,csv}] [--output F] [--sanitize] [--since T]
                                dataset export (sanitize hashes BSSIDs)
wtm status                      backend, db path, record counts
```

Time arguments (`--since`/`--until`): `2026-08-11`, `2026-08-11T10:30`,
`10:30`, or relative `30m`, `2h`, `1d`, `1w`.

Exit codes: 0 success, 1 usage/runtime error, 2 backend unavailable.

## 8. Visualization

- `timeline`: grid where each row is a network and each column a time bucket;
  cell glyph encodes signal level (`█▆▄▂·`). Appearances, disappearances and
  signal drift are visible at a glance. Terminal-friendly, no TUI library.
- `history --network`: per-observation signal sparkline with measured values.
- `channels`: horizontal bar chart of observation counts per channel.

## 9. Configuration

None required. Flags only. `WTM_DB` env override. First run creates
database and directory automatically.

## 10. Dependencies

Runtime: Python 3.10+ stdlib. System: one of {iwd + iwctl, NetworkManager +
nmcli, iw}. Dev: pytest.

## 11. Testing strategy

- Unit tests (no network needed): `iw scan dump` parsing against captured
  real output fixtures; `nmcli` parsing fixtures; frequency→channel mapping;
  DB schema/upsert/queries on temp databases; query time-argument parsing;
  export + sanitization (BSSID hashing); visualization renderers.
- Manual integration test on this machine: real `wtm scan`, `wtm record`
  over a few minutes, `timeline`, `channels`, `history`, `export`.
- Failure cases: missing backend, empty scan results, malformed/partial
  parser input, hidden SSIDs, permission errors.

## 12. Privacy

- Local storage only. No network access by the tool, no telemetry, no
  analytics, no external services.
- BSSIDs are machine identifiers: optional `--sanitize` on export hashes
  them (SHA-256, truncated); exports without sanitization print a warning.
- README documents what data is stored and how to delete it.

## 13. Limitations

- Observes what the local radio can hear; a single interface sees its own
  channel for live traffic, full sweeps happen per scan.
- Scan cache can be stale if the trigger backend fails silently.
- Hidden SSIDs appear as empty names.
- Signal is relative, not a distance estimate.
- Not a monitor-mode/passive capture tool.

## 14. Future possibilities

- Replay mode for recorded sessions (visualization of a past window).
- Multi-interface scans, station (client) observation.
- GPS/wardriving integration.
- Web dashboard serving the same SQLite database.
- Anomaly detection: "network X changed channel" event feed.
