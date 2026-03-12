# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Mission translator that compiles consecutive InOrbit waypoint steps
into single native MiR missions for continuous motion planning.

Step grouping:
    Input:  [wp_A, wp_B, wait_5s, wp_C, wp_D]
    Output: [MirNativeMission([A,B]), wait_5s, MirNativeMission([C,D])]
"""

from __future__ import annotations

import logging
import math

from inorbit_edge_executor.datatypes import MissionStepPoseWaypoint
from inorbit_edge_executor.mission import Mission

from .datatypes import (
    MirInOrbitMission,
    MirStepsList,
    MirWaypoint,
    MissionDefinitionMir,
    MissionStepExecuteMirNativeMission,
)

logger = logging.getLogger(__name__)


class InOrbitToMirTranslator:
    """Translates InOrbit missions by compiling consecutive waypoint steps
    into native MiR missions.

    Non-waypoint steps (wait, runAction, setData, etc.) pass through unchanged.
    """

    @staticmethod
    def translate(
        mission: Mission,
    ) -> MirInOrbitMission:
        """Translate an InOrbit mission to MiR format.

        Consecutive MissionStepPoseWaypoint steps are grouped into a single
        MissionStepExecuteMirNativeMission. Any non-waypoint step flushes
        the current group.
        """
        if not mission.definition.steps:
            raise ValueError("Mission has no steps to translate")

        translated_steps: MirStepsList = []
        pending_waypoints: list[MirWaypoint] = []
        pending_labels: list[str] = []

        def flush_waypoints():
            if not pending_waypoints:
                return
            n = len(pending_waypoints)
            if n == 1:
                label = pending_labels[0] if pending_labels[0] else "Navigate to waypoint"
            else:
                label = f"Navigate {n} waypoints"
            translated_steps.append(
                MissionStepExecuteMirNativeMission(
                    label=label,
                    waypoints=list(pending_waypoints),
                    robot_id=mission.robot_id,
                )
            )
            pending_waypoints.clear()
            pending_labels.clear()

        for step in mission.definition.steps:
            if isinstance(step, MissionStepPoseWaypoint):
                wp = step.waypoint
                x, y, theta = wp.x, wp.y, wp.theta

                # MiR expects orientation in degrees
                orientation_deg = math.degrees(theta)

                pending_waypoints.append(
                    MirWaypoint(label=step.label, x=x, y=y, orientation=orientation_deg)
                )
                pending_labels.append(step.label or "")
                continue

            # Non-waypoint step — flush pending waypoints first
            flush_waypoints()
            translated_steps.append(step)

        flush_waypoints()

        translated_definition = MissionDefinitionMir(
            label=mission.definition.label,
            steps=translated_steps,
        )

        translated_mission = MirInOrbitMission(
            id=mission.id,
            robot_id=mission.robot_id,
            definition=translated_definition,
            arguments=mission.arguments,
        )

        logger.debug(
            "Translated mission %s: %d original steps -> %d translated steps",
            mission.id,
            len(mission.definition.steps),
            len(translated_steps),
        )

        return translated_mission
