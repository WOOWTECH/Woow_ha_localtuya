"""Replies from a hub that numbers its own frames.

A Zemismart M1 sends every frame under one running counter and does not echo
the request's sequence number; a request can only predict it. Frame order
captured on a real M1 at HA startup (53 children):

    -> DP_QUERY cid=A  seq 23587
    <- LAN_EXT_STREAM  seq 23587   sub-device report chunk, pushed
    <- DP_QUERY cid=A  seq 23588   A's reply
    -> DP_QUERY cid=B  seq 23588   (took A's reply, then B's own was dropped)

A got the report chunk as its status, B got A's reply, the next child timed
out, and the children that "connected" with an empty status stayed
unavailable until the integration was reloaded.
"""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.localtuya import entity
from custom_components.localtuya.core.pytuya import (
    CMDType,
    TuyaListener,
    TuyaMessage,
    TuyaProtocol,
)
from custom_components.localtuya.switch import LocalTuyaSwitch

from . import DEVICE_NAME, get_entites, init
from .test_switch import CONFIG as SWITCH_CONFIG, SWITCH_DOMAIN

# The shared test harness replaces asyncio.get_running_loop once a test calls
# init(); keep the real one.
_REAL_GET_RUNNING_LOOP = asyncio.get_running_loop

CID_A, CID_B = "a4c138dc127373a0", "84ba20fffe8db97f"


class _Hub(TuyaListener):
    def __init__(self, *cids):
        self.sub_devices = {cid: Mock() for cid in cids}

    def status_updated(self, status):
        pass

    def disconnected(self, exc=""):
        pass

    def subdevice_state_updated(self, state):
        pass


def _gateway(hub, seqno=23587):
    proto = TuyaProtocol("bf00gateway", "0123456789abcdef", 3.5, False, hub)
    proto.loop = _REAL_GET_RUNNING_LOOP()
    proto.transport = Mock()
    proto.transport.is_closing.return_value = False
    proto.local_key = b"negotiated-key.."  # session key already negotiated
    proto.seqno = seqno
    return proto


