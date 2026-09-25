"""Failure modes seen while onboarding a live Zemismart M1 hub."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from . import *
from custom_components.localtuya import config_flow
from custom_components.localtuya.config_flow import wait_for_live_connection
from custom_components.localtuya.core.pytuya import EmptyListener, TuyaProtocol
from custom_components.localtuya.switch import LocalTuyaSwitch, DOMAIN as SWITCH

SWITCH_CONFIG = {
    DEVICE_NAME: {
        **DEVICE_CONFIG,
        "entities": [{"id": "1", "friendly_name": None, "platform": "switch", "icon": ""}],
    }
}


async def test_key_negotiation_does_not_swallow_cancellation():
    """A bare except here ate asyncio.timeout's CancelledError, so a 5s
    connect timeout fired and the caller carried on as if nothing happened."""
    proto = TuyaProtocol("bf00", "0123456789abcdef", 3.5, False, EmptyListener())
    proto.exchange_quick = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await proto._negotiate_session_key()


async def test_wait_for_live_connection():
    up = Mock(connected=True)
    assert await wait_for_live_connection(up, 5) is True

    down = Mock(connected=False)
    assert await wait_for_live_connection(down, 0) is False


async def _handler():
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    handler = config_flow.LocalTuyaOptionsFlowHandler(dump._entry)
    handler.hass = dump.hass
    handler.async_show_form = lambda **kw: {"type": "form", **kw}
    return handler


async def test_failed_onboarding_reports_why():
    """It used to leave no result: an empty 'unknown' and nothing logged."""
    handler = await _handler()
    handler._async_onboard_gateway_steps = AsyncMock(side_effect=RuntimeError("hub gone"))

    await handler._async_onboard_gateway("192.168.1.50", "bfgw", None)
    res = await handler.async_step_gateway_result()

    assert res["errors"] == {"base": "unknown"}
    assert "hub gone" in res["description_placeholders"]["ex"]


async def test_cancelled_onboarding_still_reports():
    handler = await _handler()
    handler._async_onboard_gateway_steps = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await handler._async_onboard_gateway("192.168.1.50", "bfgw", None)
    res = await handler.async_step_gateway_result()

    assert res["description_placeholders"]["ex"] == "cancelled"


async def test_device_without_dps_does_not_inherit_previous_entities(monkeypatch):
    """dev_entites was only assigned inside an if, so a device with no DP
    strings reused the entities built for the device before it."""
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    cloud = {
        "bfbulb": {"id": "bfbulb", "name": "Bulb", "local_key": "k" * 16, "category": "dj"},
        "bfmute": {"id": "bfmute", "name": "Mute", "local_key": "k" * 16, "category": "dj"},
    }
    discovered = {
        dev_id: {"ip": "192.168.1.50", "gwId": dev_id, "version": "3.3"} for dev_id in cloud
    }
    results = {
        "bfbulb": {"dps_strings": ["20 ( code: switch_led , value: True )"], "protocol_version": "3.3"},
        "bfmute": {"dps_strings": [], "protocol_version": "3.3"},
    }

    async def fake_validate(runtime, data):
        return results[data["device_id"]]

    monkeypatch.setattr(config_flow, "validate_input", fake_validate)
    # No other LocalTuya entries: nothing counts as already configured.
    dump.hass.config_entries = Mock(async_entries=lambda domain: [])

    devices, fails = await config_flow.setup_localtuya_devices(
        dump.hass, dump.hass.data["localtuya"][dump._entry.entry_id], discovered, cloud
    )

    assert "bfbulb" in devices
    assert "bfmute" not in devices
    assert "bfmute" in fails
