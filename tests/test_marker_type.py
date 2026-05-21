# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Unit tests for docking marker_type auto-resolution (resolve_marker_type)."""

from __future__ import annotations

import logging

import pytest

from mir_connector.src.mir_api import resolve_marker_type

_LOG = logging.getLogger("test")

_MARKER = "00000000-0000-0000-0000-00000000aaaa"
_OFFSET = "00000000-0000-0000-0000-00000000bbbb"


class _FakeMirApi:
    """Minimal stub exposing get_position_docking_offsets."""

    def __init__(self, offsets=None, raises=None):
        self._offsets = offsets if offsets is not None else []
        self._raises = raises
        self.calls: list[str] = []

    async def get_position_docking_offsets(self, position_guid: str) -> list:
        self.calls.append(position_guid)
        if self._raises is not None:
            raise self._raises
        return self._offsets


@pytest.mark.asyncio
async def test_resolves_marker_type_when_offset_exists():
    api = _FakeMirApi(offsets=[{"guid": _OFFSET}])
    result = await resolve_marker_type(api, "docking", {"marker": _MARKER}, _LOG)
    assert result == {"marker": _MARKER, "marker_type": _OFFSET}
    assert api.calls == [_MARKER]


@pytest.mark.asyncio
async def test_omits_marker_type_when_no_offset():
    api = _FakeMirApi(offsets=[])
    result = await resolve_marker_type(api, "docking", {"marker": _MARKER}, _LOG)
    assert result == {"marker": _MARKER}
    assert "marker_type" not in result


@pytest.mark.asyncio
async def test_empty_marker_type_is_resolved():
    api = _FakeMirApi(offsets=[{"guid": _OFFSET}])
    result = await resolve_marker_type(api, "docking", {"marker": _MARKER, "marker_type": ""}, _LOG)
    assert result["marker_type"] == _OFFSET


@pytest.mark.asyncio
async def test_explicit_marker_type_passes_through():
    api = _FakeMirApi(offsets=[{"guid": _OFFSET}])
    params = {"marker": _MARKER, "marker_type": "explicit-guid"}
    result = await resolve_marker_type(api, "docking", params, _LOG)
    assert result == params
    assert api.calls == []  # already set — no lookup


@pytest.mark.asyncio
async def test_lookup_failure_falls_back_to_no_marker_type():
    api = _FakeMirApi(raises=RuntimeError("TCP reset"))
    result = await resolve_marker_type(api, "docking", {"marker": _MARKER, "retries": 10}, _LOG)
    assert result == {"marker": _MARKER, "retries": 10}
    assert "marker_type" not in result


@pytest.mark.asyncio
async def test_malformed_offset_response_falls_back():
    api = _FakeMirApi(offsets=[{"no_guid_field": 1}])
    result = await resolve_marker_type(api, "docking", {"marker": _MARKER}, _LOG)
    assert "marker_type" not in result


@pytest.mark.asyncio
async def test_non_docking_action_passes_through():
    api = _FakeMirApi(offsets=[{"guid": _OFFSET}])
    params = {"minimum_percentage": 95.0}
    result = await resolve_marker_type(api, "charging", params, _LOG)
    assert result == params
    assert api.calls == []


@pytest.mark.asyncio
async def test_docking_without_marker_passes_through():
    api = _FakeMirApi(offsets=[{"guid": _OFFSET}])
    params = {"retries": 10}
    result = await resolve_marker_type(api, "docking", params, _LOG)
    assert result == params
    assert api.calls == []
