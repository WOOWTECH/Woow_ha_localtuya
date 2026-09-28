"""Gateways on protocol 3.3 and 3.4, not just 3.5.

Behaviour pinned here, with the evidence it comes from:
- A LIDL/Silvercrest 3.3 hub ignores the sub-device query instead of closing
  the connection (tinytuya #583); some 3.5 hubs ignore it too (tinytuya #470).
- A TCP connect succeeds whatever the protocol version, so it proves nothing.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.localtuya import config_flow
from custom_components.localtuya.config_flow import (
    GatewaySession,
    verify_gateway_protocol,
)
from custom_components.localtuya.core import pytuya
from custom_components.localtuya.core.pytuya import EmptyListener, TuyaProtocol
from custom_components.localtuya.core.pytuya.cipher import AESCipher

# The shared test harness replaces asyncio.get_running_loop once a test calls
# init(); keep the real one for the keep-alive task.
_REAL_GET_RUNNING_LOOP = asyncio.get_running_loop

LOCAL_KEY = "0123456789abcdef"


def _protocol(version):
    proto = TuyaProtocol("bf00gateway", LOCAL_KEY, version, False, EmptyListener())
    proto.transport = Mock()
    proto.transport.is_closing.return_value = False
    return proto


# --- keep-alive ----------------------------------------------------------


async def test_answering_gateway_keeps_the_sub_device_query():
    proto = _protocol(3.4)
    proto.subdevices_query = AsyncMock(return_value={"reqType": "report"})
    proto.heartbeat = AsyncMock()
    caps = {}

    await proto.gateway_beat(caps)

    assert caps["subdev_query"] is True
    proto.heartbeat.assert_not_awaited()


async def test_silent_gateway_falls_back_to_a_plain_heartbeat():
    """Probe twice, keep the session alive each time, then stop asking."""
    proto = _protocol(3.3)
    proto.subdevices_query = AsyncMock(side_effect=TimeoutError("no reply"))
    proto.heartbeat = AsyncMock()
    caps = {}

    await proto.gateway_beat(caps)
    assert "subdev_query" not in caps  # one miss may just be a busy hub
    await proto.gateway_beat(caps)
    assert caps["subdev_query"] is False

    await proto.gateway_beat(caps)
    assert proto.subdevices_query.await_count == 2  # not probed again
    assert proto.heartbeat.await_count == 3  # every beat kept the session


async def test_a_miss_counts_once_the_gateway_is_known_to_answer():
    proto = _protocol(3.5)
    proto.subdevices_query = AsyncMock(side_effect=TimeoutError("no reply"))
    proto.heartbeat = AsyncMock()

    with pytest.raises(TimeoutError):
        await proto.gateway_beat({"subdev_query": True})
    proto.heartbeat.assert_not_awaited()


async def test_keep_alive_does_not_drop_a_gateway_that_ignores_the_query(
    monkeypatch,
):
    """It used to beat with the query alone and disconnect after two misses."""
    monkeypatch.setattr(pytuya, "HEARTBEAT_INTERVAL", 0.01)
    proto = _protocol(3.4)
    proto.loop = _REAL_GET_RUNNING_LOOP()
    proto.subdevices_query = AsyncMock(side_effect=TimeoutError("no reply"))
    proto.heartbeat = AsyncMock()
    proto.clean_up_session = Mock()
    caps = {}

    proto.keep_alive(True, caps)
    await asyncio.sleep(0.2)

    assert proto.heartbeater is not None and not proto.heartbeater.done()
    proto.clean_up_session.assert_not_called()
    assert caps["subdev_query"] is False
    proto.heartbeater.cancel()


# --- 3.3 query mode --------------------------------------------------------


def _data_unvalid_reply():
    return AESCipher(LOCAL_KEY.encode()).encrypt(
        b"json obj data unvalid", use_base64=False
    )


async def test_data_unvalid_on_a_gateway_does_not_switch_the_shared_connection():
    proto = _protocol(3.3)
    proto._serves_subdevices = True

    assert proto._decode_payload(_data_unvalid_reply()) is None
    assert proto.dev_type == "type_0a"


async def test_data_unvalid_on_a_plain_device_still_switches_to_type_0d():
    proto = _protocol(3.3)

    assert proto._decode_payload(_data_unvalid_reply()) is None
    assert proto.dev_type == "type_0d"


async def test_a_cid_request_marks_the_connection_as_a_gateway_connection():
    proto = _protocol(3.3)
    proto.dispatcher.wait_future = AsyncMock(side_effect=TimeoutError("no reply"))

    with pytest.raises(TimeoutError):
        await proto.exchange(pytuya.CMDType.DP_QUERY, nodeID="a4c138000000")
    assert proto._serves_subdevices


# --- protocol detection when the gateway does not broadcast ----------------


class _FakeGatewayConnection:
    """A connection to a hub that speaks `device_version` only."""

    def __init__(self, version: float, device_version: float):
        self.version = version
        self._matches = version == device_version
        self.real_local_key = LOCAL_KEY.encode()
        self.local_key = self.real_local_key
        self.is_connected = True

    async def subdevices_query(self):
        if not self._matches:
            self.is_connected = False  # session-key negotiation refused
            return None
        self.local_key = b"negotiated-key.."
        raise TimeoutError("this hub ignores the query itself")

    async def heartbeat(self):
        if not self._matches:
            raise TimeoutError("frame discarded")

    async def close(self):
        self.is_connected = False


def _connect_to(device_version, tried):
    async def connect(host, gw_id, local_key, version, enable_debug):
        tried.append(version)
        return _FakeGatewayConnection(version, device_version)

    return connect


@pytest.mark.parametrize("device_version", [3.3, 3.4, 3.5])
async def test_gateway_version_is_detected_by_handshake(monkeypatch, device_version):
    """A 3.4 hub used to be taken for 3.3: the 3.3 attempt was tried first and
    accepted on a bare TCP connect."""
    tried = []
    monkeypatch.setattr(config_flow.pytuya, "connect", _connect_to(device_version, tried))
    runtime = SimpleNamespace(devices={})

    session = await GatewaySession(runtime, "192.168.1.50").open(
        "bf00gateway", LOCAL_KEY, "auto"
    )

    assert session.connected
    assert session._interface.version == device_version
    assert runtime.devices["192.168.1.50"] is session
    await session.close()
    assert "192.168.1.50" not in runtime.devices


async def test_verify_rejects_a_3_3_attempt_on_a_newer_hub():
    connection = _FakeGatewayConnection(3.3, device_version=3.5)
    assert not await verify_gateway_protocol(connection, "3.3")


async def test_verify_accepts_a_3_4_hub_that_ignores_the_query():
    connection = _FakeGatewayConnection(3.4, device_version=3.4)
    assert await verify_gateway_protocol(connection, "3.4")
