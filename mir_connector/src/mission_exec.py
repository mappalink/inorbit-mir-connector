# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Edge mission execution module for MiR robots.

Extends inorbit-edge-executor for translating missions into MiR language
and executing them with pause/resume/abort support.
"""

import json
import logging
import math
import re
from enum import Enum
from typing import Optional

from inorbit_connector.connector import CommandResultCode
from inorbit_edge_executor.datatypes import MissionRuntimeOptions, Robot
from inorbit_edge_executor.db import get_db
from inorbit_edge_executor.inorbit import RobotApi, RobotApiFactory
from inorbit_edge_executor.mission import Mission
from inorbit_edge_executor.worker_pool import WorkerPool

from mir_connector.src.mir_api import MirApi, SetStateId
from mir_connector.src.mir_api.missions_group import MirMissionsGroupHandler
from mir_connector.src.mission.behavior_tree import MirBehaviorTreeBuilderContext
from mir_connector.src.mission.datatypes import MirInOrbitMission
from mir_connector.src.mission.translator import (
    InOrbitToMirTranslator,
    SpatialTransform,
    fetch_spatial_transform,
)
from mir_connector.src.mission.tree_builder import MirTreeBuilder


class MissionScriptName(Enum):
    EXECUTE_MISSION_ACTION = "executeMissionAction"
    CANCEL_MISSION_ACTION = "cancelMissionAction"
    UPDATE_MISSION_ACTION = "updateMissionAction"


# ---------------------------------------------------------------------------
# Local pose evaluation for native-frame waypoint WaitExpressions
# ---------------------------------------------------------------------------

_POSE_EXPR_RE = re.compile(
    r"pose\s*=\s*getValue\('pose'\).*"
    r"pose\.frameId\s*==\s*'(?P<frame_id>[^']+)'"
    r".*sqrt\(pow\(pose\.x-(?P<tx>[\d.e+-]+),\s*2\)\s*\+\s*pow\(pose\.y-(?P<ty>[\d.e+-]+),\s*2\)\)"
    r"\s*<\s*(?P<dist_tol>[\d.e+-]+)"
    r".*angularDistance\(theta,\s*(?P<ttheta>[\d.e+-]+)\)\)"
    r"\s*<\s*(?P<ang_tol>[\d.e+-]+)"
)


def _angular_distance(a: float, b: float) -> float:
    d = (a - b) % (2 * math.pi)
    if d > math.pi:
        d -= 2 * math.pi
    return abs(d)


class MirRobotApi(RobotApi):
    """Evaluates pose-waypoint expressions locally when they reference
    the connector's native map frame."""

    def __init__(self, robot, api, *, mir_api: MirApi, native_map_id: str):
        super().__init__(robot, api)
        self._mir_api = mir_api
        self._native_map_id = native_map_id
        self._logger = logging.getLogger(self.__class__.__name__)

    async def evaluate_expression(self, expression: str):
        m = _POSE_EXPR_RE.search(expression)
        if not m or m.group("frame_id") != self._native_map_id:
            return await super().evaluate_expression(expression)

        try:
            status = await self._mir_api.get_status()
            pos = status.get("position", {})
            px = pos.get("x", 0.0)
            py = pos.get("y", 0.0)
            ptheta = math.radians(pos.get("orientation", 0.0))
            p_map_id = status.get("map_id", "")
        except Exception as e:
            self._logger.warning(f"Local pose eval failed, falling back to server: {e}")
            return await super().evaluate_expression(expression)

        tx = float(m.group("tx"))
        ty = float(m.group("ty"))
        ttheta = float(m.group("ttheta"))
        dist_tol = float(m.group("dist_tol"))
        ang_tol = float(m.group("ang_tol"))

        frame_ok = p_map_id == self._native_map_id
        dist = math.sqrt((px - tx) ** 2 + (py - ty) ** 2)
        ang = _angular_distance(ptheta, ttheta)

        result = frame_ok and dist < dist_tol and ang < ang_tol
        self._logger.debug(
            f"Local pose eval: frame={p_map_id}=={self._native_map_id}:{frame_ok} "
            f"dist={dist:.3f}<{dist_tol}:{dist < dist_tol} "
            f"ang={ang:.3f}<{ang_tol}:{ang < ang_tol} -> {result}"
        )
        return result


