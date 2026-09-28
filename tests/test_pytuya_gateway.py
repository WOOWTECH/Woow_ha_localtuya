"""Gateway-sharing behaviour of the Tuya LAN protocol layer.

Every case here reproduces something observed on a real Zemismart M1 hub with
55 Zigbee children, where the integration cycled connect/disconnect forever.
"""

import asyncio

import pytest

from custom_components.localtuya.core.pytuya import (
    MessageDispatcher,
    split_subdev_report,
)


def _dispatcher():
    return MessageDispatcher("devid", lambda *a, **k: None, 3.5, b"0123456789abcdef")


async def test_timeout_does_not_disturb_other_waiters():
    """A slow child must not knock every other sub-device off the hub."""
    d = _dispatcher()
    other = d.register(42, 10)

    with pytest.raises(TimeoutError):
        await d.wait_for(7, 10, timeout=0.05)

    assert not other.done(), "an unrelated listener was collateral damage"
    assert 42 in d.listeners
    assert 7 not in d.listeners


async def test_abort_fails_waiters_instead_of_cancelling_them():
    """CancelledError would stop the keep-alive loop, which reads it as 'my task
    is being shut down' and closes the session."""
    d = _dispatcher()
    future = d.register(MessageDispatcher.SUB_DEVICE_QUERY_SEQNO, 64)

    d.abort("session closed")

    with pytest.raises(TimeoutError):
        await future
    assert not future.cancelled()
    assert d.listeners == {}


KNOWN = {f"c{i}" for i in range(6)}


def test_partial_report_proves_nothing_absent():
    """The M1 answers with ~15 of its 55 children per reply."""
    absent, cycle = split_subdev_report({"c0", "c1"}, KNOWN, set())
    assert absent == set()
    absent, cycle = split_subdev_report({"c2", "c3"}, KNOWN, cycle)
    assert absent == set()
    assert cycle == {"c0", "c1", "c2", "c3"}


def test_absent_only_after_a_full_cycle():
    cycle = set()
    for chunk in ({"c0", "c1"}, {"c2", "c3"}, {"c4"}):
        absent, cycle = split_subdev_report(chunk, KNOWN, cycle)
        assert absent == set()

    # A repeated cid means the hub started over: c5 was never mentioned.
    absent, cycle = split_subdev_report({"c0", "c1"}, KNOWN, cycle)
    assert absent == {"c5"}
    assert cycle == {"c0", "c1"}


def test_single_reply_hub_still_detects_absence():
    reply = {"c0", "c1", "c2", "c3", "c4"}
    absent, cycle = split_subdev_report(reply, KNOWN, set())
    assert absent == set()
    absent, cycle = split_subdev_report(reply, KNOWN, cycle)
    assert absent == {"c5"}
