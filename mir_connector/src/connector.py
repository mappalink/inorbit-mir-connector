# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""MiR single-robot connector for InOrbit."""

import base64
import math
import uuid

import pytz
from inorbit_connector.connector import Connector, CommandResultCode
from inorbit_connector.models import MapConfigTemp
from inorbit_edge.robot import COMMAND_CUSTOM_COMMAND, COMMAND_MESSAGE, COMMAND_NAV_GOAL

from mir_connector import __version__ as connector_version
from mir_connector.src.config.models import ConnectorConfig
from mir_connector.src.mir_api import MirApi, SetStateId
from mir_connector.src.mir_api.missions_group import (
    NullMissionsGroupHandler,
    TmpMissionsGroupHandler,
)
from mir_connector.src.mission_exec import MirMissionExecutor
from mir_connector.src.mission_tracking import MirMissionTracking
from mir_connector.src.robot.robot import Robot

from inorbit_edge_executor.inorbit import InOrbitAPI
from mir_connector.src.utils import to_inorbit_percent, calculate_usage_percent

# Available MiR states to select via actions
MIR_STATE = {3: "READY", 4: "PAUSE", 11: "MANUALCONTROL"}

# Distance threshold for MiR move missions in meters
MIR_MOVE_DISTANCE_THRESHOLD = 0.1

# Diagnostic paths for robot vitals
BATTERY_PATH = "/Power System/Battery"
CPU_LOAD_PATH = "/Computer/PC/CPU Load"
CPU_TEMP_PATH = "/Computer/PC/CPU Temperature"
MEMORY_PATH = "/Computer/PC/Memory"
HARDDRIVE_PATH = "/Computer/PC/Harddrive"
WIFI_PATH = "/Computer/Network/Wifi"


