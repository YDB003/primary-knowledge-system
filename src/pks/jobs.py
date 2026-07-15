from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

from .contracts import canonical_json, payload_hash
from .events import EventStore, new_event_id
from .policy import POLICY_VERSION
from .state import KnowledgeState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_id(fingerprint: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(fingerprint).encode("utf-8")).hexdigest()
    return f"job_{digest[:24]}"


class JobQueue:
    def __init__(self, events: EventStore):
        self.events = events

    def queue(
        self,
        state: KnowledgeState,
        *,
        job_type: str,
        entity_id: str | None = None,
        entity_revision: int | None = None,
        parameters: dict[str, Any] | None = None,
        status: str = "QUEUED",
        last_error: str | None = None,
    ) -> dict[str, Any]:
        parameters = parameters or {}
        fingerprint = {
            "jobType": job_type,
            "entityId": entity_id,
            "entityRevision": entity_revision,
            "policyVersion": POLICY_VERSION,
            "parametersHash": payload_hash(parameters),
        }
        job_id = _job_id(fingerprint)
        existing = state.jobs.get(job_id)
        if existing:
            if existing["status"] != status:
                return self.transition(existing, status, last_error=last_error)
            return copy.deepcopy(existing)
        job = {
            "jobId": job_id,
            **fingerprint,
            "parameters": copy.deepcopy(parameters),
            "status": status,
            "attempts": 0,
            "maxAttempts": 3,
            "lastError": last_error,
            "createdAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._append(job)
        return job

    def transition(
        self,
        job: dict[str, Any],
        status: str,
        *,
        last_error: str | None = None,
        recovery_reason: str | None = None,
    ) -> dict[str, Any]:
        updated = copy.deepcopy(job)
        updated["status"] = status
        updated["updatedAt"] = _utc_now()
        if status == "RUNNING":
            updated["attempts"] = updated.get("attempts", 0) + 1
        if last_error is not None:
            updated["lastError"] = last_error
        if recovery_reason is not None:
            updated["recoveryReason"] = recovery_reason
        self._append(updated)
        return updated

    def requeue_running(self, state: KnowledgeState) -> list[str]:
        requeued: list[str] = []
        for job_id in sorted(state.jobs):
            job = state.jobs[job_id]
            if job["status"] != "RUNNING":
                continue
            self.transition(
                job,
                "QUEUED",
                recovery_reason="ABANDONED_RUNNING_JOB",
            )
            requeued.append(job_id)
        return requeued

    def _append(self, job: dict[str, Any]) -> None:
        self.events.append(
            {
                "eventId": new_event_id(),
                "eventType": "JOB_STATUS",
                "occurredAt": _utc_now(),
                "policyVersion": POLICY_VERSION,
                "job": copy.deepcopy(job),
            }
        )
