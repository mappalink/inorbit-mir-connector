# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Configuration models for MiR connector."""

from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from inorbit_connector.models import InorbitConnectorConfig

CONNECTOR_TYPES = ["MiR100", "MiR200", "MiR250", "MiR500"]
FIRMWARE_VERSIONS = ["v2", "v3"]


class MirConnectorConfig(BaseSettings):
    """MiR-specific settings.

    Env vars prefixed with INORBIT_MIR_ (e.g. INORBIT_MIR_MIR_HOST_ADDRESS).
    """

    model_config = SettingsConfigDict(
        env_prefix="INORBIT_MIR_",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="allow",
    )

    mir_host_address: str
    mir_host_port: int = 80
    mir_username: str
    mir_password: str
    mir_firmware_version: str = "v2"

    # SSL
    mir_use_ssl: bool = False
    verify_ssl: bool = True
    ssl_ca_bundle: Optional[str] = None
    ssl_verify_hostname: bool = True

    # Mission settings
    enable_temporary_mission_group: bool = True
    default_waypoint_mission_id: Optional[str] = None

    # Database
    mission_database_file: Optional[str] = None

    @field_validator("mir_firmware_version")
    def firmware_version_validation(cls, v):
        if v not in FIRMWARE_VERSIONS:
            raise ValueError(
                f"Unexpected firmware version '{v}'. Expected one of {FIRMWARE_VERSIONS}"
            )
        return v

    @model_validator(mode="after")
    def check_waypoint_config(self):
        if not self.enable_temporary_mission_group and not self.default_waypoint_mission_id:
            raise ValueError(
                "default_waypoint_mission_id must be set when "
                "enable_temporary_mission_group is False."
            )
        return self


class ConnectorConfig(InorbitConnectorConfig):
    """Full config: InOrbit base + MiR specifics."""

    connector_config: MirConnectorConfig

    @field_validator("connector_type")
    def connector_type_validation(cls, v):
        if v not in CONNECTOR_TYPES:
            raise ValueError(
                f"Unexpected connector type '{v}'. Expected one of {CONNECTOR_TYPES}"
            )
        return v
