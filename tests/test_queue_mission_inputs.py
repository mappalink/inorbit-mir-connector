# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""`queue_mission` and `run_mission_now` pass mission inputs to the MiR.

From the FM lab, 2026-10-01. The MiR's built-in ChargeAtStation takes its charger
as a mission input, `chargingStationPosition`. Queued without it the docking step
looks for marker "0" and the robot goes to Error ("Unable to find the end position
of the docking in the database"); queued with it the robot docks and charges. The
commands used to send the mission id alone, so no InOrbit button could run it.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from inorbit_connector.connector import CommandResultCode
from mir_connector.src.connector import MirConnector, mission_inputs

CHARGE = "mirconst-guid-0000-0004-actionlist00"
STATION = "7e201f48-2763-11f1-b07e-000129922d22"


def test_no_inputs_when_only_the_mission_id_is_given():
    assert mission_inputs({"mission_id": CHARGE}) is None


def test_every_other_argument_is_a_mission_input():
    assert mission_inputs({"mission_id": CHARGE, "chargingStationPosition": STATION}) == [
        {"input_name": "chargingStationPosition", "value": STATION}
    ]


def test_several_inputs_keep_their_order():
    inputs = mission_inputs({"a": "1", "mission_id": CHARGE, "b": "2"})
    assert [i["input_name"] for i in inputs] == ["a", "b"]


class _Connector:
    """The real custom-command handler bound to a stub carrying what it touches."""

    def __init__(self):
        self.results: list[tuple] = []
        self._logger = logging.getLogger("test")
        self.mir_api = Mock()
        self.mir_api.queue_mission = AsyncMock(return_value={"id": 6537})
        self.mir_api.abort_all_missions = AsyncMock()
        self.mission_executor = Mock()
        self.mission_executor.handle_command = AsyncMock(return_value=False)
        self.mission_tracking = Mock()

    def _record(self, code, *args, **kwargs):
        self.results.append((code, kwargs.get("execution_status_details")))

    async def run(self, args):
        bound = MirConnector._handle_custom_command.__get__(self, _Connector)
        await bound(args, {"result_function": self._record})


@pytest.mark.asyncio
async def test_queue_mission_passes_the_station_to_the_mir():
    c = _Connector()
    await c.run(["queue_mission", ["mission_id", CHARGE, "chargingStationPosition", STATION]])

    c.mir_api.queue_mission.assert_awaited_once_with(
        CHARGE, parameters=[{"input_name": "chargingStationPosition", "value": STATION}]
    )
    c.mission_tracking.add_managed_queue_id.assert_called_once_with(6537)
    assert c.results == [(CommandResultCode.SUCCESS, None)]


@pytest.mark.asyncio
async def test_queue_mission_without_inputs_is_unchanged():
    c = _Connector()
    await c.run(["queue_mission", ["mission_id", CHARGE]])

    c.mir_api.queue_mission.assert_awaited_once_with(CHARGE, parameters=None)
    assert c.results == [(CommandResultCode.SUCCESS, None)]


@pytest.mark.asyncio
async def test_run_mission_now_aborts_first_and_passes_inputs():
    c = _Connector()
    await c.run(["run_mission_now", ["mission_id", CHARGE, "chargingStationPosition", STATION]])

    c.mir_api.abort_all_missions.assert_awaited_once()
    c.mir_api.queue_mission.assert_awaited_once_with(
        CHARGE, parameters=[{"input_name": "chargingStationPosition", "value": STATION}]
    )