class MirConnector(Connector):
    """Connector between a MiR robot and InOrbit.

    One instance per robot, one process per robot.
    """

    def __init__(self, robot_id: str, config: ConnectorConfig) -> None:
        super().__init__(
            robot_id=robot_id,
            config=config,
            register_user_scripts=True,
            create_user_scripts_dir=True,
        )
        self.config = config

        # MiR REST API client
        self.mir_api = MirApi(
            mir_host_address=config.connector_config.mir_host_address,
            mir_username=config.connector_config.mir_username,
            mir_password=config.connector_config.mir_password,
            mir_host_port=config.connector_config.mir_host_port,
            mir_use_ssl=config.connector_config.mir_use_ssl,
            verify_ssl=config.connector_config.verify_ssl,
            ssl_ca_bundle=config.connector_config.ssl_ca_bundle,
            ssl_verify_hostname=config.connector_config.ssl_verify_hostname,
        )

        # Async robot wrapper managing polling loops
        # Diagnostics endpoint does not exist on v2 firmware
        enable_diagnostics = config.connector_config.mir_firmware_version != "v2"
        self.robot = Robot(
            mir_api=self.mir_api,
            default_update_freq=1.0,
            enable_diagnostics=enable_diagnostics,
        )

        # Timezone
        self.robot_tz_info = pytz.timezone("UTC")
        try:
            self.robot_tz_info = pytz.timezone(config.location_tz)
        except pytz.exceptions.UnknownTimeZoneError as ex:
            self._logger.error(
                f"Unknown timezone: '{config.location_tz}', defaulting to 'UTC'. {ex}"
            )

        # Native MiR mission tracking (reports MiR-native missions to InOrbit UI)
        self.mission_tracking = MirMissionTracking(
            mir_api=self.mir_api,
            inorbit_session=self._get_session(),
            robot_tz_info=self.robot_tz_info,
        )

        # Temporary mission groups for waypoint navigation
        if config.connector_config.enable_temporary_mission_group:
            self.mission_group = TmpMissionsGroupHandler(mir_api=self.mir_api)
        else:
            self.mission_group = NullMissionsGroupHandler()

        # Edge mission executor
        self.mission_executor = MirMissionExecutor(
            robot_id=robot_id,
            inorbit_api=InOrbitAPI(
                base_url=self._get_session().inorbit_rest_api_endpoint,
                api_key=config.api_key,
            ),
            mir_api=self.mir_api,
            database_file=config.connector_config.mission_database_file,
            missions_group=self.mission_group,
            firmware_version=config.connector_config.mir_firmware_version,
            connector_type=config.connector_type,
        )

        # Initialize status as None to prevent publishing before the robot is connected
        self.status = None

    def _is_robot_online(self) -> bool:
        return self.robot.api_connected

    async def _connect(self) -> None:
        self.robot.start()
        await self.mission_group.start()
        await self.mission_executor.initialize()

    async def _disconnect(self) -> None:
        await self.mission_group.cleanup_connector_missions()
        await self.mission_group.stop()
        await self.robot.stop()
        await self.mir_api.close()
        try:
            await self.mission_executor.shutdown()
            self._logger.info("Mission executor shut down successfully")
        except Exception as e:
            self._logger.error(f"Error shutting down mission executor: {e}")

    async def _execution_loop(self) -> None:
        status = self.robot.status
        if not status and self.status is None:
            return
        self.status = status
        self.metrics = self.robot.metrics
        self.diagnostics = self.robot.diagnostics

        # Check if the InOrbit edge executor is idle
        executor_idle = self._get_session().missions_module.executor.wait_until_idle(0)

        # Re-enable native mission tracking when edge executor is idle
        if executor_idle:
            self.mission_tracking.mir_mission_tracking_enabled = True

        # Pose (degrees -> radians)
        self.publish_pose(
            x=self.status.get("position", {}).get("x", 0),
            y=self.status.get("position", {}).get("y", 0),
            yaw=math.radians(self.status.get("position", {}).get("orientation", 0)),
            frame_id=self.status.get("map_id", ""),
        )

        # Odometry
        self.publish_odometry(
            linear_speed=self.status.get("velocity", {}).get("linear", 0),
            angular_speed=math.radians(self.status.get("velocity", {}).get("angular", 0)),
        )

        # Override mission/state/mode text when edge executor is active so the
        # InOrbit UI shows consistent "Executing" instead of flickering between
        # MiR-native states during multi-waypoint compiled missions.
        if executor_idle:
            mode_text = self.status.get("mode_text")
            state_text = self.status.get("state_text")
            mission_text = self.status.get("mission_text")
        else:
            mode_text = "Mission"
            state_text = "Executing"
            mission_text = "Mission"

        # Key values
        key_values = {
            "connector_version": connector_version,
            "robot_name": self.status.get("robot_name"),
            "serial_number": self.status.get("serial_number"),
            "errors": self.status.get("errors"),
            "distance_to_next_target": self.status.get("distance_to_next_target"),
            "mission_text": mission_text,
            "state_text": state_text,
            "state_id": self.status.get("state_id"),
            "mode_text": mode_text,
            "mode_id": self.status.get("mode_id"),
            "robot_model": self.status.get("robot_model"),
            "moved": self.status.get("moved"),
            "safety_system_muted": self.status.get("safety_system_muted"),
            "uptime": self.status.get("uptime"),
            "waiting_for": self.mission_tracking.waiting_for_text,
            "api_connected": self.robot.api_connected,
        }

        # Localization score from metrics
        key_values["localization_score"] = (self.metrics or {}).get("mir_robot_localization_score")

        # System stats
        system_stats = {}
        diagnostics = self.diagnostics or {}
        try:
            self._parse_diagnostics(key_values, system_stats, diagnostics)
        except Exception:
            self._logger.debug("Failed to parse diagnostics vitals", exc_info=True)

        # Log API connection state changes
        if hasattr(self, "_last_api_connected") and self._last_api_connected != key_values.get(
            "api_connected"
        ):
            self._logger.info(f"API connection status changed: {key_values.get('api_connected')}")
        self._last_api_connected = key_values.get("api_connected")

        self.publish_key_values(**key_values)
        if system_stats:
            self.publish_system_stats(**system_stats)

        # Report native MiR mission progress to InOrbit
        try:
            await self.mission_tracking.report_mission(self.status, self.metrics or {})
        except Exception:
            self._logger.debug("Error reporting mission", exc_info=True)

    def _parse_diagnostics(self, key_values: dict, system_stats: dict, diagnostics: dict) -> None:
        """Extract vitals from diagnostics (preferred) or status (fallback)."""
        # Battery
        batt_vals = (diagnostics.get(BATTERY_PATH, {}) or {}).get("values", {})
        remaining_pct = None
        remaining_sec = None
        for k, v in batt_vals.items():
            if "Remaining battery capacity" in k:
                remaining_pct = float(v)
            elif "Remaining battery time [sec]" in k:
                remaining_sec = float(v)
        # Fallback to status for v2 firmware
        if remaining_pct is None and self.status.get("battery_percentage") is not None:
            remaining_pct = float(self.status.get("battery_percentage"))
        if remaining_sec is None and self.status.get("battery_time_remaining") is not None:
            remaining_sec = float(self.status.get("battery_time_remaining"))
        if remaining_pct is not None:
            key_values["battery percent"] = to_inorbit_percent(remaining_pct)
        if remaining_sec is not None:
            key_values["battery_time_remaining"] = int(remaining_sec)

        # CPU load
        cpu_vals = (diagnostics.get(CPU_LOAD_PATH, {}) or {}).get("values", {})
        for k, v in cpu_vals.items():
            if "Average CPU load" in k and "30 second" not in k and "3 minute" not in k:
                system_stats["cpu_load_percentage"] = to_inorbit_percent(float(v))
                break

        # CPU temperature
        cpu_temp_vals = (diagnostics.get(CPU_TEMP_PATH, {}) or {}).get("values", {})
        for k, v in cpu_temp_vals.items():
            if "Package id" in k:
                key_values["temperature_celsius"] = float(v)
                break

        # Memory
        memory_vals = (diagnostics.get(MEMORY_PATH, {}) or {}).get("values", {})
        memory_usage_pct = calculate_usage_percent(memory_vals, "memory_usage_percent")
        if memory_usage_pct is not None:
            system_stats["ram_usage_percentage"] = to_inorbit_percent(memory_usage_pct)

        # Disk
        disk_vals = (diagnostics.get(HARDDRIVE_PATH, {}) or {}).get("values", {})
        disk_usage_pct = calculate_usage_percent(disk_vals, "disk_usage_percent")
        if disk_usage_pct is not None:
            system_stats["hdd_usage_percentage"] = to_inorbit_percent(disk_usage_pct)

        # WiFi
        wifi_vals = (diagnostics.get(WIFI_PATH, {}) or {}).get("values", {})
        if ssid := wifi_vals.get("SSID"):
            key_values["wifi_ssid"] = ssid
        if freq := wifi_vals.get("Frequency"):
            try:
                key_values["wifi_frequency_mhz"] = float(freq)
            except (ValueError, TypeError):
                pass
        if signal := wifi_vals.get("Signal level"):
            try:
                key_values["wifi_signal_dbm"] = float(signal)
            except (ValueError, TypeError):
                pass

    async def _inorbit_command_handler(self, command_name, args, options):
        self._logger.info(f"Received command '{command_name}' with {len(args)} arguments")
        self._logger.debug(f"Command details: {command_name} - {args}")

        if command_name == COMMAND_CUSTOM_COMMAND:
            await self._handle_custom_command(args, options)

        elif command_name == COMMAND_NAV_GOAL:
            pose = args[0]
            if self.config.connector_config.enable_temporary_mission_group:
                await self._send_waypoint_over_missions(pose)
            elif mission_id := self.config.connector_config.default_waypoint_mission_id:
                x, y = float(pose["x"]), float(pose["y"])
                orientation = math.degrees(float(pose["theta"]))
                await self.mir_api.abort_all_missions()
                await self.mir_api.queue_mission(
                    mission_id,
                    message="InOrbit Waypoint",
                    parameters=[
                        {"id": "X", "value": x, "label": f"{x}"},
                        {"id": "Y", "value": y, "label": f"{y}"},
                        {"id": "Orientation", "value": orientation, "label": f"{orientation}"},
                    ],
                    description="Mission created by InOrbit",
                )
            else:
                self._logger.error("No waypoint mission id or temporary missions group enabled")
                options["result_function"](
                    CommandResultCode.FAILURE,
                    execution_status_details=(
                        "No waypoint mission id or temporary missions group enabled"
                    ),
                )

        elif command_name == COMMAND_MESSAGE:
            msg = args[0]
            if msg == "inorbit_pause":
                await self.mir_api.set_state(SetStateId.PAUSE.value)
            elif msg == "inorbit_resume":
                await self.mir_api.set_state(SetStateId.READY.value)

        else:
            self._logger.warning(f"Received unknown command '{command_name}' - ignoring")

    async def _handle_custom_command(self, args, options):
        result_fn = options["result_function"]

        if len(args) < 2:
            self._logger.error(f"Invalid argument count: expected >=2, got {len(args)}")
            result_fn(CommandResultCode.FAILURE, execution_status_details="Invalid arguments")
            return

        script_name = args[0]
        args_raw = list(args[1])

        if not (
            isinstance(args_raw, list)
            and len(args_raw) % 2 == 0
            and all(isinstance(key, str) for key in args_raw[::2])
        ):
            result_fn(CommandResultCode.FAILURE, execution_status_details="Invalid arguments")
            return

        script_args = dict(zip(args_raw[::2], args_raw[1::2]))
        self._logger.debug(f"Parsed arguments: {script_args}")

        # Try edge-executor first (handles executeMissionAction, cancelMissionAction, etc.)
        handled = await self.mission_executor.handle_command(script_name, script_args, options)
        if handled:
            # Disable native mission tracking while edge executor is active
            self.mission_tracking.mir_mission_tracking_enabled = False
            return

        if script_name == "queue_mission" and "--mission_id" in script_args:
            self.mission_tracking.mir_mission_tracking_enabled = (
                self._get_session().missions_module.executor.wait_until_idle(0)
            )
            await self.mir_api.queue_mission(script_args["--mission_id"])

        elif script_name == "run_mission_now" and "--mission_id" in script_args:
            self.mission_tracking.mir_mission_tracking_enabled = (
                self._get_session().missions_module.executor.wait_until_idle(0)
            )
            await self.mir_api.abort_all_missions()
            await self.mir_api.queue_mission(script_args["--mission_id"])

        elif script_name == "abort_missions":
            self._get_session().missions_module.executor.cancel_mission("*")
            await self.mir_api.abort_all_missions()

        elif script_name == "set_state" and "--state_id" in script_args:
            state_id = script_args["--state_id"]
            if not state_id.isdigit() or int(state_id) not in MIR_STATE:
                result_fn(
                    CommandResultCode.FAILURE,
                    execution_status_details=f"Invalid state_id '{state_id}'",
                )
                return
            await self.mir_api.set_state(int(state_id))

        elif script_name == "set_state" and "--clear_error" in script_args:
            await self.mir_api.clear_error()

        elif script_name == "set_waiting_for" and "--text" in script_args:
            self._logger.info(f"Setting 'waiting for' value to {script_args['--text']}")
            self.mission_tracking.waiting_for_text = script_args["--text"]

        elif script_name == "localize":
            if all(k in script_args for k in ["--x", "--y", "--orientation", "--map_id"]):
                status = {
                    "position": {
                        "x": float(script_args["--x"]),
                        "y": float(script_args["--y"]),
                        "orientation": float(script_args["--orientation"]),
                    },
                    "map_id": script_args["--map_id"],
                }
                self._logger.info(f"Changing map to {script_args['--map_id']}")
                await self.mir_api.set_status(status)
            else:
                result_fn(
                    CommandResultCode.FAILURE,
                    execution_status_details="Invalid arguments for localize",
                )
                return

        else:
            # Unknown custom commands may be handled by the edge-sdk (e.g. user_scripts)
            return

        result_fn(CommandResultCode.SUCCESS)

    async def _send_waypoint_over_missions(self, pose):
        """Create a temporary move mission and queue it."""
        mission_id = str(uuid.uuid4())
        firmware_version = self.config.connector_config.mir_firmware_version

        if not self.mission_group.missions_group_id:
            try:
                await self.mission_group.setup_connector_missions()
            except Exception as ex:
                self._logger.error(f"Failed to setup connector missions: {ex}")
            if not self.mission_group.missions_group_id:
                raise Exception("Connector missions group not set up")

        await self.mir_api.create_mission(
            group_id=self.mission_group.missions_group_id,
            name="Move to waypoint",
            guid=mission_id,
            description="Mission created by InOrbit",
        )

        param_values = {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "orientation": math.degrees(float(pose["theta"])),
            "distance_threshold": MIR_MOVE_DISTANCE_THRESHOLD,
        }
        # Firmware-specific parameters
        if firmware_version == "v2":
            param_values["retries"] = 5
        else:
            param_values["blocked_path_timeout"] = 60.0

        action_parameters = [
            {"value": v, "input_name": None, "guid": str(uuid.uuid4()), "id": k}
            for k, v in param_values.items()
        ]
        await self.mir_api.add_action_to_mission(
            action_type="move_to_position",
            mission_id=mission_id,
            parameters=action_parameters,
            priority=1,
        )
        await self.mir_api.queue_mission(mission_id)

    async def fetch_map(self, frame_id: str) -> MapConfigTemp | None:
        """Fetch a map from the MiR robot API."""
        self._logger.info(f"Fetching map '{frame_id}' from robot")
        try:
            map_data = await self.mir_api.get_map(frame_id)
            if not map_data:
                self._logger.warning(f"No map data received for {frame_id}")
                return None

            # Field name differs by firmware: v2 uses "map", v3 uses "base_map"
            firmware_version = self.config.connector_config.mir_firmware_version
            map_field = "map" if firmware_version == "v2" else "base_map"
            image_b64 = map_data.get(map_field)
            map_label = map_data.get("name")
            resolution = map_data.get("resolution")
            origin_x = map_data.get("origin_x")
            origin_y = map_data.get("origin_y")

            if (
                not isinstance(image_b64, str)
                or not isinstance(resolution, (int, float))
                or not isinstance(origin_x, (int, float))
                or not isinstance(origin_y, (int, float))
            ):
                self._logger.error(f"Incomplete map data for {frame_id}")
                return None

            map_bytes = base64.b64decode(image_b64)
            return MapConfigTemp(
                image=map_bytes,
                map_id=frame_id,
                map_label=map_label if isinstance(map_label, str) else None,
                origin_x=float(origin_x),
                origin_y=float(origin_y),
                resolution=float(resolution),
            )
        except Exception as ex:
            self._logger.error(f"Failed to fetch map '{frame_id}' from robot: {ex}")
            return None
