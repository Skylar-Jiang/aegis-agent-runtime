"""Conservative, dependency-free IntentSpec extraction baseline.

This is intentionally a baseline for Person 1 integration. It provides deterministic
contracts and validation so Person 3 can replace the semantic detector without changing
public schemas. It never broadens scope based on external text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ra_agent.core.ids import new_id

from .models import IntentSpec

_RESOURCE_RE = re.compile(
    r"(?:https?://[^\s,;，；]+|(?:[A-Za-z]:)?[\\/][^\s,;，；]+|(?:[\w.-]+[\\/])+[\w.-]+\.(?:txt|md|json|csv|pdf|docx|xlsx)|[\w.-]+\.(?:txt|md|json|csv|pdf|docx|xlsx))",
    re.IGNORECASE,
)

_ACTION_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("read", ("读取", "阅读", "查看", "read", "open")),
    ("search", ("搜索", "检索", "查询", "search", "retrieve")),
    ("generate", ("生成", "撰写", "整理", "总结", "generate", "write report", "summarize")),
    ("write", ("写入", "保存", "修改文件", "write file", "save")),
    ("memory", ("记忆", "memory", "remember")),
    ("send", ("发送", "外发", "提交", "send", "post", "upload")),
    ("delete", ("删除", "delete", "remove")),
    ("execute", ("执行", "运行", "execute", "run")),
)


@dataclass(frozen=True, slots=True)
class ExtractionHints:
    scope: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    code: str
    severity: str
    message: str


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result


class RuleBasedIntentExtractor:
    version = "intent-extractor-rule-v1"

    def extract(
        self,
        *,
        task_id: str,
        request_text: str,
        hints: ExtractionHints | None = None,
    ) -> tuple[IntentSpec, list[ExtractionIssue]]:
        text = " ".join(request_text.strip().split())
        if not text:
            raise ValueError("request_text must not be empty")
        hints = hints or ExtractionHints()

        inferred_actions: list[str] = []
        lowered = text.casefold()
        for action, keywords in _ACTION_HINTS:
            if any(keyword.casefold() in lowered for keyword in keywords):
                inferred_actions.append(action)

        inferred_resources = _unique(match.group(0) for match in _RESOURCE_RE.finditer(text))
        scope = _unique([*hints.scope, *inferred_resources])
        allowed_actions = _unique([*hints.allowed_actions, *inferred_actions])
        forbidden_actions = _unique(hints.forbidden_actions)
        success_criteria = _unique(hints.success_criteria)

        issues: list[ExtractionIssue] = []
        if not allowed_actions:
            issues.append(
                ExtractionIssue(
                    code="ACTIONS_AMBIGUOUS",
                    severity="warning",
                    message=(
                        "No reliable action could be extracted; "
                        "trusted confirmation is required."
                    ),
                )
            )
        if not scope:
            issues.append(
                ExtractionIssue(
                    code="SCOPE_AMBIGUOUS",
                    severity="warning",
                    message=(
                        "No concrete resource scope was extracted; "
                        "keep scope empty until confirmed."
                    ),
                )
            )
        if not success_criteria:
            issues.append(
                ExtractionIssue(
                    code="SUCCESS_CRITERIA_MISSING",
                    severity="warning",
                    message=(
                        "Completion conditions were not supplied and "
                        "should be confirmed by the user."
                    ),
                )
            )

        overlap = {item.casefold() for item in allowed_actions} & {
            item.casefold() for item in forbidden_actions
        }
        if overlap:
            issues.append(
                ExtractionIssue(
                    code="ACTION_CONFLICT",
                    severity="error",
                    message=f"Actions appear in both allow and forbid sets: {sorted(overlap)}",
                )
            )
            allowed_actions = [
                action for action in allowed_actions if action.casefold() not in overlap
            ]

        intent = IntentSpec(
            intent_id=new_id("intent"),
            task_id=task_id,
            goal=text,
            scope=scope,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            success_criteria=success_criteria,
            source_refs=_unique(hints.source_refs),
            version=1,
        )
        return intent, issues
