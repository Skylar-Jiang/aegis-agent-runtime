from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


def _normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UTCDateTime = Annotated[AwareDatetime, AfterValidator(_normalize_utc)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class APIError(ContractModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Human-readable error message")
    details: dict[str, object] = Field(default_factory=dict)


T = TypeVar("T")


class APIResponse(ContractModel, Generic[T]):
    data: T | None = None
    error: APIError | None = None
