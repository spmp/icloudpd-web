from datetime import UTC, datetime
from pathlib import Path

import pytest  # noqa: F401

from icloudpd_web.scheduler.scheduler import Scheduler
from icloudpd_web.store.models import Policy


class FakeStore:
    def __init__(self, policies: list[Policy]) -> None:
        self._policies = policies

    def all(self) -> list[Policy]:
        return list(self._policies)


class FakeRunner:
    def __init__(self) -> None:
        self.running: set[str] = set()
        self.fired: list[tuple[str, datetime]] = []

    def is_running(self, name: str) -> bool:
        return name in self.running

    async def start(self, policy: Policy, *, password: str | None = None, trigger: str) -> object:
        self.fired.append((policy.name, datetime.now()))
        return object()


def _p(name: str, cron: str, enabled: bool = True, tz: str | None = None) -> Policy:
    return Policy(
        name=name,
        username="u@icloud.com",
        directory=Path("/tmp/p"),
        cron=cron,
        enabled=enabled,
        timezone=tz,
        icloudpd={},
        aws=None,
    )


def _passwords(_name: str) -> None:
    return None


def test_fires_when_cron_matches() -> None:
    store = FakeStore([_p("a", "* * * * *")])
    runner = FakeRunner()
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    now = datetime(2026, 1, 1, 12, 30, 15)
    s.tick(now)
    assert [p.name for p in s._pending] == ["a"]


def test_dedupes_within_minute() -> None:
    store = FakeStore([_p("a", "* * * * *")])
    runner = FakeRunner()
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    t0 = datetime(2026, 1, 1, 12, 30, 15)
    t1 = datetime(2026, 1, 1, 12, 30, 45)
    s.tick(t0)
    s.tick(t1)
    assert len(s._pending) == 1


def test_skips_overlap() -> None:
    store = FakeStore([_p("a", "* * * * *")])
    runner = FakeRunner()
    runner.running.add("a")
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    s.tick(datetime(2026, 1, 1, 12, 30, 15))
    assert s._pending == []


def test_skips_disabled() -> None:
    store = FakeStore([_p("a", "* * * * *", enabled=False)])
    runner = FakeRunner()
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    s.tick(datetime(2026, 1, 1, 12, 30, 15))
    assert s._pending == []


def test_next_run_at() -> None:
    s = Scheduler(store=FakeStore([]), runner=FakeRunner(), password_lookup=_passwords)
    p = _p("a", "0 * * * *", tz="UTC")
    dt = s.next_run_at(p, after=datetime(2026, 1, 1, 12, 30, tzinfo=UTC))
    assert dt == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)


def test_next_run_at_localized_like_tick() -> None:
    """next_run_at must evaluate the cron in policy.timezone, exactly like
    tick() does — a 3am-daily New York cron is 08:00 UTC, not 03:00 UTC."""
    s = Scheduler(store=FakeStore([]), runner=FakeRunner(), password_lookup=_passwords)
    p = _p("a", "0 3 * * *", tz="America/New_York")
    dt = s.next_run_at(p, after=datetime(2026, 1, 1, 20, 0, tzinfo=UTC))  # 15:00 EST
    assert dt.astimezone(UTC) == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)


def test_blank_timezone_means_server_local_zone() -> None:
    """A blank timezone resolves to the server's local zone (as the UI
    promises), not silently UTC."""
    s = Scheduler(store=FakeStore([]), runner=FakeRunner(), password_lookup=_passwords)
    p = _p("a", "0 * * * *", tz=None)
    now = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    localized = s._localize(now, p)
    assert localized.utcoffset() == now.astimezone().utcoffset()
    assert localized == now  # same instant, server-local representation


def test_localize_with_named_timezone() -> None:
    """_localize converts a UTC-aware datetime into the policy's timezone."""

    s = Scheduler(store=FakeStore([]), runner=FakeRunner(), password_lookup=_passwords)
    p = _p("a", "0 * * * *", tz="America/New_York")
    now_utc = datetime(2026, 1, 1, 20, 0, 0, tzinfo=UTC)
    localized = s._localize(now_utc, p)
    # America/New_York is UTC-5 in January, so 20:00 UTC → 15:00 EST
    assert localized.hour == 15


def test_localize_naive_treated_as_utc() -> None:
    """_localize treats naive datetimes as UTC when a timezone is set."""
    s = Scheduler(store=FakeStore([]), runner=FakeRunner(), password_lookup=_passwords)
    p = _p("a", "0 * * * *", tz="UTC")
    naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
    result = s._localize(naive, p)
    assert result.hour == 12


async def test_drain_pending_logs_on_start_failure() -> None:
    """_drain_pending logs and swallows exceptions from runner.start."""

    class FailingRunner(FakeRunner):
        async def start(
            self, policy: Policy, *, password: str | None = None, trigger: str
        ) -> object:
            raise RuntimeError("start failed")

    store = FakeStore([_p("a", "* * * * *")])
    runner = FailingRunner()
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    p = _p("a", "* * * * *")
    s._pending = [p]
    # Should not raise; just logs the error.
    await s._drain_pending()
    assert s._pending == []


async def test_run_forever_stops_on_stop() -> None:
    """run_forever exits cleanly when stop() is called."""
    import asyncio

    store = FakeStore([])
    runner = FakeRunner()
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    task = asyncio.create_task(s.run_forever())
    await asyncio.sleep(0)
    s.stop()
    await asyncio.wait_for(task, timeout=5)


def test_skip_overlap_logs_reason_once_per_minute(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cron-matching minute skipped due to an active run logs a warning —
    once, not once per tick-second."""
    store = FakeStore([_p("a", "* * * * *")])
    runner = FakeRunner()
    runner.running.add("a")
    s = Scheduler(store=store, runner=runner, password_lookup=_passwords)
    with caplog.at_level("WARNING", logger="icloudpd_web.scheduler.scheduler"):
        s.tick(datetime(2026, 1, 1, 12, 30, 15))
        s.tick(datetime(2026, 1, 1, 12, 30, 45))
    skips = [r for r in caplog.records if "skipping policy" in r.message]
    assert len(skips) == 1
    assert "a" in skips[0].getMessage()


async def test_drain_pending_reports_start_failure() -> None:
    """A failed scheduled start must invoke the on_start_failure hook with
    the reason (so app.py can notify), not just server-log it."""

    class FailingRunner(FakeRunner):
        async def start(
            self, policy: Policy, *, password: str | None = None, trigger: str
        ) -> object:
            raise ValueError("password is required to start policy 'a'")

    reported: list[tuple[str, str]] = []
    s = Scheduler(
        store=FakeStore([]),
        runner=FailingRunner(),
        password_lookup=_passwords,
        on_start_failure=lambda name, reason: reported.append((name, reason)),
    )
    s._pending = [_p("a", "* * * * *")]
    await s._drain_pending()
    assert reported == [("a", "password is required to start policy 'a'")]


async def test_drain_pending_survives_failing_hook() -> None:
    class FailingRunner(FakeRunner):
        async def start(
            self, policy: Policy, *, password: str | None = None, trigger: str
        ) -> object:
            raise RuntimeError("boom")

    def bad_hook(name: str, reason: str) -> None:
        raise RuntimeError("hook broke too")

    s = Scheduler(
        store=FakeStore([]),
        runner=FailingRunner(),
        password_lookup=_passwords,
        on_start_failure=bad_hook,
    )
    s._pending = [_p("a", "* * * * *")]
    await s._drain_pending()  # must not raise
    assert s._pending == []
