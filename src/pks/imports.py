from __future__ import annotations

from dataclasses import dataclass
import hashlib
from collections.abc import Mapping
from typing import Any, Protocol
from pathlib import Path

from .contracts import canonical_json, payload_hash
from .policy import normalize_identity


@dataclass(frozen=True)
class ImportEntity:
    external_id: str
    title: str
    aliases: tuple[str, ...]
    subject: str
    entity_type: str
    grade_start: int | None
    grade_end: int | None
    domain: str | None
    claim_value: str
    source_refs: tuple[str, ...]
    source_path: str
    import_metadata: dict[str, Any]
    knowledge_status: str = "ACCEPTED"
    identity_repository_id: str | None = None


class SubjectAdapter(Protocol):
    name: str

    def source_files(self) -> tuple[str, ...]: ...

    def load(
        self,
        root: Path,
        snapshot: dict[str, Any],
        captured_files: Mapping[str, bytes] | None = None,
    ) -> list[ImportEntity]: ...


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def import_entity_content_hash(entity: ImportEntity) -> str:
    return payload_hash(entity.import_metadata)


def build_import_mutation(
    entity: ImportEntity,
    repository: dict[str, Any],
    snapshot: dict[str, Any],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    repository_id = repository["repositoryId"]
    entity_id = (
        existing["entityId"]
        if existing
        else _stable_id(
            "ent",
            {"repositoryId": repository_id, "externalId": entity.external_id},
        )
    )
    content_hash = import_entity_content_hash(entity)
    field_path = f"repositoryContent:{repository_id}"
    claim_id = _stable_id(
        "clm",
        {
            "entityId": entity_id,
            "fieldPath": field_path,
            "value": entity.claim_value,
        },
    )
    source_id = _stable_id("src", {"repositoryId": repository_id})
    aliases = list(entity.aliases)
    return {
        "entityId": entity_id,
        "title": entity.title,
        "normalizedTitle": normalize_identity(entity.title),
        "subject": entity.subject,
        "entityType": entity.entity_type,
        "parentRevision": existing["revision"] if existing else 0,
        "newRevision": existing["revision"] + 1 if existing else 1,
        "aliasesAdded": aliases,
        "normalizedAliasesAdded": [normalize_identity(alias) for alias in aliases],
        "claim": {
            "claimId": claim_id,
            "fieldPath": field_path,
            "value": entity.claim_value,
            "state": (
                "ACCEPTED"
                if entity.knowledge_status == "ACCEPTED"
                else "PROVISIONAL"
            ),
        },
        "replaceClaimFieldPath": field_path,
        "sources": [
            {
                "sourceId": source_id,
                "title": repository_id,
                "publisher": "Local knowledge repository",
                "url": None,
                "grade": "C",
                "externalSourceRefs": list(entity.source_refs),
            }
        ],
        "artifacts": [],
        "evidenceLinks": [],
        "observation": None,
        "knowledgeStatus": entity.knowledge_status,
        "authoritativeImport": True,
        "externalRefs": [
            {
                "repositoryId": repository_id,
                "externalId": entity.external_id,
                "sourcePath": entity.source_path,
                "snapshotRevision": snapshot["snapshotRevision"],
                "contentHash": content_hash,
            }
        ],
        "gradeStart": entity.grade_start,
        "gradeEnd": entity.grade_end,
        "domain": entity.domain,
        "importMetadata": entity.import_metadata,
    }
