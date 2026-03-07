# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""MiR-specific mission datatypes for mission translation.

Defines custom step types and mission classes used when consecutive
waypoint steps are compiled into a single native MiR mission.
"""

from __future__ import annotations

from typing import List, Union

from pydantic import Field

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


class MissionStepExecuteMirNativeMission(MissionStep):
    """Custom step that executes a compiled native MiR mission.

    Produced by the translator when consecutive waypoint steps are grouped.
    The behavior tree node creates a MiR mission definition, adds one
    ``move_to_position`` action per waypoint, and queues it.
    """

    waypoints: List[MirWaypoint] = Field(description="Ordered waypoints for native MiR mission")
    robot_id: str = Field(description="InOrbit robot ID")

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
