"""Which child's config holds a hub's shared connection.

On a Zemismart M1, a battery temperature sensor ended up lending its config
to the hub connection after a reload, and the connection dropped every one
to two minutes; a mains-powered switch holding it was stable.
"""

from custom_components.localtuya import device_setup_order, gateway_config_from_subdevice

HOST = "192.168.1.50"


def _child(name, **extra):
    return {"friendly_name": name, "host": HOST, "node_id": name, "protocol_version": "3.5", **extra}


def test_mains_powered_child_holds_the_connection():
    devices = {
        "sensor": _child("sensor", device_sleep_time=1800),
        "ir": _child("ir", manual_dps_strings="0"),
        "wifi_plug": {"friendly_name": "plug", "host": "192.168.1.10"},
        "light": _child("light"),
    }

    order = [dev_id for dev_id, _ in sorted(devices.items(), key=device_setup_order)]

    assert order[0] == "wifi_plug"  # parents still come first
    assert order[1] == "light"  # first sub-device of the hub = connection holder
    assert set(order[2:]) == {"sensor", "ir"}


def test_gateway_config_drops_the_donor_quirks():
    donor = _child("sensor", device_sleep_time=1800, manual_dps_strings="0,4")

    cfg = gateway_config_from_subdevice(donor, None)

    assert cfg["device_sleep_time"] == 0
    assert cfg["manual_dps_strings"] == "4"
    # The donor's own config is untouched.
    assert donor["device_sleep_time"] == 1800
    assert donor["manual_dps_strings"] == "0,4"
