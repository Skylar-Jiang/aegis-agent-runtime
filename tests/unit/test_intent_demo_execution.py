import copy
from pathlib import Path

import pytest

from ra_agent.contracts.core_v1 import ToolCallEnvelope
from ra_agent.gateway.adapters import AdapterBypassError, ToolExecutionRejected
from ra_agent.intent_demo.service import TelecomDemoService, canonical, digest


async def test_adapter_requires_both_gateway_capability_and_intent_admission(
    tmp_path: Path,
):
    service = TelecomDemoService(tmp_path)
    view = await service.start("retrieval_injection")
    run = service.runs[view["run_id"]]
    envelope = ToolCallEnvelope.model_validate(view["actions"][-1]["envelope"])
    before = service._observe(run)
    with pytest.raises(AdapterBypassError):
        await run["executor"].execute(envelope)
    with pytest.raises(ToolExecutionRejected, match="admission"):
        await run["gateway"].execute(envelope.request_id)
    assert service._observe(run) == before


async def test_simulated_config_endpoint_records_a_real_local_effect(tmp_path: Path):
    service = TelecomDemoService(tmp_path)
    view = await service.start("normal_task")
    run = service.runs[view["run_id"]]
    args = {"content": "# SYNTHETIC explicitly admitted unit test\nhostname router-a\n"}
    step = service._safe_step("sim_config_modify", "devices/router-a.cfg", args)
    await service._action(run, step)
    after = await service.view(view["run_id"])
    assert (Path(view["workspace"]) / "devices/router-a.cfg").read_text() == args[
        "content"
    ]
    call = after["observed"]["endpoint_calls"][0]
    assert call["payload_digest"] == digest(args)
    assert call["effect"] == "LOCAL_SYNTHETIC_CONFIG_CHANGED"
    assert call["target"] == "devices/router-a.cfg"
    assert after["observed"]["config_call_count"] == 1
    assert after["acceptance"]["passed"] is False


async def test_expired_mock_decision_never_enters_adapter(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    import ra_agent.intent_demo.service as module

    real_timedelta = timedelta
    monkeypatch.setattr(module, "timedelta", lambda **_: real_timedelta(seconds=-1))
    service = TelecomDemoService(tmp_path)
    view = await service.start("normal_task")
    assert view["status"] == "BLOCKED"
    assert view["actions"][0]["execution_status"] == "NOT_EXECUTED"
    assert view["observed"] == view["baseline"]


async def test_reset_never_removes_an_unowned_directory(tmp_path: Path):
    protected = tmp_path / "telecom-demo"
    protected.mkdir()
    (protected / "user-file.txt").write_text("preserve")
    service = TelecomDemoService(tmp_path)
    with pytest.raises(ValueError, match="ownership marker"):
        await service.reset()
    with pytest.raises(ValueError, match="ownership marker"):
        await service.start("normal_task")
    assert (protected / "user-file.txt").read_text() == "preserve"


async def test_allow_retries_are_idempotent_and_contract_digests_match(tmp_path: Path):
    service = TelecomDemoService(tmp_path)
    view = await service.start("legitimate_goal_change")
    view = await service.control(view["run_id"], "confirm")
    before = copy.deepcopy(view["observed"])
    request_id = view["actions"][-1]["request_id"]
    again = await service.retry(view["run_id"], request_id)
    assert again["observed"] == before
    assert again["observed"]["send_call_count"] == 1
    for contract in again["contract_history"]:
        assert contract["digest"] == digest(
            {k: v for k, v in contract.items() if k != "digest"}
        )
    calls = (Path(view["workspace"]) / "endpoints.json").read_text(encoding="utf-8")
    assert calls == canonical(before["endpoint_calls"])
