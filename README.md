# Wi-Fi Time Machine

A historical time machine for the wireless environment around you.

`wtm` periodically scans the Wi-Fi networks visible from your machine, stores
every observation in a local SQLite database, and lets you travel back through
the record: when did a network first appear? How strong was it over the last
two hours? Which channels are busiest right now?

```
$ wtm record --duration 1h
$ wtm timeline
```

## Why does this exist?

Your Wi-Fi environment is dynamic. Routers reboot, neighbours change channels,
new access points appear, others vanish. Most tools only show a snapshot.
`wtm` treats the RF environment like a place with a history — because it is
one.

This project is an independent reimplementation of the "Wi-Fi history
recorder" concept, designed for simple, honest, Unix-style operation:

- **Local-only.** All data lives in one SQLite file. No cloud, no account, no
  telemetry.
- **Measurements, not magic.** Signal strengths are measured by your hardware.
  The tool never pretends that RSSI equals physical distance.
- **Two kinds of data, clearly labelled.** The database records *measured*
  data (signal, frequency) and *inferred* data (channel from frequency,
  coarse security label from the RSN/WPA information elements). See
  [DESIGN.md](DESIGN.md) for the full distinction.

## Features

- **`scan`** — one-shot scan of nearby networks (JSON or table output)
- **`record`** — continuous recording loop with sessions, labels, duration and
  scan limits
- **`timeline`** — a signal heatmap: rows are networks, columns are time
  buckets, glyph density encodes signal strength
- **`history`** — query observations with time windows and per-network
  sparklines
- **`channels`** — which channels are active and how busy they are
- **`networks`** — the census: first/last seen, how often seen, average signal
- **`export`** — JSON or CSV with an optional `--sanitize` mode that hashes
  BSSIDs (SHA-256, truncated, non-reversible) for safe sharing
- **`status`** — database and backend state
- **Backend auto-detection** — works with `iwd`, `nmcli`, or plain `iw`
  (via the kernel scan cache), so it runs on most Linux desktops without
  root privileges

## Installation

```sh
git clone https://github.com/ThorrijaDEV/wifi-time-machine
cd wifi-time-machine
pip install --user .
# or: pipx install .
```

Requires Python ≥ 3.10 on Linux. No runtime dependencies.

### Backends (one of)

| Backend | How it works | Needs |
| ------- | ------------ | ----- |
| `iwd` (default) | `iwctl station scan` triggers a scan, results read from the kernel scan cache via `iw dev <iface> scan dump` | `iwd` + `iw`, no root |
| `nmcli` | `nmcli dev wifi rescan` + device list | NetworkManager |
| `iwd-cache` | reads the kernel scan cache without triggering a scan | `iw`, useful when triggers fail |

The first working backend wins; override with `--backend`.

## Quick start

```sh
# What is around me right now?
wtm scan

# Record a session: one scan every 60 seconds, for one hour
wtm record --duration 1h

# Or with a label, for a targeted experiment
wtm record --interval 30 --limit 200 --label "desk position test"

# What changed?
wtm networks --sort first
wtm history --since 30m --network HOME-NET
wtm timeline --since 2h
wtm channels --since 1d

# Share an anonymized dataset
wtm export --sanitize --format csv --output observations.csv
```

## The timeline

```
$ wtm timeline --since 2h --bucket 300
                      15:00    15:30    16:00
       HOME-NET-5G    ████▆█▄█▅▆█▅█▆▇█▅▇███▆▅
   HOME-NET-EXT       ██████████████████████
   NEIGHBOR-AP-1      ▄▅▄▆▅▄▆▅▄▅▆▄▆▅▄▅▆▅▄▅▄▆
   98:d3:cf:3b:21:3e  ▂▄▂▄▅▂▄▅▂▄▅▄▂▄▅▂▄▅▄▂▄▅
buckets: 300s · glyphs: █≥-50 ▆≥-60 ▄≥-70 ▂≥-80 ·weaker · blank = not observed
```

A blank cell means the network was not observed during that bucket — exactly
the disappearance you want to notice.

## Data and privacy

Everything lives in `~/.local/share/wifi-time-machine/wtm.db`
(or `$XDG_DATA_HOME`, override with `WTM_DB` or `--db`).

- The database stores **BSSIDs** (MAC addresses of access points) and **SSIDs**
  of networks you can see. This is local-only, but treat the file like the
  location log it is.
- `wtm export --sanitize` hashes BSSIDs before writing data you intend to
  share. The hash is non-reversible (truncated SHA-256) but not secret — do
  not rely on it for strong anonymity.
- `iw` must run as root or with appropriate capabilities to *trigger* scans;
  this tool prefers unprivileged paths (`iwctl`, `nmcli`).

## Architecture

```
wtm/
├── cli.py     argparse CLI, command handlers
├── scan.py    backends (iwd / nmcli / iw scan cache) + pure parsers
├── db.py      SQLite schema, recording, queries
├── viz.py     terminal rendering (timeline heatmap, sparklines, bars)
├── export.py  JSON/CSV export with sanitization
└── util.py    time parsing, BSSID helpers, channel mapping
```

The parser functions are pure (text in, `ScanResult` out) and tested against
captured `iw` and `nmcli` output fixtures. No framework, no web UI, no daemon
required — just a CLI and a database file.

See [DESIGN.md](DESIGN.md) for the full design rationale, data model, and
limitations.

## Inspirations and attribution

This project was inspired by the idea of treating the local Wi-Fi environment
as an entity with a history. It is an independent implementation: all code is
original, the parsers were validated against real captured output, and no
code was copied from any existing project. If you know of a similar tool worth
acknowledging, please open an issue.

## Testing

```sh
python -m pytest
```

The test suite covers the parsers (against real captured output fixtures),
the database layer (schema, upsert behaviour, queries), all rendering
functions, export sanitization, and the CLI with mocked backends. 62 tests.

## Limitations

- **RSSI ≠ distance.** Signal strength is affected by antenna orientation,
  body position, walls and interference. `wtm` never reports positions.
- Scan results come from the OS networking stack and are only as fresh as the
  last scan; `iwd-cache` may return stale data.
- Some networks hide their SSID; those appear with a `(hidden)` label.
- The coarse security label is inferred from RSN/WPA information elements and
  may be wrong for mixed-mode or exotic configurations.

## License

MIT — see [LICENSE](LICENSE).
