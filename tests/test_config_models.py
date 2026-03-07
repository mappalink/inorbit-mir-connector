# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for mir_connector.src.config.models."""

from __future__ import annotations

import copy

import pytest

from mir_connector.src.config.models import (
    MirConnectorConfig,
    ConnectorConfig,
    CONNECTOR_TYPES,
    FIRMWARE_VERSIONS,
)


@pytest.fixture()
def mir_config_data() -> dict:
    """Return a minimal valid MirConnectorConfig payload."""
    return {
        "mir_host_address": "10.102.180.126",
        "mir_host_port": 80,
        "mir_username": "admin",
        "mir_password": "secret",
        "mir_firmware_version": "v2",
    }


@pytest.fixture()
def base_config_data(mir_config_data) -> dict:
    """Return a minimal valid ConnectorConfig payload."""
    return {
        "connector_type": "MiR200",
        "connector_config": mir_config_data,
    }


def test_valid_connector_config(base_config_data: dict) -> None:
    config = ConnectorConfig(**base_config_data)
    assert config.connector_type == "MiR200"
    assert isinstance(config.connector_config, MirConnectorConfig)


def test_invalid_connector_type(base_config_data: dict) -> None:
    data = copy.deepcopy(base_config_data)
    data["connector_type"] = "InvalidBot"
    with pytest.raises(ValueError, match="Unexpected connector type"):
        ConnectorConfig(**data)


@pytest.mark.parametrize("connector_type", CONNECTOR_TYPES)
def test_all_connector_types_accepted(base_config_data: dict, connector_type: str) -> None:
    data = copy.deepcopy(base_config_data)
    data["connector_type"] = connector_type
    config = ConnectorConfig(**data)
    assert config.connector_type == connector_type


def test_invalid_firmware_version(mir_config_data: dict) -> None:
    data = copy.deepcopy(mir_config_data)
    data["mir_firmware_version"] = "v99"
    with pytest.raises(ValueError, match="Unexpected firmware version"):
        MirConnectorConfig(**data)


@pytest.mark.parametrize("fw", FIRMWARE_VERSIONS)
def test_valid_firmware_versions(mir_config_data: dict, fw: str) -> None:
    data = copy.deepcopy(mir_config_data)
    data["mir_firmware_version"] = fw
    config = MirConnectorConfig(**data)
    assert config.mir_firmware_version == fw


def test_waypoint_config_validation(mir_config_data: dict) -> None:
    """When temp mission group is disabled, default_waypoint_mission_id is required."""
    data = copy.deepcopy(mir_config_data)
    data["enable_temporary_mission_group"] = False
    data["default_waypoint_mission_id"] = None
    with pytest.raises(ValueError, match="default_waypoint_mission_id"):
        MirConnectorConfig(**data)


def test_waypoint_config_with_mission_id(mir_config_data: dict) -> None:
    data = copy.deepcopy(mir_config_data)
    data["enable_temporary_mission_group"] = False
    data["default_waypoint_mission_id"] = "some-uuid"
    config = MirConnectorConfig(**data)
    assert config.default_waypoint_mission_id == "some-uuid"


def test_default_values(mir_config_data: dict) -> None:
    config = MirConnectorConfig(**mir_config_data)
    assert config.mir_host_port == 80
    assert config.mir_use_ssl is False
    assert config.verify_ssl is True
    assert config.enable_temporary_mission_group is True
    assert config.mission_database_file is None


def test_mir_config_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INORBIT_MIR_MIR_HOST_ADDRESS", "192.168.1.10")
    monkeypatch.setenv("INORBIT_MIR_MIR_USERNAME", "env-user")
    monkeypatch.setenv("INORBIT_MIR_MIR_PASSWORD", "env-pass")
    monkeypatch.setenv("INORBIT_MIR_MIR_FIRMWARE_VERSION", "v2")

    config = MirConnectorConfig(
        mir_host_address="192.168.1.10",
        mir_username="env-user",
        mir_password="env-pass",
    )
    assert config.mir_host_address == "192.168.1.10"
