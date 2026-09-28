"""Gateway / sub-device connection handling in the coordinator."""

from unittest.mock import AsyncMock, Mock

from . import *
from custom_components.localtuya import coordinator
from custom_components.localtuya.switch import LocalTuyaSwitch, DOMAIN as SWITCH

SWITCH_CONFIG = {
    DEVICE_NAME: {**DEVICE_CONFIG, "entities": [{"id": "1", "friendly_name": None, "platform": "switch", "icon": ""}]}
}
SUBDEVICE_CONFIG = {
    **DEVICE_CONFIG,
    "device_id": "bf1234567890abcdefgh",
    "protocol_version": "3.4",
    "node_id": "a4c138aabbccddee",
    "friendly_name": "Zigbee CCT bulb",
    "entities": [],
}


def _fake_interface(status=None):
    iface = Mock()
    iface.version = 3.4
    iface.is_connected = True
    iface.add_dps_to_request = Mock()
    iface.enable_debug = Mock()
    iface.keep_alive = Mock()
    iface.close = AsyncMock()
    iface.gateway_beat = AsyncMock()
    iface.status = status or AsyncMock(return_value={})
    return iface


def test_reconnect_delay_backs_off_and_caps():
    assert [coordinator.reconnect_delay(n) for n in range(1, 8)] == [5, 10, 20, 40, 60, 60, 60]
    assert coordinator.reconnect_delay(1, absent=True) == 10
    # Low-power devices keep the short interval so their wake window isn't missed.
    assert all(coordinator.reconnect_delay(n, low_power=True) == 5 for n in range(1, 10))


async def _gateway_device():
    """A fake gateway: the first sub-device of a hub, lending its config."""
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    return coordinator.TuyaDevice(dump.hass, dump._entry, SUBDEVICE_CONFIG, True)


async def test_fake_gateway_keeps_session_when_child_has_no_status(monkeypatch):
    """Every other sub-device depends on this session."""
    gateway = await _gateway_device()
    iface = _fake_interface(status=AsyncMock(side_effect=TimeoutError("no reply")))
    monkeypatch.setattr(coordinator, "pytuya_connect", AsyncMock(return_value=iface))

    await gateway._make_connection()

    assert gateway.connected
    iface.gateway_beat.assert_awaited_once_with(gateway._hub_caps)
    iface.close.assert_not_awaited()
    iface.keep_alive.assert_called_once_with(False, gateway._hub_caps)


async def test_fake_gateway_drops_session_on_key_failure(monkeypatch):
    gateway = await _gateway_device()

    async def refused(*_, **__):
        iface.is_connected = False
        raise ConnectionAbortedError("Session key negotiation failed on step 1")

    iface = _fake_interface(status=refused)
    iface.gateway_beat = AsyncMock(side_effect=refused)
    monkeypatch.setattr(coordinator, "pytuya_connect", AsyncMock(return_value=iface))

    await gateway._make_connection()

    assert not gateway.connected


async def test_connect_retries_are_paced(monkeypatch):
    """Immediate retries wait for the device to release the previous socket."""
    gateway = await _gateway_device()
    monkeypatch.setattr(coordinator, "pytuya_connect", AsyncMock(side_effect=Exception("boom")))
    sleeps = []

    async def record(delay):
        sleeps.append(delay)

    monkeypatch.setattr(coordinator.asyncio, "sleep", record)

    await gateway._make_connection()

    assert not gateway.connected
    assert sleeps[:2] == [coordinator.CONNECT_RETRY_DELAY, coordinator.CONNECT_RETRY_DELAY * 2]
