"""Pure logic: no network. Everything here is unit-testable.

Data shapes
-----------
discovered: {"ip": str, "gwId": str, "version": str, "productKey": str}
cloud device: tinytuya Cloud.getdevices() item -> {"id","name","key","category",
              "node_id"?, "gateway_id"?, "product_name"?, "sub"?}
plan: see build_plan().
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable

INFRARED_PREFIX = "infrared"
# Tuya "category" codes that LocalTuya's auto-configure knows how to build.
# Kept small on purpose: anything else is still listed, just flagged.
KNOWN_CATEGORIES = {
    "dj": "light",
    "dd": "light strip",
    "xdd": "ceiling light",
    "fwd": "ambient light",
    "tgq": "dimmer",
    "tgkg": "dimmer switch",
    "kg": "switch",
    "cz": "socket",
    "pc": "power strip",
    "cl": "curtain",
    "wsdcg": "temp/humidity sensor",
    "pir": "motion sensor",
    "mcs": "door sensor",
    "sj": "water leak sensor",
    "ywbj": "smoke sensor",
    "wkf": "thermostat valve",
    "wk": "thermostat",
    "ms": "lock",
    "sfkzq": "irrigation valve",
    "wxkg": "scene switch",
    "hps": "presence sensor",
}


@dataclass
class Child:
    id: str
    name: str
    node_id: str
    category: str
    product_name: str = ""
    kind: str = ""
    dps: dict = field(default_factory=dict)  # filled by verify
    verified: bool | None = None
    note: str = ""


@dataclass
class Plan:
    gateway_id: str
    host: str
    version: str
    local_key: str
    gateway_name: str
    product_key: str | None
    children: list[Child]

    def to_dict(self) -> dict:
        return asdict(self)


def find_by_ip(discovered: Iterable[dict], host: str) -> dict | None:
    for dev in discovered:
        if dev.get("ip") == host:
            return dev
    return None


def children_of(gateway_id: str, cloud_devices: list[dict]) -> list[dict]:
    """Sub-devices of `gateway_id`.

    Prefer the explicit `gateway_id` Tuya returns for some data centers; fall
    back to "has node_id and shares the gateway's local key". IR virtual
    remotes are dropped.
    """
    by_id = {d["id"]: d for d in cloud_devices if d.get("id")}
    gw = by_id.get(gateway_id)
    if not gw:
        return []
    gw_key = gw.get("key") or gw.get("local_key")
    out = []
    for d in cloud_devices:
        if d.get("id") == gateway_id or not d.get("node_id"):
            continue
        if str(d.get("category", "")).startswith(INFRARED_PREFIX):
            continue
        explicit = d.get("gateway_id")
        if explicit:
            if explicit == gateway_id:
                out.append(d)
        elif gw_key and (d.get("key") or d.get("local_key")) == gw_key:
            out.append(d)
    return out


def build_plan(host: str, discovered: dict | None, gateway_id: str,
               cloud_devices: list[dict]) -> Plan:
    by_id = {d["id"]: d for d in cloud_devices if d.get("id")}
    gw = by_id.get(gateway_id)
    if not gw:
        raise LookupError(
            f"gateway {gateway_id} is not in the cloud device list "
            "(wrong data center, OEM app not linked, or different account)"
        )
    version = str((discovered or {}).get("version") or "auto")
    kids = []
    for d in children_of(gateway_id, cloud_devices):
        cat = str(d.get("category", ""))
        kids.append(Child(
            id=d["id"],
            name=(d.get("name") or d["id"]).strip(),
            node_id=d["node_id"],
            category=cat,
            product_name=d.get("product_name", "") or "",
            kind=KNOWN_CATEGORIES.get(cat, "unknown category"),
        ))
    return Plan(
        gateway_id=gateway_id,
        host=host,
        version=version,
        local_key=gw.get("key") or gw.get("local_key") or "",
        gateway_name=(gw.get("name") or gateway_id).strip(),
        product_key=(discovered or {}).get("productKey"),
        children=kids,
    )


def localtuya_rows(plan: Plan) -> list[dict]:
    """Exactly what to type into LocalTuya's 'Configure device connectivity'."""
    return [
        {
            "Device Name": c.name,
            "IP Address": plan.host,
            "Device ID": c.id,
            "Local Key": plan.local_key,
            "Sub-devices Node Id": c.node_id,
            "Protocol Version": plan.version,
            "category": c.category,
        }
        for c in plan.children
    ]


def cct_light_hint(dps: dict) -> dict | None:
    """If the DPs look like a Tuya light, return the LocalTuya light entity fields.

    Two layouts exist in the wild:
      new dj: 20 switch, 21 work_mode, 22 bright_value, 23 temp_value, 24 colour
      old dj: 1 switch, 2 work_mode (str), 3 bright_value, 4 temp_value, 5 colour
    A DP whose value is a bool is never a brightness (bool is an int subclass).
    """

    def is_num(v):
        return isinstance(v, int) and not isinstance(v, bool)

    keys = set(dps)
    if {"20", "22"} <= keys and is_num(dps.get("22")):
        hint = {"id": "20", "brightness": "22", "brightness_lower": 10, "brightness_upper": 1000,
                "color_temp_min_kelvin": 2700, "color_temp_max_kelvin": 6500}
        if "21" in keys:
            hint["color_mode"] = "21"
        if "23" in keys:
            hint["color_temp"] = "23"
        if "24" in keys:
            hint["color"] = "24"
        return hint
    if {"1", "3"} <= keys and isinstance(dps.get("2"), str) and is_num(dps.get("3")):
        hint = {"id": "1", "color_mode": "2", "brightness": "3", "brightness_lower": 10, "brightness_upper": 1000,
                "color_temp_min_kelvin": 2700, "color_temp_max_kelvin": 6500}
        if "4" in keys and is_num(dps.get("4")):
            hint["color_temp"] = "4"
        if "5" in keys:
            hint["color"] = "5"
        return hint
    if {"1", "2"} <= keys and is_num(dps.get("2")):
        return {"id": "1", "brightness": "2", "brightness_lower": 10, "brightness_upper": 1000}
    return None


def format_table(rows: list[dict], columns: list[str]) -> str:
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    line = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    body = "\n".join(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows)
    return f"{line}\n{sep}\n{body}" if rows else "(none)"
