"""Wi-Fi scanning backends and parsers.

Backends, probed in order (first that works wins):

- ``iwd``: trigger a fresh scan via ``iwctl`` (unprivileged when the user is
  in the ``wheel``/``network`` group), then read the full BSS list from the
  kernel scan cache with ``iw dev <iface> scan dump``.
- ``nmcli``: NetworkManager-based, ``nmcli dev wifi rescan`` + device list.
- ``iwd-cache``: read the kernel scan cache without triggering a new scan.

All parsers are pure functions and unit-tested against captured fixtures.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from wtm.util import freq_to_channel, normalize_bssid

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """One observed access point during a scan.

    ``signal`` and ``frequency`` are measured by the hardware; ``channel``
    and ``security`` may be inferred (see DESIGN.md, section 6).
    """

    bssid: str
    ssid: str | None
    channel: int | None
    frequency: int | None
    signal: int | None
    security: str | None
    backend: str = field(default="")

    def as_dict(self) -> dict:
        return {
            "bssid": self.bssid,
            "ssid": self.ssid or "",
            "channel": self.channel,
            "frequency": self.frequency,
            "signal": self.signal,
            "security": self.security or "",
        }


class BackendUnavailable(RuntimeError):
    """No working scanning backend could be found on this system."""


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: float = 10.0) -> str | None:
    """Run a command, returning stdout (stripped) or None on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def wireless_interfaces() -> list[str]:
    """Wireless interface names, best effort, via ``iw dev`` or ``nmcli``."""
    out = _run(["iw", "dev"])
    if out is not None:
        ifaces = re.findall(r"^\s*Interface\s+(\S+)", out, re.MULTILINE)
        if ifaces:
            return ifaces
    out = _run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    if out is not None:
        ifaces = [
            line.split(":")[0]
            for line in out.splitlines()
            if ":wifi" in line.lower()
        ]
        if ifaces:
            return ifaces
    return []


# ---------------------------------------------------------------------------
# Backend probing
# ---------------------------------------------------------------------------


def _iwd_usable() -> bool:
    if shutil.which("iwctl") is None or shutil.which("iw") is None:
        return False
    return _run(["iwctl", "station", "list"]) is not None


def _nmcli_usable() -> bool:
    if shutil.which("nmcli") is None:
        return False
    out = _run(["nmcli", "-t", "-f", "RUNNING", "general"])
    return out is not None and out.strip() == "running"


def detect_backend() -> str:
    """Return the name of the first working backend or raise."""
    if _iwd_usable():
        return "iwd"
    if _nmcli_usable():
        return "nmcli"
    if shutil.which("iw") is not None and wireless_interfaces():
        return "iwd-cache"
    raise BackendUnavailable(
        "no Wi-Fi scanning backend available "
        "(need iwd/iwctl, NetworkManager/nmcli, or iw)"
    )


# ---------------------------------------------------------------------------
# iwd backend: iwctl trigger + iw scan dump
# ---------------------------------------------------------------------------


def _iw_dump(iface: str) -> str | None:
    return _run(["iw", "dev", iface, "scan", "dump"], timeout=15.0)


def parse_iw_dump(text: str) -> list[ScanResult]:
    """Parse ``iw dev <iface> scan dump`` output into ScanResults."""
    results: list[ScanResult] = []
    blocks = re.split(r"^BSS ", text, flags=re.MULTILINE)
    for block in blocks[1:]:
        lines = block.splitlines()
        bssid = re.match(r"([0-9a-fA-F:]{17})", lines[0])
        if not bssid:
            continue
        freq = signal = channel = None
        ssid = None
        security = None
        in_rsn = in_wpa = False
        akm_text = ""
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("freq:"):
                try:
                    freq = int(float(stripped.split()[1]))
                except (IndexError, ValueError):
                    pass
            elif stripped.startswith("signal:"):
                try:
                    signal = int(float(stripped.split()[1]))
                except (IndexError, ValueError):
                    pass
            elif stripped.startswith("SSID:"):
                value = stripped[5:].strip()
                ssid = value or None
            elif "DS Parameter set: channel" in stripped:
                try:
                    channel = int(stripped.rsplit(None, 1)[1])
                except (IndexError, ValueError):
                    pass
            elif stripped.startswith("RSN:"):
                in_rsn, in_wpa = True, False
            elif stripped.startswith("WPA:"):
                in_rsn, in_wpa = False, True
            elif in_rsn and "Authentication suites" in stripped:
                akm_text = stripped.split(":", 1)[1].strip()
        if freq is not None and channel is None:
            channel = freq_to_channel(freq)
        if in_rsn or in_wpa:
            security = _infer_security(akm_text if in_rsn else "")
        else:
            security = "open"
        try:
            results.append(
                ScanResult(
                    bssid=normalize_bssid(bssid.group(1)),
                    ssid=ssid,
                    channel=channel,
                    frequency=freq,
                    signal=signal,
                    security=security,
                )
            )
        except ValueError:
            continue
    return results


