# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""MiR REST API v2 client."""

import hashlib
import logging
import ssl as ssl_mod
from enum import Enum
from typing import Optional

import httpx
from prometheus_client import parser
from tenacity import (
    retry,
    wait_exponential_jitter,
    before_sleep_log,
    stop_after_attempt,
)

API_V2_CONTEXT_URL = "/api/v2.0.0"

logger = logging.getLogger(__name__)


def _should_retry(exception):
    """Retry on timeouts, connection errors, and server errors."""
    if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        code = exception.response.status_code
        return code >= 500 or code in [408, 429]
    return False


_retry_decorator = retry(
    wait=wait_exponential_jitter(initial=1, max=10),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    retry=_should_retry,
    reraise=True,
)


class SetStateId(int, Enum):
    READY = 3
    PAUSE = 4
    MANUALCONTROL = 11


class MirApi:
    """Async HTTP client for the MiR REST API v2."""

    def __init__(
        self,
        mir_host_address: str,
        mir_username: str,
        mir_password: str,
        mir_host_port: int = 80,
        mir_use_ssl: bool = False,
        verify_ssl: bool = True,
        ssl_ca_bundle: Optional[str] = None,
        ssl_verify_hostname: bool = True,
    ):
        self.logger = logging.getLogger(name=self.__class__.__name__)

        scheme = "https" if mir_use_ssl else "http"
        base_url = f"{scheme}://{mir_host_address}:{mir_host_port}{API_V2_CONTEXT_URL}"

        # SHA256 hashed password for MiR auth
        password_hash = hashlib.sha256(mir_password.encode()).hexdigest()
        auth = (mir_username, password_hash)

        # Configure SSL
        verify = self._configure_ssl(verify_ssl, ssl_ca_bundle, ssl_verify_hostname)

        if isinstance(verify, httpx.AsyncHTTPTransport):
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=30,
                auth=auth,
                headers={"Accept-Language": "en_US"},
                transport=verify,
            )
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=30,
                auth=auth,
                headers={"Accept-Language": "en_US"},
                verify=verify,
            )

        # Reduce httpx noise at INFO level
        if self.logger.getEffectiveLevel() == logging.INFO:
            logging.getLogger("httpx").setLevel(logging.WARNING)

    def _configure_ssl(self, verify_ssl, ssl_ca_bundle, ssl_verify_hostname):
        if not verify_ssl:
            self.logger.warning("SSL certificate verification is DISABLED.")
            return False
        elif not ssl_verify_hostname:
            ctx = ssl_mod.create_default_context()
            if ssl_ca_bundle:
                ctx.load_verify_locations(ssl_ca_bundle)
            ctx.check_hostname = False
            return httpx.AsyncHTTPTransport(verify=ctx)
        elif ssl_ca_bundle:
            return ssl_ca_bundle
        else:
            return True

    # -- HTTP helpers with retry ------------------------------------------

    @_retry_decorator
    async def _get(self, endpoint: str, **kwargs) -> httpx.Response:
        res = await self._client.get(endpoint, **kwargs)
        res.raise_for_status()
        return res

    @_retry_decorator
    async def _post(self, endpoint: str, **kwargs) -> httpx.Response:
        res = await self._client.post(endpoint, **kwargs)
        res.raise_for_status()
        return res

    @_retry_decorator
    async def _put(self, endpoint: str, **kwargs) -> httpx.Response:
        res = await self._client.put(endpoint, **kwargs)
        res.raise_for_status()
        return res

    @_retry_decorator
    async def _delete(self, endpoint: str, **kwargs) -> httpx.Response:
        res = await self._client.delete(endpoint, **kwargs)
        res.raise_for_status()
        return res

    async def close(self):
        await self._client.aclose()

    # -- Status -----------------------------------------------------------

    async def get_status(self) -> dict:
        return (await self._get("/status")).json()

    async def set_status(self, data: dict) -> dict:
        return (
            await self._put("/status", headers={"Content-Type": "application/json"}, json=data)
        ).json()

    async def set_state(self, state_id: int) -> dict:
        return await self.set_status({"state_id": state_id})

    async def clear_error(self):
        await self.set_status({"clear_error": True})
        await self.set_state(SetStateId.READY.value)

    # -- Metrics & diagnostics --------------------------------------------

    async def get_metrics(self) -> dict:
        text = (await self._get("/metrics")).text
        samples = {}
        for family in parser.text_string_to_metric_families(text):
            for sample in family.samples:
                samples[sample.name] = sample.value
        return samples

    async def get_diagnostics(self) -> dict:
        return (await self._get("experimental/diagnostics")).json()

    # -- Missions ---------------------------------------------------------

    async def queue_mission(
        self,
        mission_id: str,
        message: Optional[str] = None,
        parameters: Optional[list] = None,
        priority: int = 0,
        description: Optional[str] = None,
    ) -> dict:
        body = {"mission_id": mission_id}
        if message:
            body["message"] = message
        if parameters:
            body["parameters"] = parameters
        if priority:
            body["priority"] = priority
        if description:
            body["description"] = description
        resp = await self._post(
            "/mission_queue", headers={"Content-Type": "application/json"}, json=body
        )
        return resp.json()

    async def abort_all_missions(self):
        await self._delete("/mission_queue", headers={"Content-Type": "application/json"})

    async def get_missions_queue(self) -> list:
        return (await self._get("/mission_queue")).json()

    async def get_mission_queue_entry(self, queue_id: int) -> dict:
        """Return full details of a single mission queue entry."""
        return (await self._get(f"/mission_queue/{queue_id}")).json()

    async def get_executing_mission_id(self):
        """Return the queue ID of the currently executing mission, or None."""
        missions = await self.get_missions_queue()
        executing = [m for m in missions if m.get("state") == "Executing"]
        return executing[0]["id"] if executing else None

    async def get_mission(self, mission_queue_id) -> dict:
        mission = (await self._get(f"/mission_queue/{mission_queue_id}")).json()
        actions = (await self._get(f"/mission_queue/{mission_queue_id}/actions")).json()
        mission_id = mission["mission_id"]
        mission["definition"] = await self.get_mission_definition(mission_id)
        mission["actions"] = actions
        mission["definition"]["actions"] = await self.get_mission_actions(mission_id)
        return mission

    async def get_mission_definition(self, mission_id: str) -> dict:
        return (await self._get(f"/missions/{mission_id}")).json()

    async def get_mission_actions(self, mission_id: str) -> list:
        return (await self._get(f"/missions/{mission_id}/actions")).json()

    async def create_mission(self, group_id: str, name: str, **kwargs) -> dict:
        body = {"group_id": group_id, "name": name, **kwargs}
        return (
            await self._post("/missions", headers={"Content-Type": "application/json"}, json=body)
        ).json()

    async def add_action_to_mission(
        self, action_type: str, mission_id: str, parameters: list, priority: int, **kwargs
    ) -> dict:
        body = {
            "mission_id": mission_id,
            "action_type": action_type,
            "parameters": parameters,
            "priority": priority,
            **kwargs,
        }
        return (
            await self._post(
                f"/missions/{mission_id}/actions",
                headers={"Content-Type": "application/json"},
                json=body,
            )
        ).json()

    async def delete_mission_definition(self, mission_id: str):
        await self._delete(f"/missions/{mission_id}", headers={"Content-Type": "application/json"})

    # -- Mission groups ---------------------------------------------------

    async def get_mission_groups(self) -> list:
        return (await self._get("/mission_groups")).json()

    async def get_mission_group_missions(self, group_id: str) -> list:
        return (await self._get(f"/mission_groups/{group_id}/missions")).json()

    async def create_mission_group(self, feature, icon, name, priority, **kwargs) -> dict:
        body = {"feature": feature, "icon": icon, "name": name, "priority": priority, **kwargs}
        return (
            await self._post(
                "/mission_groups", headers={"Content-Type": "application/json"}, json=body
            )
        ).json()

    async def delete_mission_group(self, group_id: str):
        await self._delete(
            f"/mission_groups/{group_id}", headers={"Content-Type": "application/json"}
        )

    # -- Maps -------------------------------------------------------------

    async def get_map(self, map_id: str) -> dict:
        return (await self._get(f"maps/{map_id}")).json()
