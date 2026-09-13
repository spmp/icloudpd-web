"""Unit test for MFA flow in Run: on_mfa_needed callback is called, code is delivered via stdin."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from icloudpd_web.runner.run import Run


def _argv(fake_icloudpd_cmd: list[str]) -> list[str]:
    """Minimal valid argv for the fake binary (satisfies argparse requirements)."""
    return [
        *fake_icloudpd_cmd,
        "--username",
        "u@icloud.com",
        "--directory",
        "/tmp/test",
        "--password-provider",
        "console",
        "--mfa-provider",
        "console",
    ]


@pytest.mark.asyncio
async def test_mfa_flow_delivers_code_via_stdin(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Run sees the MFA prompt, it calls on_mfa_needed, polls the slot path,
    and writes the code to stdin. The fake binary should then complete successfully."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    slot_path = tmp_path / "p.code"
    callback_called_with: list[str] = []

    def on_mfa_needed(policy_name: str) -> Path:
        callback_called_with.append(policy_name)
        return slot_path

    run = Run(
        run_id="test-mfa",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=on_mfa_needed,
    )
    await run.start()

    # Give the run a moment to print the MFA prompt and trigger the callback.
    # Then simulate the user providing the MFA code via the API
    # (which writes to the slot path).
    async def provide_code_after_delay() -> None:
        for _ in range(50):
            await asyncio.sleep(0.05)
            if callback_called_with:
                break
        slot_path.write_text("123456\n")

    await asyncio.wait_for(
        asyncio.gather(run.wait(), provide_code_after_delay()),
        timeout=10,
    )

    # Exactly one trigger: the post-success banner ("...the two-factor
    # authentication expires.") must NOT re-trigger the MFA prompt.
    assert callback_called_with == ["p"], "on_mfa_needed must be called exactly once"
    assert run.status == "success", f"Expected success, got {run.status}"
    log_text = run.log_path.read_text()
    assert "the two-factor authentication expires." in log_text


@pytest.mark.asyncio
async def test_mfa_flow_awaiting_mfa_status_event(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run emits a status=awaiting_mfa event after calling on_mfa_needed."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    slot_path = tmp_path / "p.code"

    def on_mfa_needed(policy_name: str) -> Path:
        return slot_path

    run = Run(
        run_id="test-mfa-evt",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=on_mfa_needed,
    )

    status_events: list[str] = []

    async def collect_events() -> None:
        async for ev in run.subscribe(since=None):
            if ev.kind == "status":
                status_events.append(ev.data.get("status", ""))
            if ev.data.get("status") in ("success", "failed", "stopped"):
                break

    await run.start()

    async def provide_code_after_delay() -> None:
        for _ in range(50):
            await asyncio.sleep(0.05)
            if slot_path.parent.exists() and run.status == "running":
                # Wait for callback to be invoked (slot file doesn't exist yet)
                break
        slot_path.write_text("123456\n")

    await asyncio.wait_for(
        asyncio.gather(run.wait(), collect_events(), provide_code_after_delay()),
        timeout=10,
    )

    assert "awaiting_mfa" in status_events, f"Expected awaiting_mfa in {status_events}"
    # After the code is written to stdin the run must transition back to
    # "running" (so the UI can close the modal), then end with success.
    i = status_events.index("awaiting_mfa")
    assert status_events[i + 1 :] == ["running", "success"], (
        f"Expected awaiting_mfa → running → success, got {status_events}"
    )
    assert run.status == "success"


@pytest.mark.asyncio
async def test_mfa_flow_rejected_code_fails_run(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real icloudpd 1.32.3 EXITS with code 1 on a rejected code (no console
    re-prompt). The run must end failed — and must NOT re-open an MFA prompt
    off the rejection error line."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa_reject")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    slots: list[Path] = []

    def on_mfa_needed(policy_name: str) -> Path:
        slot = tmp_path / f"code_{len(slots)}"
        slots.append(slot)
        return slot

    run = Run(
        run_id="test-mfa-reject",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=on_mfa_needed,
    )
    await run.start()

    async def provide_bad_code() -> None:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if slots:
                slots[0].write_text("111111\n")
                break

    await asyncio.wait_for(
        asyncio.gather(run.wait(), provide_bad_code()),
        timeout=15,
    )

    assert len(slots) == 1, f"Expected one on_mfa_needed call, got {len(slots)}"
    assert run.status == "failed"
    assert run.exit_code == 1
    log_text = run.log_path.read_text()
    assert "Failed to verify two-factor authentication code" in log_text


@pytest.mark.asyncio
async def test_mfa_flow_stop_during_awaiting_mfa(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-initiated cancel while awaiting MFA must terminate the run.

    The MFA modal's Cancel button calls Runner.stop(); the run should
    transition to 'stopped' without ever receiving an MFA code.
    """
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    slot_path = tmp_path / "p.code"
    slot_called = asyncio.Event()

    def on_mfa_needed(policy_name: str) -> Path:
        slot_called.set()
        return slot_path

    run = Run(
        run_id="test-mfa-cancel",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=on_mfa_needed,
    )
    await run.start()

    # Wait until we're awaiting MFA, then stop without providing a code.
    await asyncio.wait_for(slot_called.wait(), timeout=5)
    await run.stop()
    await asyncio.wait_for(run.wait(), timeout=5)

    assert run.status == "stopped"
    # Slot file must NOT have been written — user cancelled instead of submitting.
    assert not slot_path.exists()


