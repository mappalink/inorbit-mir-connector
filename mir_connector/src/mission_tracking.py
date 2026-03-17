# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Native MiR mission tracking — reports MiR-native mission progress to InOrbit.

Polls the MiR mission queue to find executing missions and publishes their
state as ``mission_tracking`` key-value events so they appear in the InOrbit UI.

Deduplication: the connector tracks which MiR queue entries it created on
behalf of InOrbit (edge compiled missions, cloud NAV_GOAL waypoints,
queue_mission commands). Native tracking skips those entries so InOrbit
doesn't see the same mission twice — once from its own executor and once
from native tracking.
"""

import logging
from datetime import datetime

from inorbit_edge.missions import MISSION_STATE_EXECUTING, MISSION_STATE_ABORTED

# MiR mission queue states
MISSION_STATE_DONE = "Done"
MISSION_STATE_ABORT = "Abort"


class MirMissionTracking:
    def __init__(self, mir_api, inorbit_session, robot_tz_info):
        self.logger = logging.getLogger(name=self.__class__.__name__)
        self.mir_api = mir_api
        self.inorbit_session = inorbit_session
        self.robot_tz_info = robot_tz_info

        # Disabled while an InOrbit edge-executor mission is running
        self.mir_mission_tracking_enabled = True

        # MiR queue entry IDs created by the connector on behalf of InOrbit.
        # Native tracking skips these to avoid duplicate reporting.
        self._managed_queue_ids: set[int] = set()

        # Custom text for waitUntil expressions, set via set_waiting_for command
        self.waiting_for_text = ""

        self.executing_mission_id = None
        self.last_reported_mission_id = None
        self.last_reported_mission_progress = 0.0

    def add_managed_queue_id(self, queue_id):
        """Register a MiR queue entry as managed by InOrbit (skip in native tracking)."""
        if queue_id is not None:
            self._managed_queue_ids.add(queue_id)
            self.logger.info(f"Registered InOrbit-managed queue entry: {queue_id}")

    def clear_managed_queue_ids(self):
        """Clear managed queue IDs. Called when MiR returns to idle."""
        if self._managed_queue_ids:
            self.logger.debug(f"Cleared {len(self._managed_queue_ids)} managed queue entries")
            self._managed_queue_ids.clear()

    def _safe_localize_timestamp(self, timestamp_str: str) -> float:
        """Convert ISO timestamp string to Unix timestamp."""
        try:
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is not None:
                return dt.timestamp()
            return self.robot_tz_info.localize(dt).timestamp()
        except Exception as e:
            self.logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return datetime.now().timestamp()

    async def get_current_mission(self):
        """Return the current mission (executing or just ended)."""
        if self.executing_mission_id is None:
            self.executing_mission_id = await self.mir_api.get_executing_mission_id()
        if self.executing_mission_id:
            mission = await self.mir_api.get_mission(self.executing_mission_id)
            if mission["state"] != MISSION_STATE_EXECUTING:
                # Next call will look for a new executing mission
                self.executing_mission_id = None
            return mission
        return None

    async def report_mission(self, status, metrics):
        """Poll and report the current MiR-native mission to InOrbit."""
        if not self.mir_mission_tracking_enabled:
            return

        mission = await self.get_current_mission()
        if not mission:
            return

        # Skip missions created by the connector on behalf of InOrbit
        if mission["id"] in self._managed_queue_ids:
            self.logger.debug(
                f"Skipping managed mission queue entry {mission['id']} "
                f"(managed: {self._managed_queue_ids})"
            )
            return

        completed_percent = len(mission["actions"]) / len(mission["definition"]["actions"])

        # Normalize Abort → Aborted
        if mission["state"] == MISSION_STATE_ABORT:
            mission["state"] = MISSION_STATE_ABORTED

        # Avoid flooding when nothing changed
        if (
            mission["id"] == self.last_reported_mission_id
            and mission["state"] == MISSION_STATE_EXECUTING
            and completed_percent == self.last_reported_mission_progress
        ):
            return

        mission_values = {
            "missionId": mission["id"],
            "inProgress": mission["state"] == MISSION_STATE_EXECUTING,
            "state": mission["state"],
            "label": mission["definition"]["name"],
            "startTs": self._safe_localize_timestamp(mission["started"]) * 1000,
            "data": {
                "Total Distance (m)": metrics.get("mir_robot_distance_moved_meters_total", "N/A"),
                "Mission Steps": len(mission["definition"]["actions"]),
                "Total Missions": mission["id"],
                "Robot Model": status.get("robot_model", "N/A"),
                "Uptime (s)": status.get("uptime", "N/A"),
                "Serial Number": status.get("serial_number", "N/A"),
                "Battery Time Remaining (s)": status.get("battery_time_remaining", "N/A"),
                "WiFi RSSI (dbm)": metrics.get("mir_robot_wifi_access_point_rssi_dbm", "N/A"),
            },
        }

        if mission.get("finished") is not None:
            mission_values["endTs"] = self._safe_localize_timestamp(mission["finished"]) * 1000
            mission_values["completedPercent"] = 1
            mission_values["status"] = "OK" if mission["state"] == MISSION_STATE_DONE else "error"
        else:
            mission_values["completedPercent"] = completed_percent

        self.logger.debug(f"Reporting mission: {mission_values}")
        self.inorbit_session.publish_key_values(
            key_values={"mission_tracking": mission_values}, is_event=True
        )
        self.last_reported_mission_progress = completed_percent
        self.last_reported_mission_id = mission["id"]
