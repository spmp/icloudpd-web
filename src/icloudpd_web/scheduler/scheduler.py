from __future__ import annotations

import asyncio
import logging
import zoneinfo
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from croniter import croniter

from icloudpd_web.store.models import Policy


log = logging.getLogger(__name__)


class _StoreProto(Protocol):
    def all(self) -> list[Policy]: ...  # pragma: no cover


class _RunnerProto(Protocol):
    def is_running(self, name: str) -> bool: ...  # pragma: no cover

    async def start(
        self, policy: Policy, *, password: str | None, trigger: str
    ) -> object: ...  # pragma: no cover


class Scheduler:
    def __init__(
        self,
        *,
        store: _StoreProto,
        runner: _RunnerProto,
        password_lookup: Callable[[str], str | None],
        on_start_failure: Callable[[str, str], None] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._password_lookup = password_lookup
        # Called with (policy_name, reason) when a scheduled run fails to
        # start — e.g. missing password or folder-structure drift. Without
        # it those failures were server-log-only: no Run object, no
        # notification, last_run unchanged.
        self._on_start_failure = on_start_failure
        self._last_fired: dict[str, datetime] = {}
        # Cron minutes we already logged a "skipped: still running" warning
        # for, so a matching minute logs once, not once per tick-second.
        self._skip_logged: dict[str, datetime] = {}
        self._stop = asyncio.Event()
        self._pending: list[Policy] = []

    def next_run_at(self, policy: Policy, *, after: datetime) -> datetime:
        # Evaluate the cron in the same zone tick() fires in — otherwise the
        # advertised next_run_at is hours off for non-UTC policies.
        return croniter(policy.cron, self._localize(after, policy)).get_next(datetime)

    def tick(self, now: datetime) -> None:
        for p in self._store.all():
            if not p.enabled:
                continue
            local_now = self._localize(now, p)
            minute = local_now.replace(second=0, microsecond=0)
            if not croniter.match(p.cron, minute):
                continue
            if self._last_fired.get(p.name) == minute:
                continue
            if self._runner.is_running(p.name):
                # A prior run still holds the slot (possibly stuck awaiting
                # 2FA). Log the skip so unattended stalls are visible.
                if self._skip_logged.get(p.name) != minute:
                    self._skip_logged[p.name] = minute
                    log.warning(
                        "scheduler: skipping policy %s at %s — previous run "
                        "still active (it may be waiting for a 2FA code)",
                        p.name,
                        minute.isoformat(),
                    )
                continue
            self._last_fired[p.name] = minute
            self._pending.append(p)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1)
            try:
                self.tick(datetime.now(UTC))
                await self._drain_pending()
            except Exception:
                log.exception("scheduler tick failed")

    def stop(self) -> None:
        self._stop.set()

    async def _drain_pending(self) -> None:
        while self._pending:
            p = self._pending.pop(0)
            try:
                await self._runner.start(
                    p,
                    password=self._password_lookup(p.name),
                    trigger="cron",
                )
            except Exception as exc:
                log.exception("failed to start scheduled policy %s", p.name)
                if self._on_start_failure is not None:
                    try:
                        self._on_start_failure(p.name, str(exc))
                    except Exception:
                        log.exception("on_start_failure hook failed for %s", p.name)

    @staticmethod
    def _localize(now: datetime, policy: Policy) -> datetime:
        """Convert *now* into the zone the policy's cron is interpreted in.

        A blank timezone means the server's local zone (as the UI promises),
        not UTC. Naive inputs are treated as UTC.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if policy.timezone is None:
            return now.astimezone()  # server's local zone
        return now.astimezone(zoneinfo.ZoneInfo(policy.timezone))
