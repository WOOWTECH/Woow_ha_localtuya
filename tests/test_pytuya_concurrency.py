"""Requests sharing one hub connection must not block each other.

On a Zemismart M1, a sleeping Zigbee child took ~20s to answer a status
query. While the request lock was held for the whole wait, the heartbeat
could not go out and the hub closed the session every one to two minutes.
"""

import asyncio
from unittest.mock import Mock

from custom_components.localtuya.core import pytuya
from custom_components.localtuya.core.pytuya import (
    CMDType,
    EmptyListener,
    MessageDispatcher,
    TuyaMessage,
    TuyaProtocol,
)


def _connected_protocol():
    proto = TuyaProtocol("bf00", "0123456789abcdef", 3.3, False, EmptyListener())
    proto.transport = Mock()
    proto.transport.is_closing.return_value = False
    return proto


def _heartbeat_reply():
    return TuyaMessage(0, CMDType.HEART_BEAT, 0, b"", 0, True, 0x55AA, None)


async def test_heartbeat_goes_out_while_a_child_query_is_pending(monkeypatch):
    monkeypatch.setattr(pytuya, "TIMEOUT_REPLY_SUBDEVICE", 5)
    proto = _connected_protocol()

    slow_child = asyncio.ensure_future(proto.status(cid="a4c1380000000b01"))
    await asyncio.sleep(0.1)
    heartbeat = asyncio.ensure_future(proto.heartbeat())
    await asyncio.sleep(0.2)

    # Both requests are on the wire although the child hasn't answered.
    assert proto.transport.write.call_count == 2
    proto.dispatcher._dispatch(_heartbeat_reply())
    await asyncio.wait_for(heartbeat, 1)
    assert not slow_child.done()

    slow_child.cancel()


async def test_overlapping_fixed_key_requests_share_one_future():
    d = MessageDispatcher("bf00", lambda *a, **k: None, 3.5, b"0123456789abcdef")
    first = d.register(MessageDispatcher.SUB_DEVICE_QUERY_SEQNO, 64)
    second = d.register(MessageDispatcher.SUB_DEVICE_QUERY_SEQNO, 64)
    assert first is second


async def test_one_waiter_timing_out_does_not_cancel_a_shared_future():
    d = MessageDispatcher("bf00", lambda *a, **k: None, 3.5, b"0123456789abcdef")
    key = MessageDispatcher.SUB_DEVICE_QUERY_SEQNO
    shared = d.register(key, 64)
    impatient = asyncio.ensure_future(d.wait_future(shared, key, 64, timeout=0.05))
    patient = asyncio.ensure_future(d.wait_future(d.register(key, 64), key, 64, timeout=2))

    await asyncio.sleep(0.1)
    assert impatient.done() and isinstance(impatient.exception(), TimeoutError)
    assert not shared.cancelled()

    shared.set_result("report")
    assert await patient == "report"
