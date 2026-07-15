from __future__ import annotations

import copy
import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any

from .contracts import canonical_json
from .state import KnowledgeState


POLICY_VERSION = "1.0"
DEFAULT_ENTITY_TYPES = {
    "math": "concept",
    "chinese": "concept",
    "english": "concept",
}
ALLOWED_ENTITY_TYPES = {
    "math": {"concept", "procedure", "skill", "problem_type", "model"},
    "chinese": {"character", "word", "idiom", "poem", "text", "concept", "skill"},
    "english": {"letter", "phoneme", "word", "phrase", "grammar", "skill", "concept"},
}


def normalize_identity(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(normalized.split())


def source_grade(source: dict[str, Any]) -> str:
    required = (source.get("title"), source.get("publisher"), source.get("excerpt"))
    if all(isinstance(value, str) and value.strip() for value in required):
        return "C"
    return "D"


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


@dataclass(frozen=True)
class LearnDecision:
    decision: str
    entity: dict[str, Any]
    parent_revision: int
    new_revision: int
    aliases_added: list[str]
    normalized_aliases_added: list[str]
    claim: dict[str, Any]
    sources: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    evidence_links: list[dict[str, Any]]
    observation: dict[str, Any] | None
    knowledge_status: str
    jobs: list[dict[str, Any]]


def _resolve_entity(
    request: dict[str, Any],
    state: KnowledgeState,
) -> tuple[dict[str, Any], int, list[str], list[str]]:
    subject = request["context"]["subject"]
    candidate = request["candidate"]
    title = candidate["title"].strip()
    identities = [title, *candidate.get("aliases", [])]
    existing = None
    for identity in identities:
        existing = state.find_exact_entity(normalize_identity(identity), subject)
        if existing:
            break

    requested_type = candidate.get("entityType", DEFAULT_ENTITY_TYPES[subject])
    entity_type = (
        requested_type
        if requested_type in ALLOWED_ENTITY_TYPES[subject]
        else DEFAULT_ENTITY_TYPES[subject]
    )
    if existing:
        entity = {
            "entityId": existing["entityId"],
            "title": existing["title"],
            "normalizedTitle": existing["normalizedTitle"],
            "subject": existing["subject"],
            "entityType": existing["entityType"],
        }
        existing_identities = {
            existing["normalizedTitle"],
            *existing.get("normalizedAliases", []),
        }
        aliases_added: list[str] = []
        normalized_added: list[str] = []
        for alias in identities:
            normalized = normalize_identity(alias)
            if normalized not in existing_identities:
                aliases_added.append(alias.strip())
                normalized_added.append(normalized)
                existing_identities.add(normalized)
        return entity, existing["revision"], aliases_added, normalized_added

    entity = {
        "entityId": _stable_id(
            "ent", {"subject": subject, "identity": normalize_identity(title)}
        ),
        "title": title,
        "normalizedTitle": normalize_identity(title),
        "subject": subject,
        "entityType": entity_type,
    }
    aliases_added = []
    normalized_added = []
    seen = {entity["normalizedTitle"]}
    for alias in candidate.get("aliases", []):
        normalized = normalize_identity(alias)
        if normalized not in seen:
            aliases_added.append(alias.strip())
            normalized_added.append(normalized)
            seen.add(normalized)
    return entity, 0, aliases_added, normalized_added


def _build_evidence(
    request: dict[str, Any],
    entity_id: str,
    claim_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    evidence_links: list[dict[str, Any]] = []
    for source in request.get("sources", []):
        source_identity = {
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "url": source.get("url"),
        }
        source_id = _stable_id("src", source_identity)
        source_record = {
            "sourceId": source_id,
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "url": source.get("url"),
            "grade": source_grade(source),
        }
        sources.append(source_record)

        artifact_identity = {
            "callerId": request["callerId"],
            "sourceRef": source["sourceRef"],
            "excerpt": source.get("excerpt"),
        }
        artifact_id = _stable_id("art", artifact_identity)
        artifact_record = {
            "artifactId": artifact_id,
            "sourceId": source_id,
            "sourceRef": source["sourceRef"],
            "excerpt": source.get("excerpt"),
            "contentHash": _stable_id("sha256", source.get("excerpt") or ""),
        }
        artifacts.append(artifact_record)

        evidence_links.append(
            {
                "evidenceLinkId": _stable_id(
                    "evl", {"claimId": claim_id, "artifactId": artifact_id, "stance": "SUPPORTS"}
                ),
                "claimId": claim_id,
                "artifactId": artifact_id,
                "stance": "SUPPORTS",
            }
        )
    return sources, artifacts, evidence_links


def evaluate_learn(request: dict[str, Any], state: KnowledgeState) -> LearnDecision:
    request = copy.deepcopy(request)
    entity, parent_revision, aliases_added, normalized_aliases_added = _resolve_entity(
        request, state
    )
    new_revision = parent_revision + 1
    answer = request["candidate"]["answer"].strip()
    field_path = "knowledgeContent"
    claim_id = _stable_id(
        "clm",
        {"entityId": entity["entityId"], "fieldPath": field_path, "value": answer},
    )
    sources, artifacts, evidence_links = _build_evidence(
        request, entity["entityId"], claim_id
    )
    has_accepted_source = any(source["grade"] in {"A", "B", "C"} for source in sources)
    claim_state = "ACCEPTED" if has_accepted_source else "PROVISIONAL"
    knowledge_status = "ACCEPTED" if has_accepted_source else "EVIDENCE_PENDING"
    claim = {
        "claimId": claim_id,
        "fieldPath": field_path,
        "value": answer,
        "state": claim_state,
    }

    grade = request["context"].get("actualStudyGrade")
    observation = None
    if grade is not None:
        observation = {
            "observationId": _stable_id(
                "obs",
                {
                    "callerId": request["callerId"],
                    "requestId": request["requestId"],
                    "entityId": entity["entityId"],
                    "actualStudyGrade": grade,
                },
            ),
            "observationType": "ACTUAL_STUDY_GRADE",
            "actualStudyGrade": grade,
            "task": request["context"].get("task"),
            "textbookRef": request["context"].get("textbookRef"),
        }

    jobs: list[dict[str, Any]] = []
    if knowledge_status == "EVIDENCE_PENDING":
        job_fingerprint = {
            "jobType": "EVIDENCE_ENRICHMENT",
            "entityId": entity["entityId"],
            "entityRevision": new_revision,
            "policyVersion": POLICY_VERSION,
        }
        jobs.append(
            {
                "jobId": _stable_id("job", job_fingerprint),
                **job_fingerprint,
                "status": "QUEUED",
                "attempts": 0,
            }
        )

    return LearnDecision(
        decision="CREATED" if parent_revision == 0 else "ENRICHED",
        entity=entity,
        parent_revision=parent_revision,
        new_revision=new_revision,
        aliases_added=aliases_added,
        normalized_aliases_added=normalized_aliases_added,
        claim=claim,
        sources=sources,
        artifacts=artifacts,
        evidence_links=evidence_links,
        observation=observation,
        knowledge_status=knowledge_status,
        jobs=jobs,
    )
