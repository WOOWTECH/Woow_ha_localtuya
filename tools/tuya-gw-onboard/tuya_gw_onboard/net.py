"""Network side: UDP discovery, Tuya Cloud, local verification. Thin wrappers
around tinytuya so core.py stays testable."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Iterable

import tinytuya

UDP_PORTS = (6666, 6667, 7000)
SUPPORTED_VERSIONS = ("3.3", "3.4", "3.5")


def listen_for_gateway(host: str, seconds: float = 15.0) -> dict | None:
    """Listen for the Tuya UDP broadcast coming from `host` only.

    Uses tinytuya's decrypt for 6667/7000 (both are AES with the well-known
    UDP key). Returns the decoded broadcast dict or None.
    """
    socks = []
    for port in UDP_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
        except OSError:
            s.close()
            continue
        s.settimeout(0.5)
        socks.append(s)
    if not socks:
        raise OSError("could not bind UDP 6666/6667/7000; is Home Assistant or another scanner on this host?")

    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            for s in socks:
                try:
                    data, addr = s.recvfrom(4048)
                except socket.timeout:
                    continue
                if addr[0] != host:
                    continue
                try:
                    payload = tinytuya.decrypt_udp(data)
                    result = json.loads(payload)
                except Exception:  # noqa: BLE001 - unknown broadcast format
                    continue
                if result.get("gwId"):
                    return result
    finally:
        for s in socks:
            s.close()
    return None


def load_cloud(creds_path: str) -> tinytuya.Cloud:
    """Credentials file: same layout as tinytuya's tinytuya.json
    {"apiKey":..., "apiSecret":..., "apiRegion": "eu|us|cn|in|...", "apiDeviceID": optional}."""
    with open(creds_path, encoding="utf-8") as fh:
        c = json.load(fh)
    cloud = tinytuya.Cloud(
        apiRegion=c.get("apiRegion", "eu"),
        apiKey=c["apiKey"],
        apiSecret=c["apiSecret"],
        apiDeviceID=c.get("apiDeviceID", ""),
    )
    if cloud.error:
        raise RuntimeError(f"cloud login failed: {cloud.error}")
    return cloud


def cloud_devices(cloud: tinytuya.Cloud) -> list[dict]:
    devs = cloud.getdevices()
    if isinstance(devs, dict) and devs.get("Error"):
        raise RuntimeError(f"cloud device list failed: {devs}")
    return devs


def verify_children(plan, versions: Iterable[str] | None = None, timeout: float = 6.0):
    """Open ONE connection to the gateway and query each child by cid.

    Yields (child, version_used, dps_or_None, note). Tries the broadcast
    version first, then the others: the child must use the gateway's
    version, which is not always what the developer platform lists.
    """
    order = [v for v in ([plan.version] if plan.version != "auto" else []) + list(SUPPORTED_VERSIONS)]
    seen = set()
    order = [v for v in order if not (v in seen or seen.add(v))]
    if versions:
        order = [v for v in order if v in set(versions)]

    for ver in order:
        gw = tinytuya.Device(plan.gateway_id, plan.host, plan.local_key, version=float(ver), persist=True)
        gw.set_socketTimeout(timeout)
        handshake_ok = False
        results = []
        for child in plan.children:
            dev = tinytuya.Device(child.id, cid=child.node_id, parent=gw)
            st = dev.status()
            if isinstance(st, dict) and "dps" in st:
                handshake_ok = True
                results.append((child, ver, st["dps"], ""))
            else:
                err = (st or {}).get("Error") or (st or {}).get("Err") or str(st)
                results.append((child, ver, None, str(err)))
        # 3.4+ gateways also answer the sub-device online list; useful signal.
        if float(ver) >= 3.4 and not handshake_ok:
            try:
                sq = gw.subdev_query()
                if isinstance(sq, dict) and "data" in sq:
                    handshake_ok = True
            except Exception:  # noqa: BLE001
                pass
        gw.close()
        if handshake_ok:
            return ver, results
    return None, results
