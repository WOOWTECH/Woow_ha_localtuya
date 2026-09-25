"""Reconnect behaviour of the connection a whole hub depends on."""

from unittest.mock import AsyncMock
import errno

from . import *
from custom_components.localtuya import coordinator
from custom_components.localtuya.switch import LocalTuyaSwitch, DOMAIN as SWITCH

SWITCH_CONFIG = {
    DEVICE_NAME: {
        **DEVICE_CONFIG,
        "entities": [{"id": "1", "friendly_name": None, "platform": "switch", "icon": ""}],
    }
}
CHILD = {
    **DEVICE_CONFIG,
    "device_id": "bf00000000000000light01",
    "node_id": "a4c1380000000a01",
    "protocol_version": "3.5",
    "entities": [],
}


def test_hub_backoff_is_capped_low():
    hub = [coordinator.reconnect_delay(n, hub=True) for n in range(1, 8)]
    assert max(hub) <= coordinator.RECONNECT_HUB_MAX_INTERVAL.total_seconds()
    # A lone device still backs off to the full minute.
    assert coordinator.reconnect_delay(7) == coordinator.RECONNECT_MAX_INTERVAL.total_seconds()


async def test_hub_holder_retries_unreachable_within_the_cycle(monkeypatch):
    """Over Wi-Fi the first connect after a drop fails ARP; the next works."""
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    gateway = coordinator.TuyaDevice(dump.hass, dump._entry, CHILD, True)

    connect = AsyncMock(side_effect=OSError(errno.EHOSTUNREACH, "Host is unreachable"))
    monkeypatch.setattr(coordinator, "pytuya_connect", connect)

    async def no_wait(_):
        return None

    monkeypatch.setattr(coordinator.asyncio, "sleep", no_wait)

    await gateway._make_connection()

    assert gateway.holds_hub_connection
    assert connect.await_count == 3  # every retry of the cycle, not just one
