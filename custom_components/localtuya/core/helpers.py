"""
Helpers functions for HASS-LocalTuya.
"""

import asyncio
import logging
import os.path
from enum import Enum
from fnmatch import fnmatch
from typing import NamedTuple

from homeassistant.util.yaml import load_yaml, dump
from homeassistant.const import CONF_PLATFORM, CONF_ENTITIES


import custom_components.localtuya.templates as templates_dir

JSON_TYPE = list | dict | str

_LOGGER = logging.getLogger(__name__)


###############################
#          Templates          #
###############################
class templates:

    def yaml_dump(config, fname: str | None = None) -> JSON_TYPE:
        """Save yaml config."""
        try:
            with open(fname, "w", encoding="utf-8") as conf_file:
                return conf_file.write(dump(config))
        except UnicodeDecodeError as exc:
            _LOGGER.error("Unable to save file %s: %s", fname, exc)

    def list_templates():
        """Return the available templates files."""
        dir = os.path.dirname(templates_dir.__file__)
        files = {}
        for e in sorted(os.scandir(dir), key=lambda e: e.name):
            file: str = e.name.lower()
            if e.is_file() and (fnmatch(file, "*yaml") or fnmatch(file, "*yml")):
                # fn = str(file).replace(".yaml", "").replace("_", " ")
                files[e.name] = e.name
        return files

    def import_config(filename):
        """Create a data that can be used as config in localtuya."""
        template_dir = os.path.dirname(templates_dir.__file__)
        template_file = os.path.join(template_dir, filename)
        _config = load_yaml(template_file)
        entities = []
        for cfg in _config:
            ent = {}
            for plat, values in cfg.items():
                for key, value in values.items():
                    ent[str(key)] = (
                        str(value)
                        if not isinstance(value, (bool, float, dict, list))
                        else value
                    )
                ent[CONF_PLATFORM] = plat
            entities.append(ent)
        if not entities:
            raise ValueError("No entities found the can be used for localtuya")
        return entities

    @classmethod
    def export_config(cls, config: dict, config_name: str):
        """Create a yaml config file for localtuya."""
        export_config = []
        for cfg in config[CONF_ENTITIES]:
            # Special case device_classes
            for k, v in cfg.items():
                if not type(v) is str and isinstance(v, Enum):
                    cfg[k] = v.value

            ents = {cfg[CONF_PLATFORM]: cfg}
            export_config.append(ents)
        fname = (
            config_name + ".yaml" if not config_name.endswith(".yaml") else config_name
        )
        fname = fname.replace(" ", "_")
        template_dir = os.path.dirname(templates_dir.__file__)
        template_file = os.path.join(template_dir, fname)

        cls.yaml_dump(export_config, template_file)


################################
##       config flows         ##
################################

from ..const import CONF_GATEWAY_ID, CONF_LOCAL_KEY, CONF_NODE_ID

GATEWAY = NamedTuple("Gateway", [("id", str), ("data", dict)])


def get_gateway_by_deviceid(device_id: str, cloud_data: dict) -> GATEWAY:
    """Return the gateway (id, data) of the sub-deviceID if existed in cloud_data."""

    if sub_device := cloud_data.get(device_id):
        for dev_id, dev_data in cloud_data.items():
            # Get gateway Assuming the LocalKey is the same gateway LocalKey!
            if (
                dev_id != device_id
                and not dev_data.get(CONF_NODE_ID)
                and dev_data.get(CONF_LOCAL_KEY) == sub_device.get(CONF_LOCAL_KEY)
            ):
                return GATEWAY(dev_id, dev_data)


###############################
#   Gateway onboarding        #
###############################
INFRARED_PREFIX = "infrared"


def find_discovered_by_ip(discovered: dict, host: str) -> dict | None:
    """Return the UDP-discovered device that announced itself from `host`."""
    for dev in (discovered or {}).values():
        if dev.get("ip") == host:
            return dev
    return None


def gateway_children(gateway_id: str, cloud_devices: dict) -> dict[str, dict]:
    """Return the cloud devices that hang off `gateway_id`.

    Tuya returns a sub-device with the SAME local key as its gateway and a
    `node_id` (Zigbee/BLE cid). Some data centers also fill `gateway_id`;
    when they do, trust it over the key match. IR remotes are excluded:
    they are virtual children of an IR blaster, not Zigbee devices.
    """
    gateway = cloud_devices.get(gateway_id)
    if not gateway:
        return {}
    gw_key = gateway.get(CONF_LOCAL_KEY)
    children = {}
    for dev_id, dev in cloud_devices.items():
        if dev_id == gateway_id or not dev.get(CONF_NODE_ID):
            continue
        if str(dev.get("category", "")).startswith(INFRARED_PREFIX):
            continue
        explicit_gw = dev.get("gateway_id")
        if explicit_gw:
            if explicit_gw == gateway_id:
                children[dev_id] = dev
        elif gw_key and dev.get(CONF_LOCAL_KEY) == gw_key:
            children[dev_id] = dev
    return children


# Categories that are battery powered by definition: they report and then
# sleep, so they cannot be expected to answer a query during setup.
SLEEPY_CATEGORIES = {
    "pir",  # motion sensor
    "mcs",  # door/window sensor
    "wsdcg",  # temperature/humidity sensor
    "sj",  # water leak sensor
    "ywbj",  # smoke sensor
    "rqbj",  # gas sensor
    "sos",  # emergency button
    "wxkg",  # scene/wireless switch
    "ms",  # lock
    "znhsj",  # soil sensor
}
DEFAULT_SLEEP_TIME = 1800
# Categories that only ever receive commands: they have no state to report,
# so a status query just times out.
WRITE_ONLY_CATEGORIES = {
    "wnykq",  # IR blaster
}
# LocalTuya marks a write-only device with DP "0" in its manual DPs.
WRITE_ONLY_MARKER = "0"


def sleep_time_for_category(category: str) -> int:
    """Seconds of silence to tolerate from a child of this category."""
    return DEFAULT_SLEEP_TIME if category in SLEEPY_CATEGORIES else 0


def manual_dps_for_category(category: str) -> str | None:
    """Manual DPs to preset for a child of this category, if any."""
    return WRITE_ONLY_MARKER if category in WRITE_ONLY_CATEGORIES else None


def discovered_from_gateway(
    host: str,
    gateway_id: str,
    version: str,
    product_key: str | None,
    children: dict[str, dict],
) -> dict[str, dict]:
    """Shape gateway children like UDP-discovered devices so the normal
    auto-configure path (setup_localtuya_devices) can consume them."""
    return {
        dev_id: {
            "ip": host,
            "gwId": dev_id,
            "version": version,
            "productKey": product_key,
            CONF_NODE_ID: dev.get(CONF_NODE_ID),
            CONF_GATEWAY_ID: gateway_id,
        }
        for dev_id, dev in children.items()
    }


###############################
#    Auto configure device    #
###############################
from .ha_entities import gen_localtuya_entities
