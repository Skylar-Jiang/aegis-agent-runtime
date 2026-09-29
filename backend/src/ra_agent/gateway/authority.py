"""Internal provenance captured with the permissions actually evaluated."""

from dataclasses import dataclass

from ra_agent.contracts.core_v1 import PermissionContext


@dataclass(frozen=True, slots=True)
class PermissionAuthority:
    permissions: PermissionContext
    versions: dict[str, int | str]