def _infer_security(akm_text: str) -> str:
    """Map AKM suite names/numbers to a coarse security label (inferred)."""
    if akm_text:
        has_sae = "SAE" in akm_text or "00-0f-ac:8" in akm_text
        has_psk = "PSK" in akm_text or "00-0f-ac:2" in akm_text
        has_owe = "OWE" in akm_text or "00-0f-ac:18" in akm_text
        if has_sae:
            return "wpa3-sae" if not has_psk else "wpa2/wpa3"
        if has_psk:
            return "wpa2-psk"
        if has_owe:
            return "owe"
    return "wpa"  # legacy WPA v1 IE present


def _scan_iwd(iface: str) -> list[ScanResult]:
    _run(["iwctl", "station", iface, "scan"], timeout=10.0)
    dump = _iw_dump(iface)
    if dump is None:
        raise BackendUnavailable(f"failed to read scan cache for {iface}")
    results = parse_iw_dump(dump)
    for r in results:
        r.backend = "iwd"
    return results


# ---------------------------------------------------------------------------
# nmcli backend: NetworkManager
# ---------------------------------------------------------------------------


def parse_nmcli(text: str) -> list[ScanResult]:
    """Parse ``nmcli -e yes -t -f SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY dev wifi list``."""
    results: list[ScanResult] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = _split_escaped(line)
        if len(parts) < 6:
            continue
        ssid, bssid, chan, freq, signal, security = parts[:6]
        try:
            results.append(
                ScanResult(
                    bssid=normalize_bssid(bssid),
                    ssid=ssid or None,
                    channel=int(chan) if chan.isdigit() else None,
                    frequency=int(freq) if freq.isdigit() else None,
                    signal=int(signal) if signal.isdigit() else None,
                    security=security.lower() if security else None,
                )
            )
        except ValueError:
            continue
    return results


def _split_escaped(line: str) -> list[str]:
    """Split nmcli's escaped output: ``\\:`` and ``\\\\`` are literal."""
    parts: list[str] = []
    cur = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def _scan_nmcli() -> list[ScanResult]:
    _run(["nmcli", "dev", "wifi", "rescan"], timeout=15.0)
    out = _run(
        [
            "nmcli", "-e", "yes", "-t",
            "-f", "SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY",
            "dev", "wifi", "list",
        ],
        timeout=15.0,
    )
    if out is None:
        raise BackendUnavailable("nmcli wifi list failed")
    results = parse_nmcli(out)
    for r in results:
        r.backend = "nmcli"
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_backend_cache: dict[str, str] = {}


def scan(backend: str | None = None) -> tuple[str, list[ScanResult]]:
    """Perform a scan; returns ``(backend_name, results)``.

    ``backend=None`` auto-detects the best available backend.
    """
    if backend is None:
        backend = detect_backend()
    if backend == "iwd":
        ifaces = wireless_interfaces()
        if not ifaces:
            raise BackendUnavailable("no wireless interface found")
        return backend, _scan_iwd(ifaces[0])
    if backend == "nmcli":
        return backend, _scan_nmcli()
    if backend == "iwd-cache":
        ifaces = wireless_interfaces()
        if not ifaces:
            raise BackendUnavailable("no wireless interface found")
        dump = _iw_dump(ifaces[0])
        if dump is None:
            raise BackendUnavailable("failed to read scan cache")
        results = parse_iw_dump(dump)
        for r in results:
            r.backend = "iwd-cache"
        return backend, results
    raise BackendUnavailable(f"unknown backend: {backend!r}")