class MirRobotApiFactory(RobotApiFactory):
    def __init__(self, api, *, mir_api: MirApi, native_map_id: str):
        super().__init__(api)
        self._mir_api = mir_api
        self._native_map_id = native_map_id

    def build(self, robot_id: str):
        return MirRobotApi(
            Robot(id=robot_id),
            self._api,
            mir_api=self._mir_api,
            native_map_id=self._native_map_id,
        )


class MirWorkerPool(WorkerPool):
    def __init__(
        self,
        mir_api: MirApi,
        *args,
        missions_group: Optional[MirMissionsGroupHandler] = None,
        firmware_version: str = "v3",
        connector_type: str = "",
        account_id: str = "",
        **kwargs,
    ):
        self.mir_api = mir_api
        self._missions_group = missions_group
        self._firmware_version = firmware_version
        self._connector_type = connector_type
        self._account_id = account_id
        super().__init__(behavior_tree_builder=MirTreeBuilder(), *args, **kwargs)
        self.logger = logging.getLogger(name=self.__class__.__name__)
        self._native_map_id = ""
        self._prefetched_transform: Optional[SpatialTransform] = None

    def set_native_map_id(self, map_id: str):
        self._native_map_id = map_id

    def create_builder_context(self) -> MirBehaviorTreeBuilderContext:
        missions_group_id = None
        if self._missions_group is not None:
            missions_group_id = self._missions_group.missions_group_id
        return MirBehaviorTreeBuilderContext(
            mir_api=self.mir_api,
            missions_group_id=missions_group_id,
            firmware_version=self._firmware_version,
            connector_type=self._connector_type,
        )

    def prepare_builder_context(self, context, mission):
        super().prepare_builder_context(context, mission)
        if self._native_map_id:
            factory = MirRobotApiFactory(
                self._api,
                mir_api=self.mir_api,
                native_map_id=self._native_map_id,
            )
            context.robot_api_factory = factory
            context.robot_api = factory.build(mission.robot_id)

    def translate_mission(self, mission: Mission) -> MirInOrbitMission:
        self.logger.debug(f"Translating mission {mission.id}")
        return InOrbitToMirTranslator.translate(
            mission=mission,
            spatial_transform=self._prefetched_transform,
        )

    def deserialize_mission(self, serialized_mission: dict) -> MirInOrbitMission:
        return MirInOrbitMission.model_validate(serialized_mission)

    async def submit_work(self, mission, options, shared_memory=None):
        self._prefetched_transform = await self._maybe_fetch_transform(mission)
        try:
            return await super().submit_work(mission, options, shared_memory)
        finally:
            self._prefetched_transform = None

    async def _maybe_fetch_transform(self, mission: Mission) -> Optional[SpatialTransform]:
        has_map_frame = any(
            hasattr(step, "waypoint") and getattr(step.waypoint, "frame_id", None) == "map"
            for step in mission.definition.steps
        )
        if not has_map_frame or not self._native_map_id:
            return None
        return await fetch_spatial_transform(
            self._api, mission.robot_id, self._native_map_id, self._account_id
        )

    async def pause_mission(self, mission_id):
        import asyncio

        await asyncio.gather(
            super().pause_mission(mission_id),
            self.mir_api.set_state(SetStateId.PAUSE.value),
        )

    async def resume_mission(self, mission_id):
        import asyncio

        await asyncio.gather(
            super().resume_mission(mission_id),
            self.mir_api.set_state(SetStateId.READY.value),
        )

    async def abort_mission(self, mission_id):
        super().abort_mission(mission_id)
        await self.mir_api.abort_all_missions()


