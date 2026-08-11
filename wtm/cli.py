"""Command-line interface for the Wi-Fi Time Machine."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

from wtm import __version__
from wtm import db as dbmod
from wtm import export as exportmod
from wtm import scan as scanmod
from wtm import viz
from wtm.util import ago, fmt_time, now_epoch, parse_time

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_BACKEND = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wtm",
        description="Wi-Fi Time Machine — a historical time machine for the "
        "wireless environment around you.",
        epilog="example: wtm record --duration 1h   then: wtm timeline",
    )
    parser.add_argument("--version", action="version", version=f"wtm {__version__}")
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="SQLite database path (default: $XDG_DATA_HOME/wifi-time-machine/wtm.db, override with WTM_DB)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="one-shot scan of nearby networks")
    p.add_argument("--json", action="store_true", help="output raw JSON")
    p.add_argument("--store", action="store_true", help="also store the scan in the database")
    p.add_argument("--backend", choices=["iwd", "nmcli", "iwd-cache"], default=None)

    p = sub.add_parser("record", help="continuously record observations")
    p.add_argument("--interval", type=int, default=60, help="seconds between scans (default: 60, min 5)")
    p.add_argument("--duration", type=int, default=None, help="stop after N seconds")
    p.add_argument("--limit", type=int, default=None, help="stop after N scans")
    p.add_argument("--label", default=None, help="label for this recording session")
    p.add_argument("--backend", choices=["iwd", "nmcli", "iwd-cache"], default=None)

    p = sub.add_parser("networks", help="list known networks with first/last seen")
    p.add_argument("--sort", choices=["first", "last", "seen", "signal"], default="last")
    p.add_argument("--filter", default=None, help="substring match on SSID or BSSID")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("history", help="show historical observations")
    p.add_argument("--network", default=None, help="filter by BSSID or SSID")
    p.add_argument("--since", default=None, help="start time (e.g. 2026-08-11, 30m, 2h)")
    p.add_argument("--until", default=None, help="end time")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("timeline", help="visibility heatmap of networks over time")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--bucket", type=int, default=None, help="bucket size in seconds")
    p.add_argument("--top", type=int, default=None, help="only the N most-observed networks")
    p.add_argument("--width", type=int, default=None, help="heatmap width in columns")

    p = sub.add_parser("channels", help="channel activity chart")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--width", type=int, default=40)

    p = sub.add_parser("export", help="export observations (JSON or CSV)")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output", default="-", help="output file, or - for stdout")
    p.add_argument("--sanitize", action="store_true", help="hash BSSIDs (SHA-256 truncated)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--limit", type=int, default=100000)

    p = sub.add_parser("status", help="database and backend status")
    return parser


def _open_db(args) -> sqlite3.Connection:
    return dbmod.connect(args.db or dbmod.default_db_path())


def _table(headers: list[str], rows: list[list]) -> str:
    """Simple aligned text table."""
    widths = [
        max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
        for i, h in enumerate(headers)
    ]
    lines = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_scan(args) -> int:
    try:
        backend, results = scanmod.scan(args.backend)
    except scanmod.BackendUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2, ensure_ascii=False))
    else:
        rows = [
            [
                r.bssid,
                r.ssid or "(hidden)",
                r.channel if r.channel is not None else "-",
                r.frequency if r.frequency is not None else "-",
                f"{r.signal} dBm" if r.signal is not None else "-",
                r.security or "-",
            ]
            for r in results
        ]
        rows.sort(key=lambda r: -int(r[4].split()[0]) if r[4].split()[0].lstrip("-").isdigit() else -999)
        print(_table(["BSSID", "SSID", "CH", "FREQ MHz", "SIGNAL", "SECURITY"], rows))
        print(f"\n{len(results)} networks observed via {backend}")
    if args.store:
        conn = _open_db(args)
        counts = dbmod.record_scan(conn, results)
        print(f"stored {counts['stored']} observations ({counts['new_networks']} new networks)")
    return EXIT_OK


def cmd_record(args) -> int:
    interval = max(5, args.interval)
    try:
        backend, _ = scanmod.scan(args.backend)
    except scanmod.BackendUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    conn = _open_db(args)
    session_id = dbmod.start_session(conn, args.label)
    print(f"recording via {backend} into {conn.execute('PRAGMA database_list').fetchone()[2]}")
    print("press Ctrl+C to stop")
    start = now_epoch()
    scans = 0
    total_new = 0
    try:
        while True:
            t = now_epoch()
            _backend, results = scanmod.scan(args.backend)
            counts = dbmod.record_scan(conn, results, observed_at=t, session_id=session_id)
            scans += 1
            total_new += counts["new_networks"]
            print(
                f"{fmt_time(t)}  scan {scans}: {counts['stored']} networks "
                f"({counts['new_networks']} new)"
            )
            elapsed = now_epoch() - start
            if args.duration and elapsed >= args.duration:
                break
            if args.limit and scans >= args.limit:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        dbmod.end_session(conn, session_id, scans)
        duration = max(1, now_epoch() - start)
        print(
            f"session finished: {scans} scans over {duration}s, "
            f"{total_new} new networks, "
            f"{(scans * 60) // duration} scans/hour"
        )
    return EXIT_OK


def cmd_networks(args) -> int:
    conn = _open_db(args)
    rows = dbmod.list_networks(conn, sort=args.sort, filter_term=args.filter, limit=args.limit)
    if not rows:
        print("no networks recorded yet — run `wtm record` first")
        return EXIT_OK
    table_rows = [
        [
            r["ssid"] or "(hidden)",
            r["bssid"],
            ago(r["first_seen"]),
            ago(r["last_seen"]),
            r["times_seen"],
            r["avg_signal"] if r["avg_signal"] is not None else "-",
            r["last_channel"] if r["last_channel"] is not None else "-",
        ]
        for r in rows
    ]
    print(_table(["SSID", "BSSID", "FIRST", "LAST", "SEEN", "AVG dBm", "CH"], table_rows))
    return EXIT_OK


def cmd_history(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        rows = dbmod.observations(conn, bssid=args.network, since=since, until=until, limit=args.limit)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not rows:
        print("no observations match")
        return EXIT_OK
    table_rows = [
        [
            fmt_time(r["observed_at"]),
            r["bssid"],
            r["ssid"] or "(hidden)",
            r["channel"] if r["channel"] is not None else "-",
            f"{r['signal']} dBm" if r["signal"] is not None else "-",
            r["security"] or "-",
        ]
        for r in rows
    ]
    print(_table(["OBSERVED AT", "BSSID", "SSID", "CH", "SIGNAL", "SECURITY"], table_rows))
    if args.network and len(rows) > 2:
        signals = [r["signal"] for r in reversed(rows) if r["signal"] is not None]
        if len(signals) > 2:
            print(f"\nsignal over time: {viz.render_sparkline(signals)}  (min {min(signals)} / max {max(signals)} dBm)")
    return EXIT_OK


def cmd_timeline(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        obs = dbmod.timeline_observations(conn, since=since, until=until)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if args.top:
        counts: dict[str, int] = {}
        for ts, bssid, ssid, signal in obs:
            counts[bssid] = counts.get(bssid, 0) + 1
        keep = set(
            [b for b, _ in sorted(counts.items(), key=lambda kv: -kv[1])[: args.top]]
        )
        obs = [o for o in obs if o[1] in keep]
    print(viz.render_timeline(obs, bucket=args.bucket, width=args.width))
    return EXIT_OK


def cmd_channels(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        rows = dbmod.channel_counts(conn, since=since, until=until)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not rows:
        print("no observations match")
        return EXIT_OK
    items = [
        (f"ch {r['channel']}", r["n"], f" obs · avg {r['avg_signal']} dBm")
        for r in rows
        if r["channel"] is not None
    ]
    print(viz.render_bars(items, width=args.width))
    total = sum(r["n"] for r in rows)
    print(f"\n{total} observations across {len(rows)} channels")
    return EXIT_OK


def cmd_export(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        rows = dbmod.observations(conn, since=since, until=until, limit=args.limit)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if args.format == "json":
        data = exportmod.to_json(rows, sanitize=args.sanitize)
    else:
        data = exportmod.to_csv(rows, sanitize=args.sanitize)
    if args.output == "-":
        if not args.sanitize and rows:
            print(exportmod.SANITIZE_WARNING, file=sys.stderr)
        print(data)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(data)
        if not args.sanitize and rows:
            print(exportmod.SANITIZE_WARNING, file=sys.stderr)
        print(f"exported {len(rows)} observations to {args.output}")
    return EXIT_OK


def cmd_status(args) -> int:
    db_path = str(args.db or dbmod.default_db_path())
    try:
        backend = scanmod.detect_backend()
    except scanmod.BackendUnavailable as e:
        backend = f"unavailable ({e})"
    try:
        conn = dbmod.connect(db_path)
        st = dbmod.stats(conn)
        first = fmt_time(st["first_observation"]) if st["first_observation"] else "-"
        last = fmt_time(st["last_observation"]) if st["last_observation"] else "-"
    except Exception as e:  # pragma: no cover - defensive
        print(f"database error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"database : {db_path}")
    print(f"backend  : {backend}")
    print(f"networks : {st['networks']}")
    print(f"observations: {st['observations']}")
    print(f"sessions : {st['sessions']}")
    print(f"span     : {first} → {last}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        handlers = {
            "scan": cmd_scan,
            "record": cmd_record,
            "networks": cmd_networks,
            "history": cmd_history,
            "timeline": cmd_timeline,
            "channels": cmd_channels,
            "export": cmd_export,
            "status": cmd_status,
        }
        return handlers[args.command](args)
    except scanmod.BackendUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    except KeyboardInterrupt:
        return EXIT_OK
    except SystemExit as e:  # argparse (--help, bad subcommand)
        return int(e.code or 0)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
