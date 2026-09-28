"""CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import core, net


def cmd_scan(args):
    found = net.listen_for_gateway(args.ip, args.seconds)
    if not found:
        print(f"no Tuya broadcast from {args.ip} in {args.seconds:.0f}s", file=sys.stderr)
        print("  - is the gateway on the same L2 network as this host?", file=sys.stderr)
        print("  - is Home Assistant (or tinytuya scan) already holding UDP 6667?", file=sys.stderr)
        return 2
    print(json.dumps({k: found.get(k) for k in ("ip", "gwId", "version", "productKey", "ability", "encrypt")}, indent=2))
    return 0


def _plan(args):
    found = None
    if not args.no_scan:
        found = net.listen_for_gateway(args.ip, args.seconds)
    gw_id = args.gateway_id or (found or {}).get("gwId")
    if not gw_id:
        raise SystemExit("gateway not broadcasting and --gateway-id not given")
    cloud = net.load_cloud(args.creds)
    devices = net.cloud_devices(cloud)
    plan = core.build_plan(args.ip, found, gw_id, devices)
    if args.version:
        plan.version = args.version
    return plan


def cmd_plan(args):
    plan = _plan(args)
    print(f"Gateway : {plan.gateway_name}  id={plan.gateway_id}  ip={plan.host}  protocol={plan.version}")
    print(f"Local key: {plan.local_key}")
    print(f"Sub-devices: {len(plan.children)}\n")
    rows = [{"name": c.name, "id": c.id, "node_id": c.node_id, "category": c.category, "kind": c.kind} for c in plan.children]
    print(core.format_table(rows, ["name", "id", "node_id", "category", "kind"]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(plan.to_dict(), fh, indent=2, ensure_ascii=False)
        print(f"\nplan written to {args.out}")
    return 0


def cmd_verify(args):
    with open(args.plan, encoding="utf-8") as fh:
        d = json.load(fh)
    plan = core.Plan(**{**d, "children": [core.Child(**c) for c in d["children"]]})
    ver, results = net.verify_children(plan, versions=args.versions, timeout=args.timeout)
    if ver is None:
        print("handshake with the gateway failed on every protocol version.", file=sys.stderr)
        print("  - wrong local key? (re-paired gateway rotates it)", file=sys.stderr)
        print("  - gateway already serving its max local clients? stop HA/localtuya while verifying", file=sys.stderr)
        print("  - gateway offline from the cloud can refuse LAN clients on some firmwares", file=sys.stderr)
    else:
        print(f"gateway answered on protocol {ver}" + (f" (broadcast said {plan.version})" if ver != plan.version else ""))
        plan.version = ver
    rows = []
    for child, v, dps, note in results:
        child.verified = dps is not None
        child.dps = dps or {}
        child.note = note
        hint = core.cct_light_hint(dps) if dps else None
        rows.append({"name": child.name, "node_id": child.node_id, "ok": "yes" if dps is not None else "no",
                     "dps": ",".join(sorted(child.dps, key=int)) if dps else "-",
                     "light?": "CCT/RGB" if hint and "color_temp" in hint else ("dimmer" if hint else ""),
                     "note": note[:60]})
    print(core.format_table(rows, ["name", "node_id", "ok", "dps", "light?", "note"]))
    print("\nchildren that answered with no DPs: press the device once (or power-cycle the gateway) and re-run.")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(plan.to_dict(), fh, indent=2, ensure_ascii=False)
        print(f"plan updated: {args.out}")
    return 0 if ver else 1


def cmd_export(args):
    with open(args.plan, encoding="utf-8") as fh:
        d = json.load(fh)
    plan = core.Plan(**{**d, "children": [core.Child(**c) for c in d["children"]]})
    rows = core.localtuya_rows(plan)
    if args.format == "table":
        print(core.format_table(rows, ["Device Name", "Device ID", "Sub-devices Node Id", "Protocol Version", "category"]))
        print(f"\nIP Address = {plan.host}   Local Key = {plan.local_key}   (same for every row; never add the gateway itself)")
    else:
        out = {"gateway": {"id": plan.gateway_id, "host": plan.host, "local_key": plan.local_key, "protocol_version": plan.version},
               "devices": []}
        for c in plan.children:
            item = {"friendly_name": c.name, "host": plan.host, "device_id": c.id, "local_key": plan.local_key,
                    "node_id": c.node_id, "protocol_version": plan.version, "category": c.category}
            if c.dps and (hint := core.cct_light_hint(c.dps)):
                item["light_entity"] = hint
            out["devices"].append(item)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="tuya-gw-onboard",
        description="From a Tuya gateway's LAN IP to a LocalTuya-ready list of its Zigbee/BLE sub-devices.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="identify the gateway from its UDP broadcast")
    s.add_argument("ip"); s.add_argument("--seconds", type=float, default=15)
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("plan", help="scan + cloud: list sub-devices, local key, write plan.json")
    s.add_argument("ip"); s.add_argument("--creds", required=True, help="tinytuya.json-style cloud credentials")
    s.add_argument("--gateway-id", help="skip/override UDP discovery")
    s.add_argument("--no-scan", action="store_true"); s.add_argument("--seconds", type=float, default=15)
    s.add_argument("--version", choices=net.SUPPORTED_VERSIONS, help="force protocol version")
    s.add_argument("--out", default="plan.json")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("verify", help="talk to the gateway locally and query every child by cid")
    s.add_argument("plan"); s.add_argument("--versions", nargs="*", choices=net.SUPPORTED_VERSIONS)
    s.add_argument("--timeout", type=float, default=6); s.add_argument("--out")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("export", help="print what to enter in LocalTuya (table) or JSON")
    s.add_argument("plan"); s.add_argument("--format", choices=("table", "json"), default="table")
    s.set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