@pytest.mark.asyncio
async def test_mfa_flow_times_out_without_code(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no 2FA code arrives within mfa_timeout, the run must fail with a
    distinct failure_reason instead of holding the policy slot forever."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa")
    monkeypatch.setenv("FAKE_ICLOUDPD_TOTAL", "1")

    slot_path = tmp_path / "p.code"

    run = Run(
        run_id="test-mfa-timeout",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=lambda _name: slot_path,
        mfa_timeout=0.3,
    )
    await run.start()
    await asyncio.wait_for(run.wait(), timeout=10)

    assert run.status == "failed"
    assert run.failure_reason == "mfa_timeout"
    log_text = run.log_path.read_text()
    assert "2FA code not provided within" in log_text
    # Sidecar must carry the reason for the UI/history.
    import json

    meta = json.loads(run.log_path.with_suffix(".meta.json").read_text())
    assert meta["failure_reason"] == "mfa_timeout"


@pytest.mark.asyncio
async def test_mfa_flow_rejected_code_sets_failure_reason(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rejection line maps to a distinct mfa_rejected failure the UI explains."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa_reject")

    slot_path = tmp_path / "p.code"

    run = Run(
        run_id="test-mfa-reject-reason",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=lambda _name: slot_path,
    )
    await run.start()

    async def provide_bad_code() -> None:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if run.status == "running" and not slot_path.exists():
                break
        slot_path.write_text("111111\n")

    await asyncio.wait_for(asyncio.gather(run.wait(), provide_bad_code()), timeout=15)

    assert run.status == "failed"
    assert run.failure_reason == "mfa_rejected"


@pytest.mark.asyncio
async def test_mfa_flow_2sa_prompt_is_unsupported(
    tmp_path: Path,
    fake_icloudpd_cmd: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy 2sa device-index prompt must stop the run with a clear
    error instead of opening the code modal (which could never answer it)."""
    monkeypatch.setenv("FAKE_ICLOUDPD_MODE", "mfa_2sa")

    prompts: list[str] = []

    def on_mfa_needed(policy_name: str) -> Path:
        prompts.append(policy_name)
        return tmp_path / "p.code"

    run = Run(
        run_id="test-mfa-2sa",
        policy_name="p",
        argv=_argv(fake_icloudpd_cmd),
        log_dir=tmp_path,
        password="pw",
        on_mfa_needed=on_mfa_needed,
    )
    await run.start()
    await asyncio.wait_for(run.wait(), timeout=15)

    assert prompts == [], "2sa must not open the MFA code modal"
    assert run.status == "failed"
    assert run.failure_reason == "mfa_2sa_unsupported"
    assert "does not support" in run.log_path.read_text()
