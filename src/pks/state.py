from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class KnowledgeState:
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    operations_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    operations_by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    merged_branches: list[dict[str, Any]] = field(default_factory=list)
    repository_imports: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    repository_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    repository_heads: dict[str, str] = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def find_exact_entity(self, normalized_identity: str, subject: str) -> dict[str, Any] | None:
        for entity in self.entities.values():
            if entity["subject"] != subject:
                continue
            identities = {entity["normalizedTitle"], *entity.get("normalizedAliases", [])}
            if normalized_identity in identities:
                return entity
        return None

    def find_entity_by_external_ref(
        self,
        repository_id: str,
        external_id: str,
    ) -> dict[str, Any] | None:
        for entity in self.entities.values():
            for external_ref in entity.get("externalRefs", []):
                if (
                    external_ref.get("repositoryId") == repository_id
                    and external_ref.get("externalId") == external_id
                ):
                    return entity
        return None

    def apply(self, event: dict[str, Any]) -> None:
        event_id = event["eventId"]
        if event_id in self.event_ids:
            return
        self.event_ids.add(event_id)

        event_type = event.get("eventType")
        if event_type in {"OPERATION_RECEIVED", "OPERATION_APPLIED"}:
            should_apply_mutation = self._apply_operation(event)
            mutation = event.get("entityMutation")
            if mutation and should_apply_mutation:
                self._apply_entity_mutation(event_id, mutation)
                for job in copy.deepcopy(event.get("jobsQueued", [])):
                    self.jobs.setdefault(job["jobId"], job)
        elif event_type == "REPOSITORY_IMPORT_APPLIED":
            should_apply_mutation = self._apply_operation(event)
            if should_apply_mutation:
                snapshot = copy.deepcopy(event["repositorySnapshot"])
                repository_id = snapshot["repositoryId"]
                command = event["operation"].get("durableCommandEnvelope", {})
                expected_head_present = "fromRepositoryHead" in command
                expected_head = command.get("fromRepositoryHead")
                current_head = self.repository_heads.get(repository_id)
                if expected_head_present and expected_head != current_head:
                    self._mark_repository_import_conflict(
                        event,
                        "STALE_REPOSITORY_SNAPSHOT",
                    )
                    return
                entities_before = copy.deepcopy(self.entities)
                merged_branches_before = copy.deepcopy(self.merged_branches)
                mutation_results = [
                    self._apply_entity_mutation(event_id, mutation)
                    for mutation in event.get("entityMutations", [])
                ]
                if not all(mutation_results):
                    self.entities = entities_before
                    self.merged_branches = merged_branches_before
                    self._mark_repository_import_conflict(
                        event,
                        "REVISION_CONFLICT",
                    )
                    return
                content_hash = snapshot["contentHash"]
                self.repository_snapshots[repository_id] = snapshot
                self.repository_heads[repository_id] = event["operation"]["operationId"]
                self.repository_imports[(repository_id, content_hash)] = copy.deepcopy(
                    event["operation"]
                )
        elif event_type == "JOB_STATUS":
            job = copy.deepcopy(event["job"])
            self.jobs[job["jobId"]] = job

    def _apply_operation(self, event: dict[str, Any]) -> bool:
        operation = copy.deepcopy(event["operation"])
        operation_id = operation["operationId"]
        key = (operation["callerId"], operation["requestId"])
        existing = self.operations_by_key.get(key)
        if existing:
            same_identity = (
                existing["operationId"] == operation_id
                and existing["payloadHash"] == operation["payloadHash"]
            )
            if not same_identity:
                self.conflicts.append(
                    {
                        "errorCode": "IDEMPOTENCY_LEDGER_CONFLICT",
                        "eventId": event["eventId"],
                        "operationId": operation_id,
                    }
                )
                return False
            if existing["status"] == "APPLIED":
                if operation["status"] == "APPLIED" and existing.get(
                    "response"
                ) != operation.get("response"):
                    self.conflicts.append(
                        {
                            "errorCode": "OPERATION_RESULT_CONFLICT",
                            "eventId": event["eventId"],
                            "operationId": operation_id,
                        }
                    )
                return False
        self.operations_by_id[operation_id] = operation
        self.operations_by_key[key] = operation
        return operation["status"] == "APPLIED"

    def _apply_entity_mutation(self, event_id: str, mutation: dict[str, Any]) -> bool:
        incoming = copy.deepcopy(mutation)
        entity_id = incoming["entityId"]
        parent_revision = incoming["parentRevision"]
        current = self.entities.get(entity_id)

        if current is None:
            if parent_revision != 0 or incoming["newRevision"] != 1:
                self._record_revision_conflict(event_id, incoming, None)
                return False
            self.entities[entity_id] = self._new_entity(incoming)
            return True

        current_revision = current["revision"]
        if parent_revision == current_revision:
            if incoming["newRevision"] != current_revision + 1:
                self._record_revision_conflict(event_id, incoming, current)
                return False
            self._merge_delta(current, incoming)
            current["revision"] = incoming["newRevision"]
            return True

        if parent_revision < current_revision and self._branch_is_nonconflicting(
            current, incoming
        ):
            self._merge_delta(current, incoming)
            current["revision"] = current_revision + 1
            self.merged_branches.append(
                {
                    "incomingEventId": event_id,
                    "entityId": entity_id,
                    "parentRevision": parent_revision,
                    "canonicalRevision": current["revision"],
                }
            )
            return True

        self._record_revision_conflict(event_id, incoming, current)
        return False

    def _mark_repository_import_conflict(
        self,
        event: dict[str, Any],
        error_code: str,
    ) -> None:
        operation_id = event["operation"]["operationId"]
        operation = self.operations_by_id[operation_id]
        operation["status"] = "CONFLICT"
        response = operation.get("response")
        if isinstance(response, dict):
            response.update(
                operationStatus="CONFLICT",
                created=0,
                updated=0,
                unchanged=0,
                errorCode=error_code,
            )
        key = (operation["callerId"], operation["requestId"])
        self.operations_by_key[key] = operation
        self.conflicts.append(
            {
                "errorCode": error_code,
                "eventId": event["eventId"],
                "operationId": operation_id,
            }
        )

    @staticmethod
    def _new_entity(mutation: dict[str, Any]) -> dict[str, Any]:
        aliases = list(dict.fromkeys(mutation.get("aliasesAdded", [])))
        normalized_aliases = list(dict.fromkeys(mutation.get("normalizedAliasesAdded", [])))
        claim = copy.deepcopy(mutation.get("claim"))
        observation = copy.deepcopy(mutation.get("observation"))
        return {
            "entityId": mutation["entityId"],
            "title": mutation["title"],
            "normalizedTitle": mutation["normalizedTitle"],
            "subject": mutation["subject"],
            "entityType": mutation["entityType"],
            "revision": mutation["newRevision"],
            "aliases": aliases,
            "normalizedAliases": normalized_aliases,
            "claims": [claim] if claim else [],
            "sources": copy.deepcopy(mutation.get("sources", [])),
            "artifacts": copy.deepcopy(mutation.get("artifacts", [])),
            "evidenceLinks": copy.deepcopy(mutation.get("evidenceLinks", [])),
            "observations": [observation] if observation else [],
            "knowledgeStatus": mutation["knowledgeStatus"],
            "externalRefs": copy.deepcopy(mutation.get("externalRefs", [])),
            "gradeStart": mutation.get("gradeStart"),
            "gradeEnd": mutation.get("gradeEnd"),
            "domain": mutation.get("domain"),
            "importMetadata": copy.deepcopy(mutation.get("importMetadata", {})),
        }

    @staticmethod
    def _merge_delta(entity: dict[str, Any], mutation: dict[str, Any]) -> None:
        for key in (
            "title",
            "normalizedTitle",
            "subject",
            "entityType",
            "gradeStart",
            "gradeEnd",
            "domain",
            "importMetadata",
        ):
            if key in mutation:
                entity[key] = copy.deepcopy(mutation[key])

        for key, normalized_key in (
            ("aliasesAdded", "aliases"),
            ("normalizedAliasesAdded", "normalizedAliases"),
        ):
            for value in mutation.get(key, []):
                if value not in entity[normalized_key]:
                    entity[normalized_key].append(value)

        replace_field_path = mutation.get("replaceClaimFieldPath")
        if replace_field_path:
            entity["claims"] = [
                current
                for current in entity["claims"]
                if current["fieldPath"] != replace_field_path
            ]

        claim = copy.deepcopy(mutation.get("claim"))
        if claim and not any(item["claimId"] == claim["claimId"] for item in entity["claims"]):
            if not any(
                item["fieldPath"] == claim["fieldPath"] and item["value"] == claim["value"]
                for item in entity["claims"]
            ):
                entity["claims"].append(claim)

        incoming_sources = copy.deepcopy(mutation.get("sources", []))
        if mutation.get("authoritativeImport"):
            incoming_by_id = {item["sourceId"]: item for item in incoming_sources}
            entity["sources"] = [
                incoming_by_id.pop(item["sourceId"], item)
                for item in entity["sources"]
            ]
            entity["sources"].extend(incoming_by_id.values())
        else:
            existing_sources = {item["sourceId"] for item in entity["sources"]}
            for source in incoming_sources:
                if source["sourceId"] not in existing_sources:
                    entity["sources"].append(source)
                    existing_sources.add(source["sourceId"])

        existing_artifacts = {item["artifactId"] for item in entity["artifacts"]}
        for artifact in copy.deepcopy(mutation.get("artifacts", [])):
            if artifact["artifactId"] not in existing_artifacts:
                entity["artifacts"].append(artifact)
                existing_artifacts.add(artifact["artifactId"])

        existing_links = {
            item["evidenceLinkId"] for item in entity["evidenceLinks"]
        }
        for link in copy.deepcopy(mutation.get("evidenceLinks", [])):
            if link["evidenceLinkId"] not in existing_links:
                entity["evidenceLinks"].append(link)
                existing_links.add(link["evidenceLinkId"])

        observation = copy.deepcopy(mutation.get("observation"))
        if observation and not any(
            item["observationId"] == observation["observationId"]
            for item in entity["observations"]
        ):
            entity["observations"].append(observation)

        external_refs_by_key = {
            (item["repositoryId"], item["externalId"]): item
            for item in entity.get("externalRefs", [])
        }
        for external_ref in copy.deepcopy(mutation.get("externalRefs", [])):
            external_refs_by_key[
                (external_ref["repositoryId"], external_ref["externalId"])
            ] = external_ref
        entity["externalRefs"] = list(external_refs_by_key.values())

        if mutation.get("authoritativeImport"):
            entity["knowledgeStatus"] = mutation["knowledgeStatus"]
        elif mutation.get("knowledgeStatus") == "ACCEPTED":
            entity["knowledgeStatus"] = "ACCEPTED"

    @staticmethod
    def _branch_is_nonconflicting(entity: dict[str, Any], mutation: dict[str, Any]) -> bool:
        claim = mutation.get("claim")
        if not claim:
            return True
        for current_claim in entity["claims"]:
            if current_claim["fieldPath"] != claim["fieldPath"]:
                continue
            return current_claim["value"] == claim["value"]
        return True

    def _record_revision_conflict(
        self,
        event_id: str,
        mutation: dict[str, Any],
        current: dict[str, Any] | None,
    ) -> None:
        self.conflicts.append(
            {
                "errorCode": "REVISION_CONFLICT",
                "incomingEventId": event_id,
                "entityId": mutation["entityId"],
                "parentRevision": mutation["parentRevision"],
                "currentRevision": current["revision"] if current else None,
                "incomingMutation": copy.deepcopy(mutation),
            }
        )


def reduce_events(events: Iterable[dict[str, Any]]) -> KnowledgeState:
    state = KnowledgeState()
    for event in sorted(events, key=lambda item: item["eventId"]):
        state.apply(event)
    return state
