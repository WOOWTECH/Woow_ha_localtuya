"""Drive the 'onboard gateway by IP' options step with a stub cloud + discovery."""

import asyncio
from unittest.mock import AsyncMock

from . import *
from custom_components.localtuya import config_flow
from custom_components.localtuya.const import DATA_DISCOVERY, DOMAIN
from custom_components.localtuya.switch import LocalTuyaSwitch, DOMAIN as SWITCH

GW = "bf00gateway000000001"
KEY = "gatewaylocalkey1"
CLOUD = {
    GW: {"id": GW, "name": "Hub", "local_key": KEY, "category": "wg2"},
    "bf01bulb": {"id": "bf01bulb", "name": "CCT bulb", "local_key": KEY, "node_id": "a4c101", "category": "dj"},
    "bf02sw": {"id": "bf02sw", "name": "Switch", "local_key": KEY, "node_id": "a4c102", "category": "kg"},
}
SWITCH_CONFIG = {
    DEVICE_NAME: {**DEVICE_CONFIG, "entities": [{"id": "1", "friendly_name": None, "platform": "switch", "icon": ""}]}
}


class _Discovery:
    def __init__(self, devices):
        self.devices = devices


async def _handler(monkeypatch, *, no_cloud=False, discovered=None):
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    hass, entry = dump.hass, dump._entry
    data = dict(entry.data)
    data["no_cloud"] = no_cloud
    object.__setattr__(entry, "data", data)
    if discovered is not None:
        hass.data[DOMAIN][DATA_DISCOVERY] = _Discovery(discovered)

    cloud = hass.data[DOMAIN][entry.entry_id].cloud_data
    cloud.device_list = dict(CLOUD)
    cloud.async_get_devices_list = AsyncMock(return_value="ok")
    cloud.async_get_device_functions = AsyncMock(return_value={})

    # The harness stubs asyncio.create_task out, so give hass a real one.
    hass.async_create_task = lambda coro, *a, **k: asyncio.ensure_future(coro)
    # No hub on the test network: fail the shared session fast.
    monkeypatch.setattr(config_flow.pytuya, "connect", AsyncMock(side_effect=OSError(113, "unreachable")))
    monkeypatch.setattr(config_flow, "GATEWAY_CONNECT_RETRY_DELAY", 0)

    handler = config_flow.LocalTuyaOptionsFlowHandler(entry)
    handler.hass = hass
    monkeypatch.setattr(type(handler), "config_entry", property(lambda self: entry))
    handler.async_show_form = lambda **kw: {"type": "form", **kw}
    handler.async_show_progress = lambda **kw: {"type": "progress", **kw}
    handler.async_show_progress_done = lambda **kw: {"type": "progress_done", **kw}
    return handler, cloud


async def _onboard(handler, host, gw_id=""):
    """Run the step, its background half, then the outcome step."""
    started = await handler.async_step_add_gateway({"host": host, "gwId": gw_id})
    if started.get("type") != "progress":
        return started
    assert started["progress_action"] == "onboard_gateway"
    await handler._gateway_task
    return await handler.async_step_gateway_result()


async def test_add_gateway_requires_cloud(monkeypatch):
    handler, _ = await _handler(monkeypatch, no_cloud=True)
    res = await handler.async_step_add_gateway({"host": "192.168.1.50", "gwId": ""})
    assert res["errors"]["base"] == "cloud_required"


async def test_add_gateway_not_broadcasting(monkeypatch):
    handler, _ = await _handler(monkeypatch, discovered={})
    res = await handler.async_step_add_gateway({"host": "192.168.1.50", "gwId": ""})
    assert res["errors"]["base"] == "gateway_not_discovered"


async def test_add_gateway_manual_id_not_in_cloud(monkeypatch):
    handler, _ = await _handler(monkeypatch, discovered={})
    res = await _onboard(handler, "192.168.1.50", "bfunknown")
    assert res["errors"]["base"] == "gateway_not_in_cloud"
    assert res["description_placeholders"]["gw_id"] == "bfunknown"


async def test_add_gateway_success_uses_gateway_version_and_children(monkeypatch):
    discovered = {GW: {"ip": "192.168.1.50", "gwId": GW, "version": "3.3", "productKey": "pk"}}
    handler, cloud = await _handler(monkeypatch, discovered=discovered)

    captured = {}

    async def fake_setup(hass, localtuya_data, discovered_devices, cloud_devs, log_fails=False):
        captured["discovered"] = discovered_devices
        devices = {
            dev_id: {"friendly_name": cloud_devs[dev_id]["name"], "node_id": d["node_id"], "entities": [{}]}
            for dev_id, d in discovered_devices.items()
        }
        return devices, {}

    monkeypatch.setattr(config_flow, "setup_localtuya_devices", fake_setup)
    confirmed = {}

    async def fake_confirm(msg, confirm_callback=None):
        confirmed["msg"] = msg
        confirmed["cb"] = confirm_callback
        return {"type": "confirm"}

    handler.async_step_confirm = fake_confirm

    assert await _onboard(handler, "192.168.1.50") == {"type": "confirm"}

    disc = captured["discovered"]
    assert set(disc) == {"bf01bulb", "bf02sw"}
    for child in disc.values():
        assert child["ip"] == "192.168.1.50"
        assert child["version"] == "3.3"  # the gateway's broadcast version
        assert child["gateway_id"] == GW
    # DP specs were fetched only for the children, not the whole account.
    fetched = {c.args[0] for c in cloud.async_get_device_functions.await_args_list}
    assert fetched == {"bf01bulb", "bf02sw"}
    assert "Succeeded sub-devices: ``2``" in confirmed["msg"]
    assert "CCT bulb (node a4c101)" in confirmed["msg"]


async def test_add_gateway_defers_the_slow_half_to_a_progress_step(monkeypatch):
    """A hub with many children takes minutes; the flow must not block on it."""
    discovered = {GW: {"ip": "192.168.1.50", "gwId": GW, "version": "3.3"}}
    handler, _ = await _handler(monkeypatch, discovered=discovered)
    ran = []

    async def slow(host, gw_id, found):
        ran.append(host)

    handler._async_onboard_gateway = slow

    res = await handler.async_step_add_gateway({"host": "192.168.1.50", "gwId": ""})
    assert res["type"] == "progress"
    assert res["progress_task"] is handler._gateway_task
    await handler._gateway_task
    assert ran == ["192.168.1.50"]
    assert await handler.async_step_add_gateway() == {"type": "progress_done", "next_step_id": "gateway_result"}
