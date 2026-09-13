import asyncio
from pathlib import Path

import pytest

from icloudpd_web.runner.runner import Runner
from icloudpd_web.store.models import Policy


def _policy() -> Policy:
    return Policy(
        name="p",
        username="u@icloud.com",
        directory=Path("/tmp/p"),
        cron="0 * * * *",
        enabled=True,
        icloudpd={},
        aws=None,
    )


@pytest.mark.asyncio
async def test_start_returns_run(
    tmp_path: Path, fake_icloudpd_cmd: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "success")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "2")

    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
    )
    run = await r.start(_policy(), password="pw", trigger="manual")
    await run.wait()
    assert run.status == "success"
    assert r.is_running("p") is False


@pytest.mark.asyncio
async def test_is_running_blocks_duplicate(
    tmp_path: Path, fake_icloudpd_cmd: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "slow")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "100")

    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
    )
    run = await r.start(_policy(), password="pw", trigger="manual")
    assert r.is_running("p") is True
    with pytest.raises(RuntimeError):
        await r.start(_policy(), password="pw", trigger="manual")
    await run.stop()
    await run.wait()


@pytest.mark.asyncio
async def test_prunes_logs_after_completion(
    tmp_path: Path, fake_icloudpd_cmd: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "success")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
        retention=2,
    )
    for _ in range(4):
        run = await r.start(_policy(), password="pw", trigger="manual")
        await run.wait()
        # Brief pause so file mtimes differ.
        await asyncio.sleep(0.01)
    # Wait for the post-completion prune task to finish.
    await asyncio.sleep(0.1)
    log_files = list((tmp_path / "p").glob("*.log"))
    assert len(log_files) == 2


@pytest.mark.asyncio
async def test_start_raises_without_password(tmp_path: Path, fake_icloudpd_cmd: list[str]) -> None:
    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
    )
    with pytest.raises(ValueError, match="password is required"):
        await r.start(_policy(), password=None, trigger="manual")


def _policy2(name: str, username: str = "u@icloud.com") -> Policy:
    return Policy(
        name=name,
        username=username,
        directory=Path(f"/tmp/{name}"),
        cron="0 * * * *",
        enabled=True,
        icloudpd={},
        aws=None,
    )


@pytest.mark.asyncio
async def test_same_apple_id_runs_are_serialized(
    tmp_path: Path, fake_icloudpd_cmd: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two policies sharing a username must not run concurrently — they share
    session/cookie files and would clobber each other's Apple session."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "slow")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "100")

    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
    )
    run = await r.start(_policy2("a"), password="pw", trigger="manual")
    with pytest.raises(RuntimeError, match="Apple ID"):
        await r.start(_policy2("b"), password="pw", trigger="manual")
    # A different Apple ID is fine.
    other = await r.start(
        _policy2("c", username="other@icloud.com"), password="pw", trigger="manual"
    )
    await run.stop()
    await other.stop()
    await asyncio.gather(run.wait(), other.wait())
    # Give the completion tasks a beat to release the claims.
    await asyncio.sleep(0.05)
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "success")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")
    again = await r.start(_policy2("b"), password="pw", trigger="manual")
    await again.wait()
    assert again.status == "success"


@pytest.mark.asyncio
async def test_child_runs_in_new_session(
    tmp_path: Path, fake_icloudpd_cmd: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child must be detached from any controlling terminal
    (start_new_session), or icloudpd's getpass would read /dev/tty and hang."""
    import os

    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "slow")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "100")

    r = Runner(
        runs_base=tmp_path,
        icloudpd_argv=lambda argv_tail: [*fake_icloudpd_cmd, *argv_tail],
    )
    run = await r.start(_policy2("sess"), password="pw", trigger="manual")
    pid = run._proc.pid
    # A session leader's process-group id equals its own pid.
    assert os.getpgid(pid) == pid
    await run.stop()
    await run.wait()
