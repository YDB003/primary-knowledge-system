from __future__ import annotations

import copy
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    ProtocolError,
    canonical_json,
    payload_hash,
    validate_learn_request,
    validate_query_request,
)
from .events import EventStore, OperationClaimConflict, new_event_id
from .jobs import JobQueue
from .imports import build_import_mutation, import_entity_content_hash
from .materialize import ManualEditConflict, Materializer
from .paths import VaultPaths
from .policy import POLICY_VERSION, LearnDecision, evaluate_learn
from .projection import Projection
from .repositories import RepositoryRegistry, capture_repository, scan_repository
from .state import KnowledgeState, reduce_events
from .subject_adapters import get_adapter


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class KnowledgeService:
    def __init__(self, vault: str | Path):
        self.paths = VaultPaths(Path(vault))
        self.events = EventStore(self.paths)
        self.materializer = Materializer(self.paths)
        self.projection = Projection(self.paths)
        self.jobs = JobQueue(self.events)
        self.repositories = RepositoryRegistry(self.paths)

    def inspect_state(self) -> KnowledgeState:
        return reduce_events(self.events.iter_events())

    def attach_repository(
        self,
        repository_id: str,
        root: str | Path,
        subject: str,
        adapter: str,
    ) -> dict[str, Any]:
        return self.repositories.attach(repository_id, root, subject, adapter)

    def scan_repository(self, repository_id: str) -> dict[str, Any]:
        return scan_repository(self.repositories.get(repository_id))

    def import_repository(self, repository_id: str) -> dict[str, Any]:
        repository = self.repositories.get(repository_id)
        capture = capture_repository(repository)
        snapshot = capture.snapshot
        state = self.inspect_state()
        current_snapshot = state.repository_snapshots.get(repository_id)
        current_content_hash = (
            current_snapshot["contentHash"] if current_snapshot else None
        )
        current_repository_head = state.repository_heads.get(repository_id)
        if current_content_hash == snapshot["contentHash"]:
            prior = state.repository_imports[(repository_id, current_content_hash)]
            entity_ids = list(prior["response"]["entityIds"])
            response = copy.deepcopy(prior["response"])
            response.update(
                created=0,
                updated=0,
                unchanged=len(entity_ids),
                eventCommitted=False,
            )
            return self._synchronize_import_response(response, entity_ids)

        rows = get_adapter(repository["adapter"]).load(
            Path(repository["root"]), snapshot, capture.files
        )
        seen_external_ids: set[str] = set()
        mutations: list[dict[str, Any]] = []
        entity_ids: list[str] = []
        created = 0
        updated = 0
        unchanged = 0
        for row in rows:
            if row.subject != repository["subject"]:
                raise ProtocolError(
                    "REPOSITORY_SUBJECT_MISMATCH",
                    f"entity {row.external_id} does not match repository subject",
                )
            if row.external_id in seen_external_ids:
                raise ProtocolError(
                    "DUPLICATE_EXTERNAL_ID",
                    f"snapshot repeats externalId: {row.external_id}",
                )
            seen_external_ids.add(row.external_id)
            existing = state.find_entity_by_external_ref(
                repository_id, row.external_id
            )
            if existing is None and row.identity_repository_id:
                existing = state.find_entity_by_external_ref(
                    row.identity_repository_id, row.external_id
                )
                if existing and existing["subject"] != row.subject:
                    raise ProtocolError(
                        "REPOSITORY_IDENTITY_SUBJECT_MISMATCH",
                        f"origin identity {row.external_id} belongs to another subject",
                    )
            content_hash = import_entity_content_hash(row)
            current_external_ref = None
            if existing:
                current_external_ref = next(
                    (
                        ref
                        for ref in existing.get("externalRefs", [])
                        if ref["repositoryId"] == repository_id
                        and ref["externalId"] == row.external_id
                    ),
                    None,
                )
            if (
                existing
                and current_external_ref
                and current_external_ref.get("contentHash") == content_hash
            ):
                entity_ids.append(existing["entityId"])
                unchanged += 1
                continue
            mutation = build_import_mutation(
                row, repository, snapshot, existing
            )
            mutations.append(mutation)
            entity_ids.append(mutation["entityId"])
            if existing:
                updated += 1
            else:
                created += 1

        operation_id = _stable_id(
            "op",
            {
                "action": "repository-import",
                "repositoryId": repository_id,
                "fromContentHash": current_content_hash,
                "fromRepositoryHead": current_repository_head,
                "toContentHash": snapshot["contentHash"],
            },
        )
        occurred_at = _utc_now()
        response = {
            "operationId": operation_id,
            "operationStatus": "APPLIED",
            "repositoryId": repository_id,
            "snapshotContentHash": snapshot["contentHash"],
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "imported": len(rows),
            "entityIds": entity_ids,
            "totalEntities": state.entity_count + created,
            "materializationStatus": "PENDING",
            "indexStatus": "STALE",
            "eventCommitted": True,
            "errorCode": None,
        }
        request_id = (
            f"{repository_id}:{current_content_hash or 'NONE'}"
            f":{current_repository_head or 'NONE'}->{snapshot['contentHash']}"
        )
        operation = {
            "operationId": operation_id,
            "candidateId": _stable_id("batch", {"operationId": operation_id}),
            "callerId": "repository-import",
            "requestId": request_id,
            "payloadHash": payload_hash(
                {
                    "repositoryId": repository_id,
                    "fromSnapshotContentHash": current_content_hash,
                    "fromRepositoryHead": current_repository_head,
                    "snapshotContentHash": snapshot["contentHash"],
                }
            ),
            "status": "APPLIED",
            "receivedAt": occurred_at,
            "appliedAt": occurred_at,
            "durableCommandEnvelope": {
                "action": "import",
                "repositoryId": repository_id,
                "fromSnapshotContentHash": current_content_hash,
                "fromRepositoryHead": current_repository_head,
                "snapshotContentHash": snapshot["contentHash"],
            },
            "response": copy.deepcopy(response),
        }
        event = {
            "eventId": new_event_id(),
            "eventType": "REPOSITORY_IMPORT_APPLIED",
            "occurredAt": occurred_at,
            "policyVersion": POLICY_VERSION,
            "operation": operation,
            "repositorySnapshot": snapshot,
            "entityMutations": mutations,
        }
        coordination_key = canonical_json(
            {
                "repositoryId": repository_id,
                "fromRepositoryHead": current_repository_head,
            }
        )
        try:
            committed = self.events.append_operation_once(
                operation_id,
                event,
                coordination_key=coordination_key,
            )
        except OperationClaimConflict:
            response.update(
                operationStatus="CONFLICT",
                created=0,
                updated=0,
                unchanged=0,
                eventCommitted=False,
                errorCode="STALE_REPOSITORY_SNAPSHOT",
            )
            return self._synchronize_import_response(response, [])
        if not committed:
            current = self.inspect_state().operations_by_id[operation_id]
            entity_ids = list(current["response"]["entityIds"])
            response = copy.deepcopy(current["response"])
            response.update(
                created=0,
                updated=0,
                unchanged=len(entity_ids),
                eventCommitted=False,
            )
        else:
            canonical_operation = self.inspect_state().operations_by_id[operation_id]
            if canonical_operation["status"] != "APPLIED":
                response = copy.deepcopy(canonical_operation["response"])
        return self._synchronize_import_response(response, entity_ids)

    def _synchronize_import_response(
        self,
        stable_response: dict[str, Any],
        entity_ids: list[str],
    ) -> dict[str, Any]:
        response = copy.deepcopy(stable_response)
        state = self.inspect_state()
        conflicts: list[dict[str, Any]] = []
        pending = False
        for entity_id in dict.fromkeys(entity_ids):
            try:
                self.materializer.materialize_entity(state.entities[entity_id])
            except ManualEditConflict as exc:
                conflicts.append(
                    {
                        "entityId": entity_id,
                        "errorCode": "MANUAL_EDIT_CONFLICT",
                        "path": str(exc.path),
                    }
                )
            except OSError:
                pending = True
        response["materializationStatus"] = (
            "CONFLICT" if conflicts else "PENDING" if pending else "COMPLETE"
        )
        response["materializationConflicts"] = conflicts

        try:
            self.projection.rebuild(state)
        except (OSError, sqlite3.Error) as exc:
            self.jobs.queue(
                state,
                job_type="REBUILD_PROJECTION",
                last_error=str(exc),
            )
            response["indexStatus"] = "STALE"
        else:
            response["indexStatus"] = "CURRENT"
        response["totalEntities"] = self.inspect_state().entity_count
        return response

    def learn(self, raw_request: dict[str, Any]) -> dict[str, Any]:
        request = validate_learn_request(raw_request)
        digest = payload_hash(request)
        state = self.inspect_state()
        key = (request["callerId"], request["requestId"])
        prior = state.operations_by_key.get(key)
        if prior:
            if prior["payloadHash"] != digest:
                raise ProtocolError(
                    "IDEMPOTENCY_CONFLICT",
                    "callerId and requestId already identify another payload",
                )
            if prior["status"] == "APPLIED":
                return self._synchronize_response(prior["response"])
            request = copy.deepcopy(prior["durableCommandEnvelope"])
        else:
            operation_id = _stable_id(
                "op",
                {"callerId": request["callerId"], "requestId": request["requestId"]},
            )
            candidate_id = _stable_id("can", {"operationId": operation_id})
            received_event = self._received_event(
                request=request,
                digest=digest,
                operation_id=operation_id,
                candidate_id=candidate_id,
            )
            self.events.append(received_event)
            state.apply(received_event)
            prior = state.operations_by_id[operation_id]

        decision = evaluate_learn(request, state)
        applied_event = self._applied_event(prior, decision)
        self.events.append(applied_event)
        return self._synchronize_response(applied_event["operation"]["response"])

    def _synchronize_response(self, stable_response: dict[str, Any]) -> dict[str, Any]:
        response = copy.deepcopy(stable_response)
        state = self.inspect_state()
        entity = state.entities[response["entityId"]]
        try:
            self.materializer.materialize_entity(entity)
        except ManualEditConflict as exc:
            self.jobs.queue(
                state,
                job_type="MATERIALIZE_ENTITY",
                entity_id=entity["entityId"],
                entity_revision=entity["revision"],
                status="BLOCKED",
                last_error=str(exc),
            )
            response["materializationStatus"] = "CONFLICT"
        except OSError as exc:
            self.jobs.queue(
                state,
                job_type="MATERIALIZE_ENTITY",
                entity_id=entity["entityId"],
                entity_revision=entity["revision"],
                last_error=str(exc),
            )
            response["materializationStatus"] = "PENDING"
        else:
            response["materializationStatus"] = "COMPLETE"

        state = self.inspect_state()
        try:
            self.projection.rebuild(state)
        except (OSError, sqlite3.Error) as exc:
            self.jobs.queue(
                state,
                job_type="REBUILD_PROJECTION",
                last_error=str(exc),
            )
            response["indexStatus"] = "STALE"
        else:
            response["indexStatus"] = "CURRENT"
        return response

    def query(self, raw_request: dict[str, Any]) -> dict[str, Any]:
        request = validate_query_request(raw_request)
        state = self.inspect_state()
        index_status = "CURRENT"
        use_projection = self.projection.is_current(state.event_ids)
        if not use_projection:
            try:
                self.projection.rebuild(state)
            except (OSError, sqlite3.Error):
                index_status = "STALE"
            else:
                use_projection = True

        mode = request["mode"]
        if mode == "search":
            if use_projection:
                results = self.projection.search(request["query"], request.get("filters", {}))
            else:
                results = Projection.search_entities(
                    state.entities.values(), request["query"], request.get("filters", {})
                )
            return {
                "status": "OK" if results else "LEARN_REQUIRED",
                "indexStatus": index_status,
                "results": results,
            }

        if mode == "entity":
            entity = (
                self.projection.get_entity(request["entityId"])
                if use_projection
                else copy.deepcopy(state.entities.get(request["entityId"]))
            )
            return {
                "status": "OK" if entity else "NOT_FOUND",
                "indexStatus": index_status,
                "entity": entity,
            }

        operation_id = request.get("operationId")
        if use_projection:
            operation = self.projection.get_operation(
                operation_id=operation_id,
                caller_id=request.get("callerId"),
                request_id=request.get("targetRequestId"),
            )
        elif operation_id:
            operation = copy.deepcopy(state.operations_by_id.get(operation_id))
        else:
            operation = copy.deepcopy(
                state.operations_by_key.get(
                    (request["callerId"], request["targetRequestId"])
                )
            )
        return {
            "status": "OK" if operation else "NOT_FOUND",
            "indexStatus": index_status,
            "operation": operation,
        }

    def rebuild(self) -> dict[str, Any]:
        state = self.inspect_state()
        materialization = self.materializer.materialize_all(state.entities)
        self.projection.rebuild(state)
        return {
            "indexStatus": "CURRENT",
            "materializationStatus": (
                "CONFLICT" if materialization.conflicts else "COMPLETE"
            ),
            "materialized": materialization.materialized,
            "conflicts": materialization.conflicts,
        }

    def repair(self) -> dict[str, Any]:
        state = self.inspect_state()
        requeued = self.jobs.requeue_running(state)
        processed: list[dict[str, str]] = []
        state = self.inspect_state()
        for job_id in sorted(state.jobs):
            job = state.jobs[job_id]
            if job["status"] != "QUEUED":
                continue
            if job["jobType"] not in {"MATERIALIZE_ENTITY", "REBUILD_PROJECTION"}:
                continue
            running = self.jobs.transition(job, "RUNNING")
            try:
                current_state = self.inspect_state()
                if job["jobType"] == "MATERIALIZE_ENTITY":
                    entity = current_state.entities[job["entityId"]]
                    self.materializer.materialize_entity(entity)
                else:
                    self.projection.rebuild(current_state)
            except ManualEditConflict as exc:
                final_job = self.jobs.transition(
                    running, "BLOCKED", last_error=str(exc)
                )
            except (OSError, sqlite3.Error, KeyError) as exc:
                status = (
                    "EXHAUSTED"
                    if running["attempts"] >= running.get("maxAttempts", 3)
                    else "QUEUED"
                )
                final_job = self.jobs.transition(
                    running, status, last_error=str(exc)
                )
            else:
                final_job = self.jobs.transition(running, "COMPLETED")
            processed.append({"jobId": job_id, "status": final_job["status"]})

        final_state = self.inspect_state()
        materialization_status = "COMPLETE"
        try:
            materialization = self.materializer.materialize_all(final_state.entities)
        except OSError as exc:
            materialization_status = "PENDING"
            self.jobs.queue(
                final_state,
                job_type="MATERIALIZE_ALL",
                last_error=str(exc),
            )
            materialization_conflicts: list[dict[str, Any]] = []
        else:
            materialization_conflicts = materialization.conflicts
            if materialization_conflicts:
                materialization_status = "CONFLICT"

        final_state = self.inspect_state()
        try:
            self.projection.rebuild(final_state)
        except (OSError, sqlite3.Error) as exc:
            self.jobs.queue(
                final_state,
                job_type="REBUILD_PROJECTION",
                last_error=str(exc),
            )
            index_status = "STALE"
        else:
            index_status = "CURRENT"
        return {
            "indexStatus": index_status,
            "materializationStatus": materialization_status,
            "requeued": requeued,
            "processed": processed,
            "conflicts": materialization_conflicts,
        }

    @staticmethod
    def _received_event(
        *,
        request: dict[str, Any],
        digest: str,
        operation_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        occurred_at = _utc_now()
        return {
            "eventId": new_event_id(),
            "eventType": "OPERATION_RECEIVED",
            "occurredAt": occurred_at,
            "policyVersion": POLICY_VERSION,
            "operation": {
                "operationId": operation_id,
                "candidateId": candidate_id,
                "callerId": request["callerId"],
                "requestId": request["requestId"],
                "payloadHash": digest,
                "status": "RECEIVED",
                "receivedAt": occurred_at,
                "durableCommandEnvelope": copy.deepcopy(request),
            },
        }

    @staticmethod
    def _applied_event(
        prior_operation: dict[str, Any],
        decision: LearnDecision,
    ) -> dict[str, Any]:
        occurred_at = _utc_now()
        response = {
            "operationId": prior_operation["operationId"],
            "candidateId": prior_operation["candidateId"],
            "decision": decision.decision,
            "entityId": decision.entity["entityId"],
            "entityRevision": decision.new_revision,
            "operationStatus": "APPLIED",
            "knowledgeStatus": decision.knowledge_status,
            "materializationStatus": "PENDING",
            "indexStatus": "STALE",
            "errorCode": None,
        }
        operation = copy.deepcopy(prior_operation)
        operation.update(status="APPLIED", appliedAt=occurred_at, response=response)
        entity_mutation = {
            **copy.deepcopy(decision.entity),
            "parentRevision": decision.parent_revision,
            "newRevision": decision.new_revision,
            "aliasesAdded": copy.deepcopy(decision.aliases_added),
            "normalizedAliasesAdded": copy.deepcopy(
                decision.normalized_aliases_added
            ),
            "claim": copy.deepcopy(decision.claim),
            "sources": copy.deepcopy(decision.sources),
            "artifacts": copy.deepcopy(decision.artifacts),
            "evidenceLinks": copy.deepcopy(decision.evidence_links),
            "observation": copy.deepcopy(decision.observation),
            "knowledgeStatus": decision.knowledge_status,
        }
        return {
            "eventId": new_event_id(),
            "eventType": "OPERATION_APPLIED",
            "occurredAt": occurred_at,
            "policyVersion": POLICY_VERSION,
            "operation": operation,
            "entityMutation": entity_mutation,
            "jobsQueued": copy.deepcopy(decision.jobs),
        }
