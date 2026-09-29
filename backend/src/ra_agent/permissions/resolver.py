"""Aegis Core permission intersection logic.

Effective permission is the intersection of user authorization, Skill ceiling,
TaskContract boundary and system policy. A matching DENY always overrides ALLOW.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import TypeGuard

from ra_agent.contracts.core_v1 import (
    ContractPermissionRule,
    EffectivePermission,
    GatewayReasonCode,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractV2,
    ToolCallEnvelope,
)


class PermissionResolver:
    def __init__(self, *, case_insensitive_files: bool | None = None) -> None:
        self.case_insensitive_files = (
            os.name == "nt" if case_insensitive_files is None else case_insensitive_files
        )

    def resolve_effective_permission(
        self,
        envelope: ToolCallEnvelope,
        contract: TaskContractV2,
        context: PermissionContext,
        *,
        now: datetime | None = None,
    ) -> EffectivePermission:
        current = now or datetime.now(UTC)

        contract_failure = self._contract_failure(envelope, contract)
        if contract_failure is not None:
            code, reason = contract_failure
            return EffectivePermission(
                constraints=dict(contract.limits),
                conflict_code=code,
                conflict_reason=reason,
            )

        matched_allowed: list[PermissionGrant] = []
        matched_denied: list[PermissionGrant] = []
        sources: list[str] = []
        for layer_name, grants, failure_code in (
            ("user", context.user_grants, GatewayReasonCode.USER_DENY),
            ("skill", context.skill_grants, GatewayReasonCode.SKILL_LIMIT),
            ("system", context.system_grants, GatewayReasonCode.SYSTEM_DENY),
        ):
            active = [
                grant for grant in grants if self._grant_matches(grant, envelope, contract, current)
            ]
            denies = [grant for grant in active if grant.effect is GrantEffect.DENY]
            allows = [grant for grant in active if grant.effect is GrantEffect.ALLOW]
            if denies:
                matched_denied.extend(denies)
                sources.extend(grant.source for grant in denies)
                return EffectivePermission(
                    allowed=matched_allowed,
                    denied=matched_denied,
                    constraints=self._constraints(contract, matched_allowed, denies),
                    matched_sources=self._unique(sources),
                    conflict_code=failure_code,
                    conflict_reason=f"{layer_name} layer contains a matching DENY grant",
                )
            if not allows:
                return EffectivePermission(
                    allowed=matched_allowed,
                    denied=matched_denied,
                    constraints=self._constraints(contract, matched_allowed, []),
                    matched_sources=self._unique(sources),
                    conflict_code=failure_code,
                    conflict_reason=f"{layer_name} layer has no matching ALLOW grant",
                )
            matched_allowed.extend(allows)
            sources.extend(grant.source for grant in allows)

        requires_confirmation = (
            envelope.action in contract.confirmation.required_actions
            or envelope.effect_class in contract.confirmation.required_effect_classes
        )
        return EffectivePermission(
            allowed=matched_allowed,
            denied=matched_denied,
            constraints=self._constraints(contract, matched_allowed, []),
            matched_sources=self._unique(sources),
            conflict_reason=None,
            conflict_code=None,
            requires_confirmation=requires_confirmation,
        )

    def explain_conflict(self, permission: EffectivePermission) -> str | None:
        return permission.conflict_reason

    def _contract_failure(
        self, envelope: ToolCallEnvelope, contract: TaskContractV2
    ) -> tuple[GatewayReasonCode, str] | None:
        if envelope.task_id != contract.task_id or envelope.session_id != contract.session_id:
            return GatewayReasonCode.OUT_OF_CONTRACT, "task/session does not match TaskContractV2"

        if any(self._rule_matches(rule, envelope) for rule in contract.denied):
            return GatewayReasonCode.OUT_OF_CONTRACT, "TaskContractV2 contains a matching deny rule"

        if any(self._rule_matches(rule, envelope) for rule in contract.allowed):
            return None

        same_action = [
            rule
            for rule in contract.allowed
            if self._scalar_match(rule.tool, envelope.tool)
            and self._scalar_match(rule.action, envelope.action)
            and self._scalar_match(rule.effect, envelope.effect_class.value)
        ]
        if same_action:
            return (
                GatewayReasonCode.RESOURCE_MISMATCH,
                "requested resource is outside the TaskContractV2 allow rules",
            )
        return GatewayReasonCode.OUT_OF_CONTRACT, "requested action is outside TaskContractV2"

    def _grant_matches(
        self,
        grant: PermissionGrant,
        envelope: ToolCallEnvelope,
        contract: TaskContractV2,
        now: datetime,
    ) -> bool:
        if grant.expires_at is not None and grant.expires_at <= now:
            return False
        if grant.subject not in {"*", contract.user_id}:
            return False
        if grant.skill not in {"*", envelope.skill_ref}:
            return False
        if not self._scope_matches(grant, envelope):
            return False
        return (
            self._scalar_match(grant.tool, envelope.tool)
            and self._scalar_match(grant.action, envelope.action)
            and self._resource_match(grant.resource, envelope.resource, envelope.tool)
        )

    @staticmethod
    def _scope_matches(grant: PermissionGrant, envelope: ToolCallEnvelope) -> bool:
        if grant.scope is GrantScope.GLOBAL:
            return True
        if grant.scope is GrantScope.SESSION:
            return grant.scope_ref == envelope.session_id
        if grant.scope is GrantScope.TASK:
            return grant.scope_ref == envelope.task_id
        if grant.scope is GrantScope.ONE_SHOT:
            return grant.scope_ref == envelope.request_id
        return False

    def _rule_matches(self, rule: ContractPermissionRule, envelope: ToolCallEnvelope) -> bool:
        return (
            self._scalar_match(rule.tool, envelope.tool)
            and self._scalar_match(rule.action, envelope.action)
            and self._resource_match(rule.resource, envelope.resource, envelope.tool)
            and self._scalar_match(rule.effect, envelope.effect_class.value)
        )

    @staticmethod
    def _scalar_match(pattern: str, value: str) -> bool:
        return pattern == "*" or pattern == value

    def _resource_match(self, pattern: str, value: str, tool: str) -> bool:
        if tool in {
            "create_file",
            "write_file",
            "read_file",
            "delete_file",
            "list_dir",
            "run_shell",
        }:
            pattern, value = pattern.replace("\\", "/"), value.replace("\\", "/")
            if self.case_insensitive_files:
                pattern, value = pattern.lower(), value.lower()
        return pattern == "*" or fnmatchcase(value, pattern)

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @staticmethod
    def _constraints(
        contract: TaskContractV2,
        allows: list[PermissionGrant],
        denies: list[PermissionGrant],
    ) -> dict[str, object]:
        expirations = sorted(
            grant.expires_at.isoformat()
            for grant in [*allows, *denies]
            if grant.expires_at is not None
        )
        constraints: dict[str, object] = dict(contract.limits)
        for grant in allows:
            for name, value in grant.limits.items():
                current = constraints.get(name)
                if PermissionResolver._is_numeric_limit(current):
                    constraints[name] = min(current, value)
                elif current is None:
                    constraints[name] = value
        constraints["contract_version"] = contract.version
        if expirations:
            constraints["earliest_expiry"] = expirations[0]
        return constraints

    @staticmethod
    def _is_numeric_limit(value: object) -> TypeGuard[int | float]:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
