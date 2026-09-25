"""Sub-device kinds seen behind a Zemismart M1 hub that onboarding missed.

DP layouts below are copied from what the Tuya cloud returned for the real
devices (3-gang scene switch, IR blaster).
"""

import json
from unittest.mock import AsyncMock, Mock

from . import *
from custom_components.localtuya import coordinator
from custom_components.localtuya.core.ha_entities import gen_localtuya_entities
from custom_components.localtuya.core.helpers import (
    WRITE_ONLY_MARKER,
    manual_dps_for_category,
)
from custom_components.localtuya.switch import LocalTuyaSwitch, DOMAIN as SWITCH


def _cloud_dp(code, spec):
    return {"code": code, "values": json.dumps(spec)}


def _device(dps_strings, dps_data):
    return {
        "friendly_name": "child",
        "dps_strings": dps_strings,
        "device_cloud_data": {"dps_data": dps_data},
    }


NUMERIC_PRESS = {"type": "enum", "range": ["0", "1", "2"]}
SCENE_SWITCH = _device(
    [
        "1 ( code: switch1_value , value: , cloud pull )",
        "2 ( code: switch2_value , value: , cloud pull )",
        "3 ( code: switch3_value , value: , cloud pull )",
    ],
    {
        "1": _cloud_dp("switch1_value", NUMERIC_PRESS),
        "2": _cloud_dp("switch2_value", NUMERIC_PRESS),
        "3": _cloud_dp("switch3_value", NUMERIC_PRESS),
    },
)


def test_numeric_scene_buttons_get_an_entity_each():
    """0/1/2-encoded presses used to produce no entity at all."""
    entities = gen_localtuya_entities(SCENE_SWITCH, "wxkg")

    buttons = {e["id"]: e for e in entities if e["platform"] == "select"}
    assert set(buttons) == {"1", "2", "3"}
    assert buttons["1"]["friendly_name"] == "Button 1"
    assert buttons["1"]["select_options"] == {
        "0": "Single click",
        "1": "Double click",
        "2": "Long press",
    }


def test_scene_buttons_named_by_the_cloud_status_api():
    """What the M1 switches actually returned: switch_modeN status codes."""
    device = _device(
        [
            "1 ( code: switch_mode1 , value: 0 )",
            "2 ( code: switch_mode2 , value: 0, cloud pull )",
            "3 ( code: switch_mode3 , value: 1, cloud pull )",
        ],
        {
            "1": _cloud_dp("switch_mode1", NUMERIC_PRESS),
            "2": _cloud_dp("switch_mode2", NUMERIC_PRESS),
            "3": _cloud_dp("switch_mode3", NUMERIC_PRESS),
        },
    )

    entities = gen_localtuya_entities(device, "wxkg")

    buttons = {e["id"]: e["friendly_name"] for e in entities if e["platform"] == "select"}
    assert buttons == {"1": "Button 1", "2": "Button 2", "3": "Button 3"}


def test_click_named_scene_switch_keeps_its_original_entity():
    """Devices the existing template handled must not change."""
    named = {"type": "enum", "range": ["single_click", "double_click", "long_press"]}
    device = _device(
        ["1 ( code: switch1_value , value: single_click )"],
        {"1": _cloud_dp("switch1_value", named)},
    )

    entities = gen_localtuya_entities(device, "wxkg")

    selects = [e for e in entities if e["platform"] == "select"]
    assert len(selects) == 1
    assert selects[0]["friendly_name"] == "Switch 1"


def test_ir_blaster_is_marked_write_only_and_becomes_a_remote():
    assert manual_dps_for_category("wnykq") == WRITE_ONLY_MARKER
    assert manual_dps_for_category("dj") is None
    assert manual_dps_for_category("") is None

    ir = _device(
        [
            "201 ( code: ir_send , value: , cloud pull )",
            "202 ( code: ir_study_code , value: , cloud pull )",
        ],
        {
            "201": _cloud_dp("ir_send", {"type": "string"}),
            "202": _cloud_dp("ir_study_code", {"type": "raw"}),
        },
    )
    entities = gen_localtuya_entities(ir, "wnykq")

    remote = next(e for e in entities if e["platform"] == "remote")
    assert remote["id"] == "201"
    assert remote["receive_dp"] == "202"


SWITCH_CONFIG = {
    DEVICE_NAME: {
        **DEVICE_CONFIG,
        "entities": [{"id": "1", "friendly_name": None, "platform": "switch", "icon": ""}],
    }
}


async def test_write_only_child_is_never_queried():
    """A child that cannot answer must not hold the hub's request slot."""
    dump = await init(SWITCH_CONFIG, SWITCH, LocalTuyaSwitch)
    child_config = {
        **DEVICE_CONFIG,
        "device_id": "bf000000000000000irbl1",
        "node_id": "0000000000000c01",
        "manual_dps_strings": WRITE_ONLY_MARKER,
        "entities": [],
    }
    gateway = coordinator.TuyaDevice(dump.hass, dump._entry, child_config, True)
    child = coordinator.TuyaDevice(dump.hass, dump._entry, child_config)
    child.gateway = gateway
    # The harness has no running loop for dispatcher_send.
    child._dispatch_status = Mock()

    iface = Mock()
    iface.is_connected = True
    iface.version = 3.5
    iface.add_dps_to_request = Mock()
    iface.enable_debug = Mock()
    iface.keep_alive = Mock()
    iface.status = AsyncMock(side_effect=TimeoutError("never answers"))
    gateway._interface = iface

    await child._make_connection()

    iface.status.assert_not_awaited()
    assert child.connected
