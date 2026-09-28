from tuya_gw_onboard import core

GW = "bf00gateway"
KEY = "gatewaykey000001"
DEVS = [
    {"id": GW, "name": "Hub", "key": KEY, "category": "wg2"},
    {"id": "b1", "name": "CCT bulb ", "key": KEY, "node_id": "a4c101", "category": "dj", "product_name": "TS0502B"},
    {"id": "s1", "name": "Switch", "key": KEY, "node_id": "a4c102", "category": "kg"},
    {"id": "ir", "name": "TV", "key": KEY, "node_id": "ir1", "category": "infrared_tv"},
    {"id": "x1", "name": "Other hub sensor", "key": "otherkey00000000", "node_id": "a4c109", "category": "wsdcg"},
    {"id": "e1", "name": "Explicit", "key": "rotated000000000", "node_id": "a4c106", "category": "dj", "gateway_id": GW},
    {"id": "f1", "name": "Foreign", "key": KEY, "node_id": "a4c107", "category": "dj", "gateway_id": "bf99"},
    {"id": "w1", "name": "Wifi plug", "key": "plug000000000000", "category": "cz"},
]
DISC = {"ip": "10.0.0.5", "gwId": GW, "version": "3.4", "productKey": "pk"}


def test_children_of():
    assert [d["id"] for d in core.children_of(GW, DEVS)] == ["b1", "s1", "e1"]
    assert core.children_of("nope", DEVS) == []


def test_build_plan_and_rows():
    plan = core.build_plan("10.0.0.5", DISC, GW, DEVS)
    assert plan.version == "3.4" and plan.local_key == KEY and plan.gateway_name == "Hub"
    assert [c.name for c in plan.children] == ["CCT bulb", "Switch", "Explicit"]
    assert plan.children[0].kind == "light"
    row = core.localtuya_rows(plan)[0]
    assert row["Device ID"] == "b1" and row["Local Key"] == KEY and row["IP Address"] == "10.0.0.5"


def test_build_plan_without_broadcast_defaults_auto():
    plan = core.build_plan("10.0.0.5", None, GW, DEVS)
    assert plan.version == "auto" and plan.product_key is None


def test_build_plan_unknown_gateway():
    try:
        core.build_plan("10.0.0.5", DISC, "missing", DEVS)
    except LookupError as e:
        assert "missing" in str(e)
    else:
        raise AssertionError("expected LookupError")


def test_cct_light_hint_new_layout():
    h = core.cct_light_hint({"20": True, "21": "white", "22": 500, "23": 300})
    assert h["color_temp"] == "23" and h["color_mode"] == "21" and "color" not in h


def test_cct_light_hint_old_dj_layout():
    # Zemismart M1 field data: CCT downlights report DPs 1,2,3,4(,7)
    h = core.cct_light_hint({"1": True, "2": "white", "3": 800, "4": 500, "7": 0})
    assert h["id"] == "1" and h["color_mode"] == "2" and h["brightness"] == "3" and h["color_temp"] == "4"
    assert core.cct_light_hint({"1": True, "2": "colour", "3": 800, "4": 500, "5": "00ff00"})["color"] == "5"


def test_multi_gang_switch_is_not_a_dimmer():
    # kg switches report bool on DP 2/3; bool must never be read as brightness
    assert core.cct_light_hint({"1": True, "2": False, "3": True, "7": 0, "14": "off"}) is None
