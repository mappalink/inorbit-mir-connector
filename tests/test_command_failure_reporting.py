# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""A command that raises must report FAILURE to InOrbit, never silence.

Regression from the FM lab, 2026-09-03. `goto_position` raised
`RemoteProtocolError: Server disconnected without sending a response` inside the
handler. The exception escaped, no result function was ever called, and InOrbit
recorded the mission as completed/OK while the robot never moved — a silent
false pass in a customer-facing script.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from inorbit_connector.connector import CommandResultCode
from mir_connector.src.connector import MirConnector


class _Handler:
    """Bind the real handler to a stub carrying only what it touches."""

    def __init__(self, dispatch_side_effect=None):
        self.results: list[tuple] = []
        self._logger = logging.getLogger("test")
        self._dispatch = AsyncMock(side_effect=dispatch_side_effect)

    def _record(self, code, *args, **kwargs):
        self.results.append((code, kwargs.get("execution_status_details")))

    async def run(self, command_name="customCommand", args=None):
        options = {"result_function": self._record}
        bound = MirConnector._inorbit_command_handler.__get__(self, _Handler)
        self._dispatch_inorbit_command = self._dispatch
        return await bound(command_name, args or [], options)


@pytest.mark.asyncio
async def test_raising_command_reports_failure():
    boom = ConnectionError("Server disconnected without sending a response")
    h = _Handler(dispatch_side_effect=boom)

    with pytest.raises(ConnectionError):
        await h.run(args=["goto_position", ["position_guid", "abc"]])

    assert len(h.results) == 1
    code, details = h.results[0]
    assert code == CommandResultCode.FAILURE
    assert "ConnectionError" in details
    assert "Server disconnected" in details


@pytest.mark.asyncio
async def test_successful_command_reports_nothing_extra():
    """The wrapper must not invent a result when the branch reports its own."""
    h = _Handler()

    async def dispatch(command_name, args, options):
        options["result_function"](CommandResultCode.SUCCESS)

    h._dispatch = AsyncMock(side_effect=dispatch)
    await h.run()

    assert h.results == [(CommandResultCode.SUCCESS, None)]


@pytest.mark.asyncio
async def test_failure_already_reported_is_not_reported_twice():
    """A branch that reports FAILURE then raises must yield exactly one result."""

    async def dispatch(command_name, args, options):
        options["result_function"](
            CommandResultCode.FAILURE, execution_status_details="Invalid arguments"
        )
        raise RuntimeError("and then it blew up")

    h = _Handler()
    h._dispatch = AsyncMock(side_effect=dispatch)

    with pytest.raises(RuntimeError):
        await h.run()

    assert h.results == [(CommandResultCode.FAILURE, "Invalid arguments")]
