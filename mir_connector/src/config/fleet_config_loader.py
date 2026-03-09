# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Fleet YAML loader — merges common + per-robot, nests MiR fields."""

import os
import logging
from copy import deepcopy
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Fields that belong under connector_config (MiR-specific)
MIR_FIELDS = [
    "mir_host_address",
    "mir_host_port",
    "mir_username",
    "mir_password",
    "mir_firmware_version",
    "mir_use_ssl",
    "verify_ssl",
    "ssl_ca_bundle",
    "ssl_verify_hostname",
    "enable_temporary_mission_group",
    "default_waypoint_mission_id",
    "mission_database_file",
]

# Mapping from mir_connection shorthand to canonical field names
MIR_CONNECTION_MAPPING = {
    "host": "mir_host_address",
    "port": "mir_host_port",
    "username": "mir_username",
    "password": "mir_password",
    "use_ssl": "mir_use_ssl",
}


def get_robot_config(config_filename: str, robot_id: str) -> dict[str, Any]:
    """Load config for a single robot, merging common + per-robot sections.

    Supports:
    - ``common:`` section for shared defaults
    - Per-robot sections at the top level
    - Optional ``mir_connection:`` shorthand mapped to canonical field names
    - Optional ``mir_api:`` section for firmware_version
    """
    with open(config_filename, "r", encoding="utf-8") as f:
        full_config = yaml.safe_load(f) or {}

    full_config = _expand_env_vars(full_config)

    if robot_id not in full_config:
        available = [k for k in full_config if k not in ("common",)]
        raise IndexError(f"Robot '{robot_id}' not found. Available: {available}")

    # Merge common + per-robot
    robot_config = deepcopy(full_config.get("common", {}))
    _deep_merge(robot_config, full_config[robot_id])

    # Handle mir_connection shorthand
    if "mir_connection" in robot_config:
        mir_conn = robot_config.pop("mir_connection")
        for short, canonical in MIR_CONNECTION_MAPPING.items():
            if short in mir_conn:
                robot_config[canonical] = mir_conn[short]
        for field in ["verify_ssl", "ssl_ca_bundle", "ssl_verify_hostname"]:
            if field in mir_conn:
                robot_config[field] = mir_conn[field]

    # Handle mir_api section
    if "mir_api" in robot_config:
        mir_api = robot_config.pop("mir_api")
        if "firmware_version" in mir_api:
            robot_config["mir_firmware_version"] = mir_api["firmware_version"]

    # Nest MiR-specific fields under connector_config
    if "connector_config" not in robot_config:
        robot_config["connector_config"] = {}
    for field in MIR_FIELDS:
        if field in robot_config:
            robot_config["connector_config"][field] = robot_config.pop(field)

    return robot_config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base (mutates base)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ${VAR} references in config values."""
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(i) for i in obj]
    elif isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def validate_config_structure(config_filename: str) -> dict[str, Any]:
    """Validate the fleet configuration file and provide helpful diagnostics.

    Returns a dict with keys: valid, structure_type, robots, has_common_section,
    suggestions.
    """
    try:
        with open(config_filename, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f) or {}
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
            "suggestions": ["Check if the file exists and has valid YAML syntax"],
        }

    validation: dict[str, Any] = {
        "valid": True,
        "structure_type": "unknown",
        "robots": [],
        "has_common_section": False,
        "suggestions": [],
    }

    if "common" in full_config:
        validation["structure_type"] = "hierarchical"
        validation["has_common_section"] = True
        validation["robots"] = [k for k in full_config if k != "common"]
    else:
        validation["structure_type"] = "flat"
        validation["robots"] = list(full_config.keys())

    if not validation["has_common_section"]:
        validation["suggestions"].append(
            "Consider adding a 'common' section to reduce configuration duplication"
        )

    if len(validation["robots"]) == 0:
        validation["valid"] = False
        validation["suggestions"].append("No robot configurations found")

    return validation
