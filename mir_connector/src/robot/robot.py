# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Async polling wrapper for MiR robot status, metrics, and diagnostics."""

import asyncio
import logging
import time
from typing import Coroutine

from mir_connector.src.mir_api.mir_api import MirApi


class Robot:
    """Manages async polling loops for different MiR API data sources.

    Each endpoint is polled at its own frequency. Property accessors
    return the latest fetched data.
    """

    def __init__(
        self,
        mir_api: MirApi,
        default_update_freq: float = 1.0,
        enable_diagnostics: bool = True,
    ):
        self.logger = logging.getLogger(name=self.__class__.__name__)
        self._mir_api = mir_api
        self._stop_event = asyncio.Event()
        self._status: dict = {}
        self._metrics: dict = {}
        self._diagnostics: dict = {}
        self._default_update_freq = default_update_freq
        self._running_tasks: list[asyncio.Task] = []
        self._last_call_successful: bool = True
        self._enable_diagnostics = enable_diagnostics

        # Circuit breaker
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5
        self._backoff_time = 1.0
        self._max_backoff_time = 30.0
        self._last_error_time = 0

    def start(self) -> None:
        self.logger.info("Starting polling loops")
        self._run_in_loop(self._update_status)
        self._run_in_loop(self._update_metrics, frequency=0.5)
        if self._enable_diagnostics:
            self._run_in_loop(self._update_diagnostics, frequency=0.5)

    async def stop(self) -> None:
        self.logger.info("Stopping polling loops")
        self._stop_event.set()
        if self._running_tasks:
            try:
                done, pending = await asyncio.wait(
                    self._running_tasks, timeout=1.0, return_when=asyncio.ALL_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=0.5)
            except Exception as e:
                self.logger.error(f"Error during shutdown: {e}")
        self._running_tasks.clear()

    async def _update_status(self) -> None:
        try:
            self._status = await self._mir_api.get_status()
            self._handle_success()
        except Exception as e:
            self._handle_error(e, "status")

    async def _update_metrics(self) -> None:
        try:
            self._metrics = await self._mir_api.get_metrics()
            self._handle_success()
        except Exception as e:
            self._handle_error(e, "metrics")

    async def _update_diagnostics(self) -> None:
        try:
            self._diagnostics = await self._mir_api.get_diagnostics()
            self._handle_success()
        except Exception as e:
            self._handle_error(e, "diagnostics")

    @property
    def status(self) -> dict:
        return self._status

    @property
    def metrics(self) -> dict:
        return self._metrics

    @property
    def diagnostics(self) -> dict:
        return self._diagnostics

    @property
    def api_connected(self) -> bool:
        return self._last_call_successful

    def _run_in_loop(self, coro: Coroutine, frequency: float | None = None) -> None:
        async def loop():
            try:
                while not self._stop_event.is_set():
                    try:
                        if self._stop_event.is_set():
                            break
                        if self._consecutive_errors >= self._max_consecutive_errors:
                            if time.time() - self._last_error_time < self._backoff_time:
                                await asyncio.sleep(self._backoff_time)
                                continue
                        await asyncio.gather(
                            coro(),
                            asyncio.sleep(1 / (frequency or self._default_update_freq)),
                        )
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        self.logger.error(f"Error in loop {coro.__name__}: {e}")
                        await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                pass

        self._running_tasks.append(asyncio.create_task(loop()))

    def _handle_success(self) -> None:
        if self._consecutive_errors > 0:
            self.logger.info(f"API recovered after {self._consecutive_errors} consecutive errors")
        self._consecutive_errors = 0
        self._backoff_time = 1.0
        self._last_call_successful = True

    def _handle_error(self, error: Exception, operation: str) -> None:
        self._last_call_successful = False
        self._consecutive_errors += 1
        self._last_error_time = time.time()
        self._backoff_time = min(self._backoff_time * 1.5, self._max_backoff_time)

        if self._consecutive_errors == 1:
            self.logger.error(f"Error in {operation}: {error}", exc_info=True)
        elif self._consecutive_errors == self._max_consecutive_errors:
            self.logger.error(
                f"Circuit breaker active after {self._consecutive_errors} errors in {operation}"
            )
        elif self._consecutive_errors % 10 == 0:
            self.logger.error(
                f"Still failing {operation} ({self._consecutive_errors} errors): {error}"
            )
