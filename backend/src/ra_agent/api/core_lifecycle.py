"""Keep Core's durable admission state and workbench task history consistent."""

from typing import Any

from fastapi import Request

from ra_agent.contracts.core_v1 import ContractRecord
from ra_agent.gateway.state import CoreStateTransaction


async def set_core_task_status(
    request: Request, task_id: str, status: str, *, contract: dict[str, Any] | None = None
) -> None:
    def persist(transaction: CoreStateTransaction) -> str:
        state = transaction.get("task_lifecycle", task_id) or {}
        current_status = "CANCELLED" if state.get("status") == "CANCELLED" else status
        if contract is not None:
            binding = [contract["contract_id"], contract["version"]]
            if current_status == "READY" and state.get("confirmed_contract") == binding:
                current_status = state.get("status", "READY")
            state["confirmed_contract"] = binding
        state["status"] = current_status
        transaction.put("task_lifecycle", task_id, state)
        return current_status

    status = await request.app.state.core_state_store.run(persist)
    store = request.app.state.task_store
    if await store.get(task_id) is not None:
        fields: dict[str, Any] = {"status": status}
        if contract is not None:
            fields["contract"] = contract
        await store.update(task_id, **fields)


async def complete_core_confirmation(request: Request, record: ContractRecord) -> None:
    """Repair an interrupted confirmation without duplicating its evidence or state."""
    store = request.app.state.core_state_store
    key = f"{record.ref.contract_id}:v{record.ref.version}"
    if await store.get("confirmation_events", key) is not None:
        return
    gateway = request.app.state.core_gateway
    # A crash may have persisted the event before its completion marker. Read the
    # verified history to reconcile that gap while holding the admission guard.
    events = await gateway.event_store.list_task_events(record.contract.task_id)
    if not any(
        event["type"] == "CONTRACT_CONFIRMED"
        and event["source_ref"] == f"contract:{key}"
        and event["actor"] == record.ref.confirmed_by
        and record.ref.digest in event.get("evidence_refs", [])
        for event in events
    ):
        await gateway.record_contract_event(
            record, event_type="CONTRACT_CONFIRMED", actor=record.ref.confirmed_by or "user"
        )
    await set_core_task_status(
        request, record.contract.task_id, "READY", contract=record.contract.model_dump(mode="json")
    )
    await store.run(lambda tx: tx.put("confirmation_events", key, {"digest": record.ref.digest}))
