"""Export formatting tests (JSON/CSV, sanitization)."""

import json

from wtm.export import observations_to_records, to_csv, to_json
from wtm.util import hash_bssid

ROW = {
    "observed_at": 1710000000,
    "bssid": "aa:bb:cc:dd:ee:01",
    "ssid": "HOME-NET",
    "channel": 36,
    "frequency": 5180,
    "signal": -42,
    "security": "wpa2-psk",
    "backend": "iw",
}


def test_observations_to_records_stringifies_none():
    row = dict(ROW)
    row["ssid"] = None
    row["signal"] = None
    records = observations_to_records([row])
    assert records[0]["ssid"] == ""
    assert records[0]["signal"] is None


def test_to_json_sanitize_hashes_bssid():
    raw = json.loads(to_json([ROW]))
    clean = json.loads(to_json([ROW], sanitize=True))
    assert raw[0]["bssid"] == "aa:bb:cc:dd:ee:01"
    clean_bssid = clean[0]["bssid"]
    assert clean_bssid == hash_bssid("aa:bb:cc:dd:ee:01")
    assert clean_bssid != "aa:bb:cc:dd:ee:01"
    assert raw[0]["ssid"] == clean[0]["ssid"]  # SSIDs are not hashed


def test_to_csv_field_order():
    csv_text = to_csv([ROW])
    lines = csv_text.splitlines()
    assert lines[0] == (
        "observed_at,bssid,ssid,channel,frequency,signal,security"
    )
    assert lines[1].endswith("-42,wpa2-psk")
    assert "HOME-NET" in lines[1]


def test_to_csv_sanitize():
    csv_text = to_csv([ROW], sanitize=True)
    assert "aa:bb:cc:dd:ee:01" not in csv_text
    assert hash_bssid("aa:bb:cc:dd:ee:01") in csv_text


def test_to_csv_empty():
    assert to_csv([]).splitlines() == [
        "observed_at,bssid,ssid,channel,frequency,signal,security"
    ]
