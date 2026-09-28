# tuya-gw-onboard

From **one LAN IP** of a Tuya Zigbee/BLE gateway (paired in Smart Life or in your own OEM app) to a
verified, LocalTuya-ready list of every sub-device behind it.

```
pip install .
tuya-gw-onboard scan   192.168.1.50                              # who is this? (gwId, protocol version)
tuya-gw-onboard plan   192.168.1.50 --creds tinytuya.json        # cloud: local key + sub-devices -> plan.json
tuya-gw-onboard verify plan.json --out plan.json                 # local: one connection, query each child by cid
tuya-gw-onboard export plan.json                                 # what to type into LocalTuya
```

`tinytuya.json` is the same credentials file `python -m tinytuya wizard` writes:
`{"apiKey": "...", "apiSecret": "...", "apiRegion": "eu"}`. For an OEM app, the cloud project must have the app
attached via **Devices → Link My App** (Automatic Link); then every customer's gateway is listed.

What it checks that the HA UI cannot tell you:
- the protocol version the gateway actually speaks (children must use the gateway's version, not the one the developer platform lists for the child);
- which children answer a `cid` status query right now, and with which DPs (a child that never reported since the gateway booted answers empty);
- whether the DP layout is a standard CCT / RGBCW bulb (20/21/22/23[/24]) and what the LocalTuya light entity fields should be.

Run `verify` with Home Assistant's LocalTuya **stopped or not yet configured for this gateway**: most gateways accept only 1–3 local clients.

The Home Assistant side is the `Onboard a gateway and all its sub-devices (by IP)` action of the
LocalTuya integration in this repository (`custom_components/localtuya`): it does the same
scan → cloud → auto-configure inside the config flow.

Run the tests from this directory: `PYTHONPATH=. python -m pytest -q tests` (needs `tinytuya`).
