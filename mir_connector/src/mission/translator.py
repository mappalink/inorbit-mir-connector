# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Mission translator that compiles consecutive InOrbit waypoint steps
into single native MiR missions for continuous motion planning.

Step grouping:
    Input:  [wp_A, wp_B, wait_5s, wp_C, wp_D]
    Output: [MirNativeMission([A,B]), wait_5s, MirNativeMission([C,D])]

Coordinate transform:
    - Native frameId (MiR map GUID): coordinates pass through directly
    - Shared "map" frameId (FMS): inverse SpatialTransformation applied
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from inorbit_edge_executor.datatypes import MissionStepPoseWaypoint
from inorbit_edge_executor.inorbit import InOrbitAPI
from inorbit_edge_executor.mission import Mission

from .datatypes import (
    MirInOrbitMission,
    MirStepsList,
    MirWaypoint,
    MissionDefinitionMir,
    MissionStepExecuteMirNativeMission,
)

logger = logging.getLogger(__name__)

# Shared "map" frame identifier used by InOrbit's FMS
_MAP_FRAME_ID = "map"


class SpatialTransform:
    """Wraps a forward 3x3 affine matrix (native -> map) and provides
    the inverse (map -> native) for coordinate conversion.

    The InOrbit SpatialTransformation stores the *forward* matrix that
    converts native robot-frame coordinates to the shared "map" frame.
    When translating InOrbit waypoints (in "map" frame) back to
    MiR-native coordinates we need the *inverse*.
    """

    def __init__(self, forward_matrix: list[list[float]]):
        r00 = forward_matrix[0][0]
        r01 = forward_matrix[0][1]
        tx = forward_matrix[0][2]
        r10 = forward_matrix[1][0]
        r11 = forward_matrix[1][1]
        ty = forward_matrix[1][2]

        self._rot_angle = math.atan2(r10, r00)

        # Inverse rotation: R^T
        self._ir00 = r00
        self._ir01 = r10
        self._ir10 = r01
        self._ir11 = r11

        # Inverse translation: -R^T @ t
        self._itx = -(self._ir00 * tx + self._ir01 * ty)
        self._ity = -(self._ir10 * tx + self._ir11 * ty)

    def map_to_native(
        self, x_map: float, y_map: float, theta_map: float
    ) -> tuple[float, float, float]:
        """Convert a pose from the shared "map" frame to MiR native frame."""
        x_native = self._ir00 * x_map + self._ir01 * y_map + self._itx
        y_native = self._ir10 * x_map + self._ir11 * y_map + self._ity
        theta_native = theta_map - self._rot_angle
        return x_native, y_native, theta_native


# Cache for SpatialTransform objects, keyed by (robot_id, native_map_id)
_transform_cache: dict[tuple[str, str], SpatialTransform] = {}


async def fetch_spatial_transform(
    api: InOrbitAPI,
    robot_id: str,
    native_map_id: str,
) -> Optional[SpatialTransform]:
    """Fetch and cache the SpatialTransformation from InOrbit."""
    cache_key = (robot_id, native_map_id)
    if cache_key in _transform_cache:
        return _transform_cache[cache_key]

    try:
        response = await api.get(
            f"robots/{robot_id}/config?kind=SpatialTransformation&id={native_map_id}"
        )
        data = response.json()
        matrix = data.get("spec", data).get("transformation")
        if not matrix:
            logger.warning(
                "No SpatialTransformation found for robot=%s frame=%s",
                robot_id,
                native_map_id,
            )
            return None
        transform = SpatialTransform(matrix)
        _transform_cache[cache_key] = transform
        logger.info("Cached SpatialTransform for robot=%s frame=%s", robot_id, native_map_id)
        return transform
    except Exception:
        logger.warning(
            "Failed to fetch SpatialTransformation for robot=%s frame=%s",
            robot_id,
            native_map_id,
            exc_info=True,
        )
        return None


class InOrbitToMirTranslator:
    """Translates InOrbit missions by compiling consecutive waypoint steps
    into native MiR missions.

    Non-waypoint steps (wait, runAction, setData, etc.) pass through unchanged.
    """

    @staticmethod
    def translate(
        mission: Mission,
        spatial_transform: Optional[SpatialTransform] = None,
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
                frame_id = wp.frame_id

                # Transform from shared "map" frame to native MiR coordinates
                if frame_id == _MAP_FRAME_ID and spatial_transform is not None:
                    x, y, theta = spatial_transform.map_to_native(x, y, theta)

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