def _frame(seqno, cmd, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    return TuyaMessage(seqno, cmd, 0, body, 0, True, 0x6699, None)


def _reply(seqno, cid, dps):
    return _frame(seqno, CMDType.DP_QUERY_NEW, {"cid": cid, "dps": dps})


def _report_chunk(seqno, online):
    data = {"online": online, "offline": []}
    return _frame(
        seqno,
        CMDType.LAN_EXT_STREAM,
        {"reqType": "subdev_online_stat_report", "data": data},
    )


def _pending(proto):
    return sorted(s for s, f in proto.dispatcher.listeners.items() if not f.done())


async def _query(proto, cid):
    """Start a status query and return it once it is waiting for its reply."""
    before = _pending(proto)
    task = asyncio.ensure_future(proto.status(cid))
    for _ in range(100):
        if _pending(proto) != before:
            return task
        await asyncio.sleep(0.005)
    raise AssertionError(f"query for {cid} never registered")


async def test_pushed_report_does_not_take_the_reply_to_a_status_query():
    hub = _Hub(CID_A, CID_B)
    proto = _gateway(hub)
    query_a = await _query(proto, CID_A)
    assert _pending(proto) == [23587]

    proto.dispatcher._dispatch(_report_chunk(23587, [CID_A, CID_B]))
    await asyncio.sleep(0.01)
    assert not query_a.done(), "a pushed report chunk was taken as A's status"

    proto.dispatcher._dispatch(_reply(23588, CID_A, {"1": True}))
    assert await asyncio.wait_for(query_a, 1) == {"1": True}

    # The next request is sent under the number the hub will answer with.
    query_b = await _query(proto, CID_B)
    assert _pending(proto) == [23589]
    proto.dispatcher._dispatch(_reply(23589, CID_B, {"1": False}))
    assert await asyncio.wait_for(query_b, 1) == {"1": False}


async def test_a_neighbours_reply_goes_to_that_neighbour():
    hub = _Hub(CID_A, CID_B)
    proto = _gateway(hub)
    query_b = await _query(proto, CID_B)

    # A's reply arrives late, under the number B's query predicted.
    proto.dispatcher._dispatch(_reply(23587, CID_A, {"1": True}))
    await asyncio.sleep(0.01)

    assert not query_b.done(), "B took A's reply"
    hub.sub_devices[CID_A].status_updated.assert_called_once_with({"1": True})

    proto.dispatcher._dispatch(_reply(23588, CID_B, {"2": 500}))
    assert await asyncio.wait_for(query_b, 1) == {"2": 500}


async def test_a_pushed_report_for_the_child_answers_its_query():
    """Hubs that answer a query with a STATUS frame are served too."""
    hub = _Hub(CID_A)
    proto = _gateway(hub)
    proto.dps_cache[CID_A] = {"1": False, "2": 300}
    query_a = await _query(proto, CID_A)

    push = _frame(23590, CMDType.STATUS, {"cid": CID_A, "dps": {"1": True}})
    proto.dispatcher._dispatch(push)

    # Merged into what was known: the push carries only what changed.
    assert await asyncio.wait_for(query_a, 1) == {"1": True, "2": 300}
    hub.sub_devices[CID_A].status_updated.assert_called_once()


async def test_an_ack_does_not_end_a_status_query():
    proto = _gateway(_Hub(CID_A))
    query_a = await _query(proto, CID_A)

    proto.dispatcher._dispatch(_frame(23587, CMDType.CONTROL_NEW))
    await asyncio.sleep(0.01)

    assert not query_a.done()
    query_a.cancel()


async def test_a_reply_naming_no_child_still_ends_the_query_at_once():
    """An error reply must not leave the query waiting 25 seconds."""
    proto = _gateway(_Hub(CID_A))
    query_a = await _query(proto, CID_A)

    proto.dispatcher._dispatch(_frame(23587, CMDType.DP_QUERY_NEW, {"Err": "905"}))

    assert await asyncio.wait_for(query_a, 1) == {}
    assert _pending(proto) == []


async def test_a_plain_device_is_still_matched_by_sequence_number():
    proto = TuyaProtocol("bf00plain", "0123456789abcdef", 3.5, False, _Hub())
    proto.transport = Mock()
    proto.transport.is_closing.return_value = False
    proto.local_key = b"negotiated-key.."
    proto.seqno = 7

    task = asyncio.ensure_future(proto.status())
    for _ in range(100):
        if 7 in proto.dispatcher.listeners:
            break
        await asyncio.sleep(0.005)
    proto.dispatcher._dispatch(_frame(7, CMDType.DP_QUERY_NEW, {"dps": {"1": True}}))

    assert await asyncio.wait_for(task, 1) == {"1": True}


# --- entity side ------------------------------------------------------------


async def test_connected_child_with_no_status_yet_is_written_available(monkeypatch):
    """The entity was written unavailable before its device connected; the
    empty status it connected with was no change, so it was never rewritten."""
    device = await init(SWITCH_CONFIG, SWITCH_DOMAIN, LocalTuyaSwitch)
    switch = get_entites(device)[0]

    handlers = []
    monkeypatch.setattr(RestoreEntity, "async_added_to_hass", AsyncMock())
    monkeypatch.setattr(
        entity,
        "async_dispatcher_connect",
        lambda hass, signal, handler: handlers.append(handler) or (lambda: None),
    )
    monkeypatch.setattr(entity, "async_dispatcher_send", lambda *args: None)
    switch.async_get_last_state = AsyncMock(return_value=None)
    switch.async_on_remove = Mock()
    switch.schedule_update_ha_state = Mock()
    switch.entity_id = "switch.switch_1"
    switch.hass = Mock()
    switch.hass.states.get.return_value = State(switch.entity_id, STATE_UNAVAILABLE)
    await switch.async_added_to_hass()
    (update_handler,) = handlers

    monkeypatch.setattr(type(device), "connected", property(lambda self: True))
    update_handler({})

    switch.schedule_update_ha_state.assert_called_once()


async def test_unchanged_status_is_not_rewritten(monkeypatch):
    device = await init(SWITCH_CONFIG, SWITCH_DOMAIN, LocalTuyaSwitch)
    switch = get_entites(device)[0]

    handlers = []
    monkeypatch.setattr(RestoreEntity, "async_added_to_hass", AsyncMock())
    monkeypatch.setattr(
        entity,
        "async_dispatcher_connect",
        lambda hass, signal, handler: handlers.append(handler) or (lambda: None),
    )
    monkeypatch.setattr(entity, "async_dispatcher_send", lambda *args: None)
    switch.async_get_last_state = AsyncMock(return_value=None)
    switch.async_on_remove = Mock()
    switch.schedule_update_ha_state = Mock()
    switch.entity_id = "switch.switch_1"
    switch.hass = Mock()
    switch.hass.states.get.return_value = State(switch.entity_id, "on")
    await switch.async_added_to_hass()
    (update_handler,) = handlers

    update_handler({"1": True})
    update_handler({"1": True})

    assert switch.schedule_update_ha_state.call_count == 1
