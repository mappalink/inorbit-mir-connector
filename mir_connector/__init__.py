# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Top-level package for InOrbit MiR Connector."""

from importlib import metadata

__author__ = """InOrbit Inc."""
__email__ = "info@mappalink.com"
# Read the installed package version from metadata
try:
    __version__ = metadata.version("mir-connector")
except metadata.PackageNotFoundError:
    __version__ = "unknown"
