"""Dataset export with optional privacy sanitization."""

from __future__ import annotations

import csv
import io
import json

from wtm.util import hash_bssid, normalize_bssid

SANITIZE_WARNING = (
    "warning: exporting raw BSSIDs; consider --sanitize to hash them"
)


def observations_to_records(rows) -> list[dict]:
    records = []
    for row in rows:
        records.append(
            {
                "observed_at": row["observed_at"],
                "bssid": row["bssid"],
                "ssid": row["ssid"] or "",
                "channel": row["channel"],
                "frequency": row["frequency"],
                "signal": row["signal"],
                "security": row["security"] or "",
            }
        )
    return records


def to_json(rows, sanitize: bool = False) -> str:
    records = observations_to_records(rows)
    if sanitize:
        for rec in records:
            rec["bssid"] = hash_bssid(rec["bssid"])
    return json.dumps(records, indent=2, ensure_ascii=False)


def to_csv(rows, sanitize: bool = False) -> str:
    records = observations_to_records(rows)
    buf = io.StringIO()
    if not records:
        return "observed_at,bssid,ssid,channel,frequency,signal,security\n"
    writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
    writer.writeheader()
    for rec in records:
        if sanitize:
            rec["bssid"] = hash_bssid(normalize_bssid(rec["bssid"]))
        writer.writerow(rec)
    return buf.getvalue()
