# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Utility functions for MiR connector data processing."""

import re
from typing import Optional


def to_inorbit_percent(value: float) -> float:
    """Convert percentage (0-100) to InOrbit format (0-1)."""
    return max(0.0, min(100.0, value)) / 100.0


def parse_number(value: object) -> Optional[float]:
    """Parse a numeric value from various input types."""
    try:
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if not s:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None
    except (ValueError, AttributeError):
        return None


def to_gb(val: object, key: str) -> Optional[float]:
    """Convert a value to gigabytes based on unit indicators in the key."""
    n = parse_number(val)
    if n is None:
        return None
    k = key.lower()
    if "[gb]" in k:
        return n
    if "[mb]" in k:
        return n / 1024.0
    if "[b]" in k:
        return n / (1024.0 * 1024.0 * 1024.0)
    return n


def calculate_usage_percent(diagnostic_values: dict, key_name: str) -> Optional[float]:
    """Calculate usage percentage from diagnostic total/used/free values."""
    total_gb = used_gb = free_gb = None
    for k, v in diagnostic_values.items():
        if "Total size" in k:
            total_gb = to_gb(v, k)
        elif "Used" in k:
            used_gb = to_gb(v, k)
        elif "Free" in k:
            free_gb = to_gb(v, k)

    if total_gb and total_gb > 0:
        if used_gb is not None:
            return (used_gb / total_gb) * 100.0
        elif free_gb is not None:
            return ((total_gb - free_gb) / total_gb) * 100.0
    return None
