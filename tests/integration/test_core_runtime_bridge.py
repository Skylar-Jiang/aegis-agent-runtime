"""Core effects use the same durable execution and recovery services as Runtime."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import (
    DeepCheckResult,
    EffectStatus,
    ExecutionStatus,
    RollbackPlan,
    SourceType,
    ToolCallRequest,
)
from ra_agent.contracts.core_v1 import (
    ContractVersionRef,
    ContractVersionStatus,
    EffectClass,
    ToolCallEnvelope,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.core.config import Settings
from ra_agent.execution.checkpoint import CheckpointStatus
from ra_agent.gateway import AdapterBypassError, ToolExecutionRejected
from ra_agent.gateway.runtime_bridge import build_core_runtime_bridge


@pytest.fixture
def bridge(tmp_path):
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        security_config_dir=Path(__file__).resolve().parents[2] / "configs",
        workspace_root=tmp_path / "workspace",
        pending_root=tmp_path / "pending",
        checkpoint_root=tmp_path / "checkpoints",
        quarantine_root=tmp_path / "quarantine",
        core_memory_path=tmp_path / "memory.json",
        core_outbox_path=tmp_path / "outbox.jsonl",
        core_event_log_path=tmp_path / "core/events.jsonl",
    )
    instance = build_core_runtime_bridge(settings, build_mock_container())
    token = object()
    instance.bind_gateway(token)
    return instance, token, settings


def call(tool, request_id, resource, **arguments):
    return ToolCallEnvelope(
        request_id=request_id,
        task_id="core-bridge-task",
        session_id="core-bridge-session",
        contract_ref=ContractVersionRef(
            contract_id="bridge-contract",
            version=1,
            digest="bridge-contract-digest",
            status=ContractVersionStatus.CONFIRMED,
        ),
        skill_ref="writer",
        tool=tool,
        action=tool,
        effect_class=EffectClass.READ if tool.endswith("read") else EffectClass.WRITE,
        resource=resource,
        canonical_args=arguments,
    )


async def recover(instance, *request_ids):
    return await instance.services.selective_rollback_executor.execute(
        RollbackPlan(
            plan_id="recover-core-bridge",
            task_id="core-bridge-task",
            trigger="integration-test",
            request_ids=list(request_ids),
            reason="recover the selected Core effect",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["create_file", "write_file", "delete_file"])
async def test_core_file_commits_and_recovers_through_runtime(bridge, tool):
    instance, token, settings = bridge
    target = settings.workspace_root / "document.txt"
    if tool != "create_file":
        target.write_text("original", encoding="utf-8")
    result = await instance.execute(
        call(tool, "core-file", "document.txt", path="document.txt", content="new content"),
        gateway_token=token,
    )
    receipt = result.pop("runtime")
    assert result == (
        {"tool": tool, "path": "document.txt", "deleted": True}
        if tool == "delete_file"
        else {"tool": tool, "path": "document.txt", "bytes_written": 11}
    )
    effect = await instance.services.effect_store.get_by_request_id("core-file")
    assert effect is not None
    assert effect.status is EffectStatus.COMMITTED
    checkpoint = await instance.services.checkpoint_manager.get(effect.checkpoint_id)
    assert checkpoint.status is CheckpointStatus.COMMITTED
    assert receipt == {
        "request_id": "core-file",
        "checkpoint_id": checkpoint.checkpoint_id,
        "effect_id": effect.effect_id,
        "commit_status": "COMMITTED",
    }
    assert not target.exists() if tool == "delete_file" else target.read_text() == "new content"

    assert (await recover(instance, "core-file")).status is ExecutionStatus.SUCCESS
    assert not target.exists() if tool == "create_file" else target.read_text() == "original"
    assert (
        await instance.services.effect_store.get(effect.effect_id)
    ).status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_core_memory_uses_runtime_versions_and_selective_recovery(bridge):
    instance, token, _ = bridge
    for request_id, key, value in [
        ("memory-first", "note", {"message": "first"}),
        ("memory-second", "note", {"message": "second"}),
        ("memory-other", "other", 42),
    ]:
        result = await instance.execute(
            call("memory_write", request_id, key, key=key, value=value), gateway_token=token
        )
        receipt = result.pop("runtime")
        assert result == {"tool": "memory_write", "key": key, "stored": True}
        effect = await instance.services.effect_store.get_by_request_id(request_id)
        assert effect is not None and effect.status is EffectStatus.COMMITTED
        assert receipt == {
            "request_id": request_id,
            "checkpoint_id": None,
            "effect_id": effect.effect_id,
            "commit_status": "COMMITTED",
        }
    assert (await recover(instance, "memory-second")).status is ExecutionStatus.SUCCESS
    assert await instance.execute(
        call("memory_read", "memory-read", "note", key="note"), gateway_token=token
    ) == {"tool": "memory_read", "key": "note", "found": True, "value": {"message": "first"}}
    assert (await instance.services.memory_manager.store.get_trusted_value("other"))[1] == 42


@pytest.mark.asyncio
async def test_bridge_retains_the_opaque_gateway_boundary(bridge):
    instance, _, settings = bridge
    with pytest.raises(AdapterBypassError):
        await instance.execute(
            call("create_file", "bypass", "bypass.txt", path="bypass.txt", content="bypass"),
            gateway_token=object(),
        )
    assert not (settings.workspace_root / "bypass.txt").exists()


@pytest.mark.asyncio
async def test_core_deep_check_rejection_restores_file_and_effect(bridge):
    instance, token, settings = bridge
    target = settings.workspace_root / "original.txt"
    target.write_text("original")

    class RejectAfterStaging:
        async def check(self, request, execution):
            assert target.read_text() == "original"
            effect = await instance.services.effect_store.get_by_request_id(request.request_id)
            assert effect.status is EffectStatus.PENDING
            return DeepCheckResult(
                request_id=request.request_id, passed=False, reason="reject test"
            )

    instance.sandbox_flow.deep_checker = RejectAfterStaging()
    with pytest.raises(ToolExecutionRejected, match="reject test"):
        await instance.execute(
            call("write_file", "rejected", "original.txt", path="original.txt", content="unsafe"),
            gateway_token=token,
        )
    effect = await instance.services.effect_store.get_by_request_id("rejected")
    assert effect.status is EffectStatus.ROLLED_BACK
    assert target.read_text() == "original"
    assert (await instance.services.checkpoint_manager.get(effect.checkpoint_id)).status is (
        CheckpointStatus.ROLLED_BACK
    )
    assert (await recover(instance, "rejected")).status is ExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_bridges_reuse_existing_real_execution_services_and_survive_restart(bridge):
    instance, token, settings = bridge
    await instance.execute(
        call("memory_write", "persisted", "note", key="note", value="durable"),
        gateway_token=token,
    )
    second = build_core_runtime_bridge(settings, instance.services)
    assert second.services is instance.services
    restarted = build_core_runtime_bridge(settings, build_mock_container())
    restarted.bind_gateway(token)
    assert await restarted.execute(
        call("memory_read", "read-after-restart", "note", key="note"), gateway_token=token
    ) == {"tool": "memory_read", "key": "note", "found": True, "value": "durable"}
    assert (await recover(restarted, "persisted")).status is ExecutionStatus.SUCCESS
    assert (
        await restarted.execute(
            call("memory_read", "read-after-rollback", "note", key="note"), gateway_token=token
        )
    )["found"] is False


@pytest.mark.asyncio
async def test_existing_core_json_memory_is_imported_before_new_version(bridge):
    instance, token, settings = bridge
    settings.core_memory_path.write_text(json.dumps({"note": "legacy"}))
    await instance.execute(
        call("memory_write", "changed", "note", key="note", value="updated"),
        gateway_token=token,
    )
    assert (await recover(instance, "changed")).status is ExecutionStatus.SUCCESS
    assert (
        await instance.execute(
            call("memory_read", "legacy-read", "note", key="note"), gateway_token=token
        )
    )["value"] == "legacy"


@pytest.mark.asyncio
async def test_simulated_outbox_preserves_existing_core_result(bridge):
    instance, token, settings = bridge
    result = await instance.execute(
        call(
            "send_email_dry_run",
            "simulated",
            "email:test@example.com",
            to="test@example.com",
            subject="Preview",
            body="Local only",
        ),
        gateway_token=token,
    )
    assert result == {
        "tool": "send_email_dry_run",
        "recipient": "test@example.com",
        "simulated": True,
        "sent": False,
    }
    assert json.loads(settings.core_outbox_path.read_text())["sent"] is False


@pytest.mark.asyncio
async def test_bridge_commit_io_failure_recovers_real_file_and_effect(bridge, monkeypatch):
    instance, token, settings = bridge
    target = settings.workspace_root / "original.txt"
    target.write_text("original")

    def fail_replace(*args, **kwargs):
        raise OSError("disk failure before replace")

    monkeypatch.setattr(instance.services.commit_gate, "_atomic_replace_target", fail_replace)
    with pytest.raises(ToolExecutionRejected, match="disk failure"):
        await instance.execute(
            call("write_file", "disk-failed", "original.txt", path="original.txt", content="new"),
            gateway_token=token,
        )
    assert target.read_text() == "original"
    effect = await instance.services.effect_store.get_by_request_id("disk-failed")
    assert effect.status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_core_cancellation_drains_commit_and_recovers_file(bridge, monkeypatch):
    instance, token, settings = bridge
    target = settings.workspace_root / "original.txt"
    target.write_text("original")
    entered = threading.Event()
    release = threading.Event()
    replace = instance.services.commit_gate._atomic_replace_target

    def delayed_replace(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release commit")
        return replace(*args, **kwargs)

    monkeypatch.setattr(instance.services.commit_gate, "_atomic_replace_target", delayed_replace)
    running = asyncio.create_task(
        instance.execute(
            call("write_file", "cancelled", "original.txt", path="original.txt", content="new"),
            gateway_token=token,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert target.read_text() == "original"
    effect = await instance.services.effect_store.get_by_request_id("cancelled")
    assert effect.status is EffectStatus.ROLLED_BACK
    assert (await instance.services.checkpoint_manager.get(effect.checkpoint_id)).status is (
        CheckpointStatus.ROLLED_BACK
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", ["请求:1", "REQUEST", "request.", "con", "a" * 128])
async def test_core_public_ids_map_to_safe_distinct_runtime_correlation(bridge, request_id):
    instance, token, settings = bridge
    result = await instance.execute(
        call("create_file", request_id, "safe.txt", path="safe.txt", content="safe"),
        gateway_token=token,
    )
    receipt = result["runtime"]
    assert receipt["request_id"] != request_id
    effect = await instance.services.effect_store.get_by_request_id(receipt["request_id"])
    assert effect.effect_id == receipt["effect_id"]
    assert effect.status is EffectStatus.COMMITTED
    assert (settings.workspace_root / "safe.txt").read_text() == "safe"
    assert (await recover(instance, receipt["request_id"])).status is ExecutionStatus.SUCCESS
    assert not (settings.workspace_root / "safe.txt").exists()


@pytest.mark.asyncio
async def test_core_and_existing_runtime_scheduler_share_memory_and_recovery(bridge):
    instance, token, _ = bridge
    scheduler = build_runtime_scheduler(instance.services)
    runtime_write = ToolCallRequest(
        task_id="core-bridge-task",
        step_id="legacy-runtime-step",
        request_id="runtime-memory",
        tool_name="memory_write",
        arguments={"key": "shared", "value": "runtime value"},
        objective="write shared trusted memory",
        context_summary="existing Runtime entry integration",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )
    assert (await scheduler.schedule(runtime_write)).status is ExecutionStatus.COMMITTED
    assert (
        await instance.execute(
            call("memory_read", "core-read", "shared", key="shared"), gateway_token=token
        )
    )["value"] == "runtime value"
    core_write = await instance.execute(
        call("memory_write", "core-memory", "shared", key="shared", value="core value"),
        gateway_token=token,
    )
    runtime_read = runtime_write.model_copy(
        update={
            "tool_name": "memory_read",
            "request_id": "runtime-read",
            "step_id": "runtime-read-step",
            "arguments": {"key": "shared"},
        }
    )
    assert (await scheduler.schedule(runtime_read)).output["value"] == "core value"
    assert (
        await recover(instance, core_write["runtime"]["request_id"])
    ).status is ExecutionStatus.SUCCESS
    assert (await instance.services.memory_manager.store.get_trusted_value("shared"))[1] == (
        "runtime value"
    )
