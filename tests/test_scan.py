"""Parser and scanning-backend tests (no network required)."""

from pathlib import Path

import pytest

from wtm.scan import BackendUnavailable, parse_iw_dump, parse_nmcli
from wtm.util import freq_to_channel, hash_bssid, normalize_bssid

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# iw scan dump parsing — real captured output (identifiers masked)
# ---------------------------------------------------------------------------


def test_parse_real_dump_counts():
    results = parse_iw_dump(load("iw_dump_real.txt"))
    assert len(results) == 4


def test_parse_real_dump_fields():
    results = parse_iw_dump(load("iw_dump_real.txt"))
    by_bssid = {r.bssid: r for r in results}

    ap1 = by_bssid["aa:bb:cc:dd:ee:01"]
    assert ap1.ssid == "HOME-NET-5G"
    assert ap1.frequency == 5180
    assert ap1.channel == 36
    assert ap1.signal == -42
    assert ap1.security == "wpa2-psk"

    ap2 = by_bssid["aa:bb:cc:dd:ee:02"]
    assert ap2.ssid is None  # hidden network, no SSID element
    assert ap2.frequency == 5180
    assert ap2.channel == 36

    ap3 = by_bssid["aa:bb:cc:dd:ee:03"]
    assert ap3.ssid == "PUBLIC-NODO-1"
    assert ap3.frequency == 5600
    assert ap3.channel == 120  # derived from frequency (no DS param)

    ap4 = by_bssid["aa:bb:cc:dd:ee:04"]
    assert ap4.ssid == "HOME-NET-EXT"
    assert ap4.channel == 11
    assert ap4.signal == -75
    assert ap4.security == "wpa2-psk"


# ---------------------------------------------------------------------------
# iw scan dump parsing — synthetic variety
# ---------------------------------------------------------------------------


def test_parse_variety_security_and_channels():
    results = parse_iw_dump(load("iw_dump_variety.txt"))
    by_bssid = {r.bssid: r for r in results}

    assert by_bssid["11:22:33:44:55:01"].security == "open"
    assert by_bssid["11:22:33:44:55:01"].channel == 6

    assert by_bssid["11:22:33:44:55:02"].security == "wpa3-sae"
    assert by_bssid["11:22:33:44:55:02"].channel == 44

    assert by_bssid["11:22:33:44:55:03"].security == "wpa"  # legacy WPA v1
    assert by_bssid["11:22:33:44:55:03"].channel == 11

    assert by_bssid["11:22:33:44:55:04"].security == "wpa2/wpa3"  # SAE + PSK

    assert by_bssid["11:22:33:44:55:05"].security == "owe"
    assert by_bssid["11:22:33:44:55:05"].channel == 38  # 6 GHz derived

    hidden = by_bssid["11:22:33:44:55:06"]
    assert hidden.ssid is None
    assert hidden.security == "open"


def test_parse_iw_dump_garbage_and_empty():
    assert parse_iw_dump("") == []
    assert parse_iw_dump("not a dump at all\njust text") == []
    assert parse_iw_dump("BSS not-an-address(on wlan0)\nfreq: junk") == []


def test_parse_iw_dump_partial_bss():
    # A BSS block missing signal/freq must not crash and keeps valid fields.
    text = """BSS aa:bb:cc:dd:ee:99(on wlan0)
	SSID: PARTIAL
	DS Parameter set: channel 7
"""
    results = parse_iw_dump(text)
    assert len(results) == 1
    assert results[0].ssid == "PARTIAL"
    assert results[0].channel == 7
    assert results[0].frequency is None
    assert results[0].signal is None
    assert results[0].security == "open"


# ---------------------------------------------------------------------------
# nmcli parsing
# ---------------------------------------------------------------------------


def test_parse_nmcli():
    # Real `nmcli -e yes -t` output: BSSID and SSID colons are backslash-escaped.
    text = (
        "CoffeeShop:11\\:22\\:33\\:44\\:55\\:01:6:2437:72:WPA2\n"
        ":11\\:22\\:33\\:44\\:55\\:02:1:2412:45:WPA2\n"
        "OpenNet:11\\:22\\:33\\:44\\:55\\:03:11:2462:80:\n"
        "Cafe\\:Bar:11\\:22\\:33\\:44\\:55\\:04:36:5180:88:WPA3\n"
    )
    results = parse_nmcli(text)
    assert len(results) == 4
    assert results[0].ssid == "CoffeeShop"
    assert results[0].signal == 72
    assert results[0].security == "wpa2"
    assert results[1].ssid is None  # empty SSID -> hidden
    assert results[2].security is None
    # escaped colons inside the SSID survive unescaped
    assert results[3].ssid == "Cafe:Bar"
    assert results[3].bssid == "11:22:33:44:55:04"
    assert results[3].security == "wpa3"
    assert parse_nmcli("") == []
    assert parse_nmcli("shortline") == []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "freq,channel",
    [
        (2412, 1),
        (2437, 6),
        (2462, 11),
        (2484, 14),
        (5180, 36),
        (5220, 44),
        (5600, 120),
        (6115, 38),
        (100, None),
    ],
)
def test_freq_to_channel(freq, channel):
    assert freq_to_channel(freq) == channel


def test_normalize_bssid():
    assert normalize_bssid("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(ValueError):
        normalize_bssid("not-a-bssid")
    with pytest.raises(ValueError):
        normalize_bssid("aa:bb:cc:dd:ee")


def test_hash_bssid():
    h1 = hash_bssid("aa:bb:cc:dd:ee:ff")
    h2 = hash_bssid("AA:BB:CC:DD:EE:FF")
    h3 = hash_bssid("aa:bb:cc:dd:ee:00")
    assert h1 == h2  # normalization before hashing
    assert len(h1) == 12
    assert h1 != h3  # different inputs, different hashes
    assert h1 != "aa:bb:cc:dd:ee:ff"  # non-reversible


def test_backend_unavailable_is_runtime_error():
    with pytest.raises(RuntimeError):
        raise BackendUnavailable("no backend")
