"""Server-owned Core tool semantics and permission ceilings.

The local workbench is an operator interface, not a multi-tenant identity service.
An HTTP client may request a Skill but cannot supply its definition or mint grants.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ra_agent.contracts import SourceType, ToolCallRequest
from ra_agent.contracts.core_v1 import (
    EffectClass,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    ToolCallEnvelope,
)
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.rule_engine import RuleEngine

from .authority import PermissionAuthority
from .gateway import GatewayError

CORE_TOOL_EFFECTS = {
    "list_dir": EffectClass.READ,
    "read_file": EffectClass.READ,
    "create_file": EffectClass.WRITE,
    "write_file": EffectClass.WRITE,
    "delete_file": EffectClass.DELETE,
    "memory_read": EffectClass.MEMORY,
    "memory_write": EffectClass.MEMORY,
    "send_email_dry_run": EffectClass.NETWORK,
    "run_shell": EffectClass.PROCESS,
}


def validate_core_envelope(envelope: ToolCallEnvelope) -> None:
    expected = CORE_TOOL_EFFECTS.get(envelope.tool)
    if expected is None:
        raise GatewayError("TOOL_UNKNOWN: no trusted Core adapter is registered")
    if envelope.action != envelope.tool or envelope.effect_class is not expected:
        raise GatewayError("TOOL_METADATA_MISMATCH: action/effect must match the trusted tool")
    field = "path"
    if envelope.tool.startswith("memory_"):
        field = "key"
    elif envelope.tool == "run_shell":
        field = "cwd"
    elif envelope.tool == "send_email_dry_run":
        field = "to"
    value = envelope.canonical_args.get(field, "." if field == "cwd" else envelope.resource)
    accepted = {value} if isinstance(value, str) else set()
    if field == "to" and isinstance(value, str):
        accepted.add(f"email:{value}")
    if envelope.resource not in accepted:
        raise GatewayError("RESOURCE_MISMATCH: actual tool arguments differ from declared resource")


class TrustedCorePolicy:
    def __init__(self, app: Any, skill_manifest: Path) -> None:
        self.app = app
        self.skill_manifest = skill_manifest
        self.config_dir = app.state.runtime_settings.security_config_dir

    def validate_envelope(self, envelope: ToolCallEnvelope) -> None:
        validate_core_envelope(envelope)
        if envelope.tool not in {
            "list_dir",
            "read_file",
            "create_file",
            "write_file",
            "delete_file",
            "run_shell",
        }:
            return
        # Permission patterns name logical workspace paths. Reject aliases whose
        # actual target has a different name (including Windows directory junctions).
        # Checking containment alone would let reports/link escape reports/**.
        root = self.app.state.runtime_settings.workspace_root.resolve()
        logical = PureWindowsPath(envelope.resource).as_posix()
        try:
            resolved = (root / logical).resolve().relative_to(root).as_posix()
        except (ValueError, OSError, RuntimeError) as exc:
            raise GatewayError(
                "RESOURCE_MISMATCH: resource cannot be resolved within workspace"
            ) from exc
        if os.path.normcase(resolved) != os.path.normcase(logical):
            raise GatewayError("RESOURCE_MISMATCH: aliased workspace resources are not permitted")

    def _skill_tools(self, skill: str) -> list[str]:
        try:
            with self.skill_manifest.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Skill manifest is too large")
            data = json.loads(raw)
            if data.get("version") != 1 or not isinstance(data.get("skills"), dict):
                raise ValueError("Unsupported Skill manifest")
            tools = data["skills"].get(skill, [])
            if not isinstance(tools, list) or not all(item in CORE_TOOL_EFFECTS for item in tools):
                raise ValueError("Invalid Skill tool ceiling")
            return tools
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise GatewayError(
                "CHECK_UNAVAILABLE: trusted Skill manifest cannot be loaded"
            ) from exc

    async def _profile_for(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        session = await self.app.state.conversation_store.get(envelope.session_id)
        if session is None:
            session = await asyncio.to_thread(
                self.app.state.core_state_store.get_session, envelope.session_id
            )
        profile_id = session["security_profile_id"] if session else "default"
        profile = await self.app.state.security_profile_store.get(profile_id)
        if profile is None:
            raise GatewayError("CHECK_UNAVAILABLE: trusted security profile is unavailable")
        return profile

    async def task_limits_for(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        profile = await self._profile_for(envelope)
        return {"max_affected_objects": int(profile.get("max_affected_objects", 100))}

    async def permissions_for(self, envelope: ToolCallEnvelope) -> PermissionContext:
        return (await self.authority_for(envelope)).permissions

    async def authority_for(self, envelope: ToolCallEnvelope) -> PermissionAuthority:
        self.validate_envelope(envelope)
        task_state = await self.app.state.core_state_store.get("task_lifecycle", envelope.task_id)
        if task_state is not None and task_state.get("status") == "CANCELLED":
            raise GatewayError("TASK_CANCELLED: this task no longer accepts tool calls")
        record = await self.app.state.core_contract_service.get_contract_version(
            envelope.contract_ref.contract_id, envelope.contract_ref.version
        )
        if record is None:
            return PermissionAuthority(PermissionContext(), {})
        contract = record.contract
        profile = await self._profile_for(envelope)
        registry = self.app.state.services.tool_registry
        spec = registry.get_spec(envelope.tool)
        # Re-read trusted rules before admission; a running process must observe
        # permission tightening. RuleEngine fails closed on invalid configuration.
        rules = await asyncio.to_thread(RuleEngine.from_directory, self.config_dir)
        permission = await RuleBasedPermissionGate(rules).check(
            ToolCallRequest(
                task_id=envelope.task_id,
                step_id=envelope.request_id,
                request_id=envelope.request_id,
                tool_name=envelope.tool,
                arguments=envelope.canonical_args,
                objective="; ".join(contract.goals),
                context_summary="Core confirmed task contract",
                source_type=SourceType.USER,
                requested_at=datetime.now(UTC),
            ),
            spec,
        )
        required = set(profile.get("approval_policy", {}).get("required_actions", []))
        if permission.requires_approval:
            required.add(envelope.tool)
        if (
            permission.allowed
            and envelope.tool in required
            and envelope.action not in contract.confirmation.required_actions
            and envelope.effect_class not in contract.confirmation.required_effect_classes
        ):
            raise GatewayError(
                "CONFIRMATION_REQUIRED: update the contract to include system approval"
            )
        allowed_actions = set(profile.get("allowed_actions", []))
        if not profile.get("allow_egress", False):
            allowed_actions.discard("send_email_dry_run")
        skill_tools = await asyncio.to_thread(self._skill_tools, envelope.skill_ref)

        def grants(actions: list[str] | set[str], resources: list[str], source: str):
            return [
                PermissionGrant(
                    subject=contract.user_id,
                    skill=envelope.skill_ref,
                    tool=action,
                    action=action,
                    resource=resource,
                    effect=GrantEffect.ALLOW,
                    scope=GrantScope.TASK,
                    scope_ref=envelope.task_id,
                    source=source,
                    limits={"max_affected_objects": int(profile.get("max_affected_objects", 100))},
                )
                for action in sorted(actions)
                for resource in resources
            ]

        context = PermissionContext(
            user_grants=grants(allowed_actions, list(profile.get("resource_scopes", [])), "user"),
            skill_grants=grants(skill_tools, ["*"], "skill"),
            system_grants=grants([envelope.tool] if permission.allowed else [], ["*"], "system"),
        )
        return PermissionAuthority(
            context, {"policy": f"{profile['profile_id']}:{profile['version']}"}
        )
