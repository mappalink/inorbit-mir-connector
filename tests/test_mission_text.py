# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Unit tests for format_mir_text: MiR message templates become readable text."""

from __future__ import annotations

from mir_connector.src.utils import format_mir_text

# mission_text exactly as mir200-1 (firmware 2.x) reported it on 2026-09-30:
# a message template plus its arguments, with the quotes backslash-escaped.
LIVE_MIR200 = (
    r"{\"message\": \"Charging failed to start - Last voltage measurement: "
    r"%(last_measurement).2f V)\", \"args\": {\"last_measurement\":27.442}}"
)


def test_template_with_escaped_quotes_is_rendered():
    assert (
        format_mir_text(LIVE_MIR200)
        == "Charging failed to start - Last voltage measurement: 27.44 V)"
    )


def test_template_as_plain_json_is_rendered():
    text = '{"message": "Docking to %(name)s failed", "args": {"name": "Charger"}}'
    assert format_mir_text(text) == "Docking to Charger failed"


def test_plain_text_is_left_alone():
    assert format_mir_text("Waiting for new missions...") == "Waiting for new missions..."


def test_message_without_args_is_the_message():
    assert format_mir_text('{"message": "Emergency stop pressed"}') == "Emergency stop pressed"


def test_missing_argument_falls_back_to_the_unformatted_message():
    text = '{"message": "Voltage %(volts).1f V", "args": {}}'
    assert format_mir_text(text) == "Voltage %(volts).1f V"


def test_json_that_is_not_a_message_is_left_alone():
    assert format_mir_text('{"a": 1}') == '{"a": 1}'


def test_none_stays_none():
    assert format_mir_text(None) is None
