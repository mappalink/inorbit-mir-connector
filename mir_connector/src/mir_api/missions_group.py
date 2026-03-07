# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Temporary mission group management for MiR waypoint navigation."""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod

import httpx
from tenacity import (
    retry,
    wait_exponential_jitter,
    before_sleep_log,
    retry_if_exception_type,
)

from .mir_api import MirApi


class MirMissionsGroupHandler(ABC):
    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass

    @abstractmethod
    async def setup_connector_missions(self):
        pass

    @abstractmethod
    async def cleanup_connector_missions(self):
        pass

    @property
    def missions_group_id(self) -> str | None:
        return None


class NullMissionsGroupHandler(MirMissionsGroupHandler):
    """No-op handler when temporary mission groups are disabled."""

    async def start(self):
        pass

    async def stop(self):
        pass

    async def setup_connector_missions(self):
        pass

    async def cleanup_connector_missions(self):
        pass


class TmpMissionsGroupHandler(MirMissionsGroupHandler):
    """Creates and manages a temporary mission group on the MiR robot."""

    MIR_INORBIT_MISSIONS_GROUP_NAME = "InOrbit Temporary Missions Group"
    MISSIONS_GARBAGE_COLLECTION_INTERVAL_SECS = 6 * 60 * 60

    def __init__(self, mir_api: MirApi):
        self.mir_api = mir_api
        self._logger = logging.getLogger(name=self.__class__.__name__)
        self._missions_group_id = None
        self._missions_group_id_lock = asyncio.Lock()
        self._bg_tasks: list[asyncio.Task] = []

    @property
    def missions_group_id(self) -> str | None:
        return self._missions_group_id

    async def start(self):
        self._bg_tasks.append(asyncio.create_task(self.setup_connector_missions()))
        self._bg_tasks.append(asyncio.create_task(self._missions_garbage_collector()))

    async def stop(self):
        for task in self._bg_tasks:
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

    @retry(
        wait=wait_exponential_jitter(max=10),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def setup_connector_missions(self):
        async with self._missions_group_id_lock:
            if self._missions_group_id is not None:
                return

        self._logger.info("Setting up connector missions")
        mission_groups = await self.mir_api.get_mission_groups()
        group = next(
            (x for x in mission_groups if x["name"] == self.MIR_INORBIT_MISSIONS_GROUP_NAME),
            None,
        )
        self._missions_group_id = group["guid"] if group is not None else str(uuid.uuid4())
        if group is None:
            self._logger.info(f"Creating mission group '{self.MIR_INORBIT_MISSIONS_GROUP_NAME}'")
            await self.mir_api.create_mission_group(
                feature=".",
                icon=".",
                name=self.MIR_INORBIT_MISSIONS_GROUP_NAME,
                priority=0,
                guid=self._missions_group_id,
            )
            self._logger.info(f"Mission group created: {self._missions_group_id}")
        else:
            self._logger.info(f"Found existing mission group: {self._missions_group_id}")

    async def cleanup_connector_missions(self):
        async with self._missions_group_id_lock:
            if self._missions_group_id is None:
                self._missions_group_id = ""
                return
        self._logger.info(f"Deleting missions group {self._missions_group_id}")
        await self.mir_api.delete_mission_group(self._missions_group_id)

    async def _delete_unused_missions(self):
        try:
            mission_defs = await self.mir_api.get_mission_group_missions(self._missions_group_id)
            missions_queue = await self.mir_api.get_missions_queue()
            protected = [
                (await self.mir_api.get_mission(m["id"]))["mission_id"]
                for m in missions_queue
                if m["state"].lower() in ["pending", "executing"]
            ]
            for mission_def in mission_defs:
                if mission_def["guid"] not in protected:
                    try:
                        await self.mir_api.delete_mission_definition(mission_def["guid"])
                    except Exception as ex:
                        self._logger.error(f"Failed to delete mission {mission_def['guid']}: {ex}")
        except Exception as ex:
            self._logger.error(f"Failed garbage collection: {ex}")

    async def _missions_garbage_collector(self):
        while True:
            await asyncio.sleep(self.MISSIONS_GARBAGE_COLLECTION_INTERVAL_SECS)
            await self._delete_unused_missions()
