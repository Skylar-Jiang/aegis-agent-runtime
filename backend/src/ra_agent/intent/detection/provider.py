"""Dependency-injected async bridge for the frozen runtime protocol.

The integration branch used for this implementation does not yet contain P1's
IntentCheckContext or DecisionResult classes. This provider deliberately imports
neither guessed schemas nor an alternate runtime implementation. P1 supplies the
context adapter and its actual DecisionResult constructor; all nine output keys
are emitted here and no public fields are added.
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Generic, TypeVar
from uuid import uuid4

from ._types import Assessment, DetectionInput
from .engine import IntentDetectorCore

ContextT = TypeVar("ContextT")
ResultT = TypeVar("ResultT")


class DetectorBusyError(RuntimeError):
    """No bounded worker slot is available; the runtime must pause/retry safely."""


class IntentDetector(Generic[ContextT, ResultT]):
    """Instantiate as IntentDetector[IntentCheckContext, DecisionResult] at integration.

    The trusted adapter must map normalized tool arguments, all target resources,
    confirmed contract versions and verified past events. It must not obtain any
    authorization or progress flags from an Agent/source's self-declared text.

    Adapter/model/result-validation failures propagate to the runtime's error
    handling. They never return None or an implicit CONTINUE.
    """

    def __init__(
        self,
        *,
        context_adapter: Callable[[ContextT], DetectionInput],
        result_factory: Callable[..., ResultT],
        core: IntentDetectorCore | None = None,
        max_pending_evaluations: int = 8,
    ) -> None:
        if type(max_pending_evaluations) is not int or not 1 <= max_pending_evaluations <= 64:
            raise ValueError("max_pending_evaluations must be an integer between 1 and 64")
        self._adapter = context_adapter
        self._factory = result_factory
        self.core = core or IntentDetectorCore()
        self._explanations: OrderedDict[str, Assessment] = OrderedDict()
        self._explanation_lock = threading.RLock()
        self._worker_lock = threading.RLock()
        self._max_pending = max_pending_evaluations
        self._pending = 0
        self._workers: set[asyncio.Future[Assessment]] = set()

    @property
    def pending_evaluations(self) -> int:
        with self._worker_lock:
            return self._pending

    def _worker_finished(self, future: asyncio.Future[Assessment]) -> None:
        with self._worker_lock:
            self._workers.discard(future)
            self._pending -= 1
        # Consume exceptions even if the requesting coroutine has been cancelled.
        # Awaiters still receive this same exception through the shielded future.
        if not future.cancelled():
            future.exception()

    async def evaluate(self, context: ContextT, /) -> ResultT | None:
        # Copy/normalize input before handing CPU work to the worker. The private
        # snapshot is frozen and the core serializes updates to bounded state.
        with self._worker_lock:
            if self._pending >= self._max_pending:
                raise DetectorBusyError("intent detector pending evaluation limit reached")
            self._pending += 1
        try:
            snapshot = self._adapter(context)
            snapshot.validate()
            loop = asyncio.get_running_loop()
            worker = loop.run_in_executor(None, self.core.analyze, snapshot)
            with self._worker_lock:
                self._workers.add(worker)
            worker.add_done_callback(self._worker_finished)
        except BaseException:
            with self._worker_lock:
                self._pending -= 1
            raise
        # Cancellation does not free capacity while the underlying scorer is
        # still running. It also cannot publish a result or explanation later.
        assessment = await asyncio.shield(worker)
        decision_id = str(uuid4())
        fields: dict[str, Any] = {
            "decision_id": decision_id,
            "decision": assessment.disposition.value,
            "risk_score": assessment.risk_score,
            "trigger_dimensions": list(assessment.trigger_dimensions),
            "evidence_refs": list(assessment.evidence_refs),
            "reason_code": assessment.reason_code,
            "policy_version": assessment.policy_version,
            "detector_version": assessment.detector_version,
            "expires_at": assessment.expires_at,
        }
        result = self._factory(**fields)
        if result is None:
            raise ValueError("DecisionResult factory returned None instead of a validated result")
        with self._explanation_lock:
            self._explanations[decision_id] = assessment
            while len(self._explanations) > self.core.policy.cache_size:
                self._explanations.popitem(last=False)
        return result

    def explain(self, decision_id: str) -> Assessment | None:
        """Optional local detail lookup; persistent evidence remains runtime-owned."""
        with self._explanation_lock:
            return self._explanations.get(decision_id)