class MirMissionExecutor:
    """Mission executor for MiR connector using InOrbit edge executor.

    Handles mission submission, pause, resume, and abort operations.
    """

    def __init__(
        self,
        robot_id,
        inorbit_api,
        mir_api,
        database_file=None,
        missions_group: Optional[MirMissionsGroupHandler] = None,
        firmware_version: str = "v3",
        connector_type: str = "",
        account_id: str = "",
    ):
        self.logger = logging.getLogger(name=self.__class__.__name__)
        self.robot_id = robot_id
        self.inorbit_api = inorbit_api
        self.mir_api = mir_api
        self._missions_group = missions_group
        self._firmware_version = firmware_version
        self._connector_type = connector_type
        self._account_id = account_id
        if database_file:
            self.database_file = "dummy" if database_file == "dummy" else f"sqlite:{database_file}"
        else:
            self.database_file = f"sqlite:missions_{robot_id}.db"
        self._worker_pool: Optional[MirWorkerPool] = None
        self._initialized = False

    async def initialize(self):
        if not self._initialized:
            db = await get_db(self.database_file)
            self._worker_pool = MirWorkerPool(
                mir_api=self.mir_api,
                api=self.inorbit_api,
                db=db,
                missions_group=self._missions_group,
                firmware_version=self._firmware_version,
                connector_type=self._connector_type,
                account_id=self._account_id,
            )
            await self._worker_pool.start()
            self._initialized = True
            self.logger.info("MiR Mission Executor initialized")

    async def shutdown(self):
        if self._worker_pool:
            await self._worker_pool.shutdown()
            self.logger.info("MiR Mission Executor shut down")

    def is_initialized(self) -> bool:
        return self._initialized

    async def handle_command(self, script_name: str, script_args: dict, options: dict) -> bool:
        """Handle mission-related commands. Returns True if handled."""
        if not self._initialized:
            self.logger.warning("Mission executor not initialized")
            return False

        if script_name == MissionScriptName.EXECUTE_MISSION_ACTION.value:
            await self._handle_execute(script_args, options)
            return True
        elif script_name == MissionScriptName.CANCEL_MISSION_ACTION.value:
            await self._handle_cancel(script_args, options)
            return True
        elif script_name == MissionScriptName.UPDATE_MISSION_ACTION.value:
            await self._handle_update(script_args, options)
            return True
        return False

    async def _handle_execute(self, script_args: dict, options: dict) -> None:
        try:
            mission_id = script_args.get("missionId")
            mission_definition = json.loads(script_args.get("missionDefinition", "{}"))
            mission_args = json.loads(script_args.get("missionArgs", "{}"))
            mission_options_dict = json.loads(script_args.get("options", "{}"))

            mission = Mission(
                id=mission_id,
                robot_id=self.robot_id,
                definition=mission_definition,
                arguments=mission_args,
            )
            mission_runtime_options = MissionRuntimeOptions(**mission_options_dict)

            await self._worker_pool.submit_work(mission, mission_runtime_options)
            options["result_function"](CommandResultCode.SUCCESS)

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in mission definition: {e}")
            options["result_function"](
                CommandResultCode.FAILURE, execution_status_details=f"Invalid JSON: {e}"
            )
        except Exception as e:
            self.logger.error(f"Failed to execute mission: {e}")
            options["result_function"](CommandResultCode.FAILURE, execution_status_details=str(e))

    async def _handle_cancel(self, script_args: dict, options: dict) -> None:
        mission_id = script_args.get("missionId")
        self.logger.info(f"Cancelling mission {mission_id}")
        try:
            result = await self._worker_pool.abort_mission(mission_id)
            if result is False:
                options["result_function"](CommandResultCode.FAILURE, "Mission not found")
            else:
                options["result_function"](CommandResultCode.SUCCESS)
        except Exception as e:
            self.logger.error(f"Failed to cancel mission {mission_id}: {e}")
            options["result_function"](CommandResultCode.FAILURE, execution_status_details=str(e))

    async def _handle_update(self, script_args: dict, options: dict) -> None:
        mission_id = script_args.get("missionId")
        action = script_args.get("action")
        self.logger.info(f"Updating mission {mission_id}: {action}")
        try:
            if action == "pause":
                await self._worker_pool.pause_mission(mission_id)
            elif action == "resume":
                await self._worker_pool.resume_mission(mission_id)
            else:
                raise Exception(f"Unknown action: {action}")
            options["result_function"](CommandResultCode.SUCCESS)
        except Exception as e:
            self.logger.error(f"Failed to update mission {mission_id}: {e}")
            options["result_function"](CommandResultCode.FAILURE, execution_status_details=str(e))
