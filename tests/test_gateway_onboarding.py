"""Helpers behind 'onboard a gateway and all its sub-devices by IP'."""

import logging
from unittest.mock import AsyncMock, Mock

from custom_components.localtuya import gateway_config_from_subdevice
from custom_components.localtuya.config_flow import nudge_subdevice_dps
from custom_components.localtuya.core import pytuya
from custom_components.localtuya.core.helpers import (
    discovered_from_gateway,
    find_discovered_by_ip,
    gateway_children,
    sleep_time_for_category,
)

GW = "bf00gateway000000001"
KEY = "gatewaylocalkey1"
CLOUD = {
    GW: {"id": GW, "name": "Living room hub", "local_key": KEY, "category": "wg2"},
    "bf01bulb": {"id": "bf01bulb", "name": "CCT bulb", "local_key": KEY, "node_id": "a4c1000000000001", "category": "dj"},
    "bf02switch": {"id": "bf02switch", "name": "Wall switch", "local_key": KEY, "node_id": "a4c1000000000002", "category": "kg"},
    "bf03ir": {"id": "bf03ir", "name": "TV remote", "local_key": KEY, "node_id": "ir01", "category": "infrared_tv"},
    "bf04other": {"id": "bf04other", "name": "Other hub's sensor", "local_key": "otherkey00000000", "node_id": "a4c1000000000009", "category": "wsdcg"},
    "bf05wifi": {"id": "bf05wifi", "name": "Wi-Fi plug", "local_key": "plugkey000000000", "category": "cz"},
    "bf06explicit": {"id": "bf06explicit", "name": "Explicit child", "local_key": "rotatedkey000000", "node_id": "a4c1000000000006", "category": "dj", "gateway_id": GW},
    "bf07foreign": {"id": "bf07foreign", "name": "Same key, other gw", "local_key": KEY, "node_id": "a4c1000000000007", "category": "dj", "gateway_id": "bf99othergw"},
}
DISCOVERED = {
    GW: {"ip": "192.168.1.50", "gwId": GW, "version": "3.3", "productKey": "keyabc"},
    "bf05wifi": {"ip": "192.168.1.51", "gwId": "bf05wifi", "version": "3.3"},
}


def test_find_discovered_by_ip():
    assert find_discovered_by_ip(DISCOVERED, "192.168.1.50")["gwId"] == GW
    assert find_discovered_by_ip(DISCOVERED, "192.168.1.99") is None
    assert find_discovered_by_ip(None, "192.168.1.50") is None


def test_gateway_children_by_key_and_explicit_gateway_id():
    assert set(gateway_children(GW, CLOUD)) == {"bf01bulb", "bf02switch", "bf06explicit"}
    assert gateway_children("nope", CLOUD) == {}


def test_children_inherit_the_gateway_address_and_version():
    disc = discovered_from_gateway("192.168.1.50", GW, "3.3", "keyabc", gateway_children(GW, CLOUD))
    bulb = disc["bf01bulb"]
    assert bulb["ip"] == "192.168.1.50"
    assert bulb["gwId"] == "bf01bulb"  # the child's own id, never the gateway's
    assert bulb["version"] == "3.3"
    assert bulb["node_id"] == "a4c1000000000001"
    assert bulb["gateway_id"] == GW


def test_gateway_connection_uses_the_broadcast_version():
    """Children are often listed as 3.4 while the hub speaks 3.3."""
    child = {"host": "192.168.1.50", "device_id": "bf01bulb", "protocol_version": "3.4", "node_id": "a4c1"}
    assert gateway_config_from_subdevice(child, DISCOVERED)["protocol_version"] == "3.3"
    assert gateway_config_from_subdevice(child, None)["protocol_version"] == "3.4"
    assert child["protocol_version"] == "3.4"  # the child's own config is untouched


def test_sleep_time_only_for_battery_categories():
    for category in ("pir", "mcs", "wsdcg", "wxkg"):
        assert sleep_time_for_category(category) > 0
    for category in ("dj", "kg", ""):
        assert sleep_time_for_category(category) == 0


def _logger():
    logger = pytuya.ContextualLogger()
    logger.set_logger(logging.getLogger(__name__), "bf00", False, "test")
    return logger


async def test_nudge_returns_what_the_gateway_pushes():
    iface = Mock()
    iface.update_dps = AsyncMock()
    iface.dps_cache = {"a4c1": {"20": True, "22": 500}}

    assert await nudge_subdevice_dps(iface, "a4c1", _logger(), wait=0) == {"20": True, "22": 500}
    assert iface.update_dps.await_args.kwargs["cid"] == "a4c1"


async def test_nudge_asks_again_when_nothing_was_pushed():
    iface = Mock()
    iface.update_dps = AsyncMock(side_effect=TimeoutError("no reply"))
    iface.dps_cache = {}
    iface.status = AsyncMock(return_value={"1": True})

    assert await nudge_subdevice_dps(iface, "a4c1", _logger(), wait=0) == {"1": True}
