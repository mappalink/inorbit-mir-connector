# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Unit tests for MiR behavior tree nodes: serialization and the goto_position step."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from inorbit_edge_executor.behavior_tree import (
    NODE_STATE_ERROR,
    NODE_STATE_SUCCESS,
    BehaviorTreeSequential,
    RunActionNode,
    build_tree_from_object,
)
from inorbit_edge_executor.datatypes import (
    MissionDefinition,
    MissionRuntimeOptions,
    MissionRuntimeSharedMemory,
    MissionStepRunAction,
)
from inorbit_edge_executor.mission import Mission

from mir_connector.src.mission.behavior_tree import (
    ACTION_GOTO_POSITION,
    MIR_MOVE_MISSION_GUID,
    MirBehaviorTreeBuilderContext,
    MirNodeFromStepBuilder,
    QueueMirMoveToPositionNode,
    WaitForMirMissionCompletionNode,
)
from mir_connector.src.mission.datatypes import (
    MirAction,
    MirWaypoint,
    MissionStepExecuteMirNativeMission,
)


class TestMissionStepExecuteMirNativeMissionSerialization:
    """Verify round-trip serialization of MissionStepExecuteMirNativeMission.

    This is critical for pause/resume: the step is serialized to SQLite
    on pause and deserialized on resume via model_validate().
    """

    def _make_step_with_waypoints(self):
        return MissionStepExecuteMirNativeMission(
            label="Navigate 2 waypoints",
            actions=[
                MirWaypoint(label="wp1", x=1.0, y=2.0, orientation=90.0),
                MirWaypoint(label="wp2", x=3.0, y=4.0, orientation=180.0),
            ],
            robot_id="mir200-1",
        )

    def _make_step_with_actions(self):
        return MissionStepExecuteMirNativeMission(
            label="Execute 2 actions",
            actions=[
                MirAction(label="Wait", action_type="wait", parameters={"time": "00:01:00.000000"}),
                MirWaypoint(label="wp1", x=1.0, y=2.0, orientation=90.0),
            ],
            robot_id="mir200-1",
        )

    def _make_step_wait_only(self):
        return MissionStepExecuteMirNativeMission(
            label="Wait only",
            actions=[
                MirAction(
                    label="Wait 60 seconds",
                    action_type="wait",
                    parameters={"time": "00:01:00.000000"},
                ),
            ],
            robot_id="mir200-1",
        )

    def test_round_trip_waypoints(self):
        step = self._make_step_with_waypoints()
        serialized = step.model_dump(mode="json", exclude_none=True)
        restored = MissionStepExecuteMirNativeMission.model_validate(serialized)
        assert len(restored.actions) == 2
        assert isinstance(restored.actions[0], MirWaypoint)
        assert restored.actions[0].x == 1.0
        assert restored.actions[1].orientation == 180.0

    def test_round_trip_mixed_actions(self):
        step = self._make_step_with_actions()
        serialized = step.model_dump(mode="json", exclude_none=True)
        restored = MissionStepExecuteMirNativeMission.model_validate(serialized)
        assert len(restored.actions) == 2
        assert isinstance(restored.actions[0], MirAction)
        assert restored.actions[0].action_type == "wait"
        assert isinstance(restored.actions[1], MirWaypoint)

    def test_round_trip_wait_only(self):
        """Reproduces the v0.1.19 bug: wait-only step failed to deserialize."""
        step = self._make_step_wait_only()
        serialized = step.model_dump(mode="json", exclude_none=True)
        restored = MissionStepExecuteMirNativeMission.model_validate(serialized)
        assert len(restored.actions) == 1
        assert isinstance(restored.actions[0], MirAction)
        assert restored.actions[0].action_type == "wait"

    def test_round_trip_fails_without_exclude_none(self):
        """Confirms the bug: model_dump without exclude_none produces invalid data."""
        step = self._make_step_wait_only()
        serialized = step.model_dump(mode="json")  # no exclude_none
        with pytest.raises(Exception):
            MissionStepExecuteMirNativeMission.model_validate(serialized)


def _context(mir_api):
    mission = Mission(
        id="mission-001",
        robot_id="mir200-1",
        definition=MissionDefinition(label="test", steps=[]),
    )
    return MirBehaviorTreeBuilderContext(
        mir_api=mir_api,
        missions_group_id="group-guid",
        firmware_version="v2",
        mission=mission,
        options=MissionRuntimeOptions(),
        shared_memory=MissionRuntimeSharedMemory(),
    )


