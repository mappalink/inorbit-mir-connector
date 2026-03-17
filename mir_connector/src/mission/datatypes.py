# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""MiR-specific mission datatypes for mission translation.

Defines custom step types and mission classes used when consecutive
waypoint steps are compiled into a single native MiR mission.
"""

from __future__ import annotations

from typing import Any, List, Union

from pydantic import Field, model_validator

from inorbit_edge_executor.datatypes import (
    MissionDefinition,
    MissionStep,
    MissionStepPoseWaypoint,
    MissionStepRunAction,
    MissionStepSetData,
    MissionStepWait,
    MissionStepWaitUntil,
)
from inorbit_edge_executor.mission import Mission


class MirWaypoint(MissionStep):
    """A single waypoint in MiR-native coordinates.

    Carries x, y (meters) and orientation (degrees) ready to be sent
    as a ``move_to_position`` action.
    """

    x: float = Field(description="X coordinate in MiR native frame (meters)")
    y: float = Field(description="Y coordinate in MiR native frame (meters)")
    orientation: float = Field(description="Orientation in degrees (MiR convention)")


class MirAction(MissionStep):
    """Generic MiR action with pass-through parameters."""

    action_type: str = Field(description="MiR action type (e.g. 'docking', 'charging', 'wait')")
    parameters: dict[str, Any] = Field(default_factory=dict)


class MissionStepExecuteMirNativeMission(MissionStep):
    """Custom step that executes a compiled native MiR mission.

    Produced by the translator when consecutive waypoint/action steps are
    grouped. The behavior tree node creates a MiR mission definition, adds
    one action per entry, and queues it.
    """

    actions: List[Union[MirWaypoint, MirAction]] = Field(
        description="Ordered actions for native MiR mission"
    )
    robot_id: str = Field(description="InOrbit robot ID")

    @model_validator(mode="before")
    @classmethod
    def _migrate_waypoints(cls, data):
        """Backward-compat: accept serialized missions that still use 'waypoints'."""
        if isinstance(data, dict) and "waypoints" in data and "actions" not in data:
            data["actions"] = data.pop("waypoints")
        return data

    def accept(self, visitor):
        if hasattr(visitor, "visit_execute_mir_native_mission"):
            return visitor.visit_execute_mir_native_mission(self)
        if hasattr(visitor, "collect_step"):
            return visitor.collect_step(self)
        return None


# Type alias for MiR-specific steps list
MirStepsList = List[
    Union[
        MissionStepSetData,
        MissionStepPoseWaypoint,
        MissionStepRunAction,
        MissionStepWait,
        MissionStepWaitUntil,
        MissionStepExecuteMirNativeMission,
    ]
]


class MissionDefinitionMir(MissionDefinition):
    """Mission definition that supports MiR-specific step types."""

    steps: MirStepsList  # type: ignore[assignment]


class MirInOrbitMission(Mission):
    """Mission subclass using MiR-specific definition after translation."""

    definition: MissionDefinitionMir  # type: ignore[assignment]
