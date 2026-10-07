from __future__ import annotations

import pytest

from ra_agent.intent import ExtractionHints, IntentContractStatus, IntentRegistry, RuleBasedIntentExtractor


@pytest.mark.asyncio
async def test_extract_create_confirm_and_version_intent() -> None:
    extractor = RuleBasedIntentExtractor()
    intent, issues = extractor.extract(
        task_id="task-1",
        request_text="读取 reports/input.md，整理并生成报告。",
        hints=ExtractionHints(
            scope=("reports/**",),
            allowed_actions=("read", "generate"),
            forbidden_actions=("send",),
            success_criteria=("生成最终报告",),
            source_refs=("user-request:1",),
        ),
    )
    assert intent.task_id == "task-1"
    assert "read" in intent.allowed_actions
    assert issues == []

    registry = IntentRegistry()
    intent, contract = await registry.create(intent, tools=["read_file"])
    assert contract.status is IntentContractStatus.DRAFT
    assert contract.intent_ref.endswith(":v1")

    confirmed, confirmed_contract = await registry.confirm(intent.intent_id, confirmed_by="user-1")
    assert confirmed.confirmed_by == "user-1"
    assert confirmed_contract.status is IntentContractStatus.CONFIRMED

    updated, updated_contract, expansion = await registry.update(
        intent.intent_id,
        allowed_actions=["read", "generate", "execute"],
    )
    assert updated.version == 2
    assert updated_contract.contract_version == 2
    assert updated_contract.parent_version == 1
    assert expansion is True
    assert updated.confirmed_by is None


@pytest.mark.asyncio
async def test_extractor_does_not_silently_invent_scope_or_success_criteria() -> None:
    extractor = RuleBasedIntentExtractor()
    intent, issues = extractor.extract(task_id="task-2", request_text="帮我整理一下这些资料")
    assert intent.scope == []
    assert intent.success_criteria == []
    codes = {item.code for item in issues}
    assert "SCOPE_AMBIGUOUS" in codes
    assert "SUCCESS_CRITERIA_MISSING" in codes