def _built(mir_api, step):
    """The step's tree, with the shared memory frozen as the executor does after building."""
    context = _context(mir_api)
    tree = MirNodeFromStepBuilder(context).visit_run_action(step)
    context.shared_memory.freeze()
    return tree


def _goto_position_step(arguments: dict | None = None, **kwargs):
    run_action: dict = {"actionId": ACTION_GOTO_POSITION}
    if arguments is not None:
        run_action["arguments"] = arguments
    return MissionStepRunAction(runAction=run_action, **kwargs)


class TestGotoPositionStep:
    """A goto_position runAction step runs locally and waits for arrival.

    Through the default RunActionNode it reported success once the Move
    mission was queued, so the mission completed while the robot was driving.
    """

    def test_builds_queue_then_wait(self):
        step = _goto_position_step({"position_guid": "pos-guid"}, label="Go", timeoutSecs=300)
        tree = MirNodeFromStepBuilder(_context(AsyncMock())).visit_run_action(step)

        assert isinstance(tree, BehaviorTreeSequential)
        queue_node, wait_node = tree.nodes
        assert isinstance(queue_node, QueueMirMoveToPositionNode)
        assert isinstance(wait_node, WaitForMirMissionCompletionNode)
        assert wait_node._timeout_secs == 300

    def test_missing_position_guid_raises(self):
        with pytest.raises(RuntimeError, match="position_guid"):
            MirNodeFromStepBuilder(_context(AsyncMock())).visit_run_action(_goto_position_step())

    def test_other_actions_keep_default_node(self):
        step = MissionStepRunAction(runAction={"actionId": "unknown_action"}, label="x")
        node = MirNodeFromStepBuilder(_context(AsyncMock())).visit_run_action(step)
        assert isinstance(node, RunActionNode)

    @pytest.mark.asyncio
    async def test_waits_until_mir_reports_done(self):
        mir_api = AsyncMock()
        mir_api.queue_mission.return_value = {"id": 42}
        mir_api.get_mission_queue_entry.side_effect = [
            {"state": "Pending"},
            {"state": "Executing"},
            {"state": "Done"},
        ]
        step = _goto_position_step({"position_guid": "pos-guid"}, label="Go")
        tree = _built(mir_api, step)

        await tree.execute()

        mir_api.queue_mission.assert_awaited_once_with(
            MIR_MOVE_MISSION_GUID,
            parameters=[{"input_name": "Position", "value": "pos-guid"}],
        )
        assert mir_api.get_mission_queue_entry.await_count == 3
        mir_api.get_mission_queue_entry.assert_awaited_with(42)
        assert tree.state == NODE_STATE_SUCCESS

    @pytest.mark.asyncio
    async def test_aborted_move_fails_the_step(self):
        mir_api = AsyncMock()
        mir_api.queue_mission.return_value = {"id": 42}
        mir_api.get_mission_queue_entry.return_value = {"state": "Aborted", "message": "blocked"}
        step = _goto_position_step({"position_guid": "pos-guid"}, label="Go")
        tree = _built(mir_api, step)

        await tree.execute()

        assert tree.state == NODE_STATE_ERROR

    @pytest.mark.asyncio
    async def test_queue_failure_fails_the_step(self):
        mir_api = AsyncMock()
        mir_api.queue_mission.side_effect = RuntimeError("Server disconnected")
        step = _goto_position_step({"position_guid": "pos-guid"}, label="Go")
        tree = _built(mir_api, step)

        await tree.execute()

        assert tree.state == NODE_STATE_ERROR
        mir_api.get_mission_queue_entry.assert_not_awaited()

    def test_queue_node_round_trip(self):
        """Pause/resume persists the tree; the node must come back with its position."""
        context = _context(AsyncMock())
        node = QueueMirMoveToPositionNode(context, "pos-guid", label="Queue")

        restored = build_tree_from_object(_context(AsyncMock()), node.dump_object())

        assert isinstance(restored, QueueMirMoveToPositionNode)
        assert restored._position_guid == "pos-guid"
        assert restored.label == "Queue"
