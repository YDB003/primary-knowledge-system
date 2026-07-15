from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pks.contracts import ProtocolError
from pks.service import KnowledgeService


def sourced_request() -> dict:
    return {
        "callerId": "agent-a",
        "requestId": "request-1",
        "schemaVersion": "1.0",
        "query": "What is a fraction?",
        "candidate": {
            "title": "Fraction",
            "answer": "A fraction represents a part of a whole.",
            "aliases": [],
        },
        "sources": [
            {
                "sourceRef": "source-1",
                "title": "Primary mathematics guide",
                "url": None,
                "publisher": "Education Publisher",
                "excerpt": "A fraction can represent part of a whole.",
            }
        ],
        "context": {
            "subject": "math",
            "actualStudyGrade": 3,
            "textbookRef": None,
            "task": "homework-check",
        },
    }


def test_learn_creates_auditable_entity(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)

    response = service.learn(sourced_request())
    state = service.inspect_state()

    assert response["decision"] == "CREATED"
    assert response["operationStatus"] == "APPLIED"
    assert response["knowledgeStatus"] == "ACCEPTED"
    assert response["materializationStatus"] == "COMPLETE"
    assert response["indexStatus"] == "CURRENT"
    assert response["entityRevision"] == 1
    entity = state.entities[response["entityId"]]
    assert entity["title"] == "Fraction"
    assert entity["claims"][0]["state"] == "ACCEPTED"
    assert len(entity["sources"]) == 1
    assert len(entity["artifacts"]) == 1
    assert len(entity["evidenceLinks"]) == 1
    assert len(entity["observations"]) == 1
    assert len(list(service.events.iter_events())) == 2
    assert (tmp_path / "knowledge" / "math" / f"{response['entityId']}.md").exists()


def test_two_agents_enrich_one_entity(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    first = service.learn(sourced_request())
    second_request = copy.deepcopy(sourced_request())
    second_request["callerId"] = "agent-b"
    second_request["requestId"] = "request-2"
    second_request["candidate"]["aliases"] = ["fraction concept"]

    second = service.learn(second_request)

    assert second["entityId"] == first["entityId"]
    assert second["decision"] == "ENRICHED"
    assert second["entityRevision"] == 2
    state = service.inspect_state()
    assert state.entity_count == 1
    assert state.entities[first["entityId"]]["aliases"] == ["fraction concept"]
    assert len(state.operations_by_id) == 2


def test_same_idempotency_key_and_payload_returns_original_result(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    request = sourced_request()
    first = service.learn(request)
    event_count = len(list(service.events.iter_events()))

    second = service.learn(copy.deepcopy(request))

    assert second == first
    assert len(list(service.events.iter_events())) == event_count


def test_same_idempotency_key_with_changed_payload_conflicts(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    request = sourced_request()
    service.learn(request)
    request["candidate"]["answer"] = "Changed answer"
    event_count = len(list(service.events.iter_events()))

    with pytest.raises(ProtocolError, match="IDEMPOTENCY_CONFLICT"):
        service.learn(request)

    assert len(list(service.events.iter_events())) == event_count


def test_received_event_can_resume_after_apply_failure(tmp_path: Path, monkeypatch) -> None:
    service = KnowledgeService(tmp_path)
    original_append = service.events.append

    def fail_applied(event: dict):
        if event["eventType"] == "OPERATION_APPLIED":
            raise OSError("injected failure")
        return original_append(event)

    monkeypatch.setattr(service.events, "append", fail_applied)
    with pytest.raises(OSError, match="injected failure"):
        service.learn(sourced_request())

    interrupted = service.inspect_state()
    operation = interrupted.operations_by_key[("agent-a", "request-1")]
    assert operation["status"] == "RECEIVED"
    assert operation["durableCommandEnvelope"]["candidate"]["answer"].startswith("A fraction")

    recovered = KnowledgeService(tmp_path)
    response = recovered.learn(sourced_request())

    assert response["operationId"] == operation["operationId"]
    assert response["operationStatus"] == "APPLIED"
    assert recovered.inspect_state().entity_count == 1


def test_operation_and_entity_ids_are_deterministic_across_interrupted_evaluation(
    tmp_path: Path,
) -> None:
    request = sourced_request()
    first_service = KnowledgeService(tmp_path / "one")
    second_service = KnowledgeService(tmp_path / "two")

    first = first_service.learn(request)
    second = second_service.learn(request)

    assert first["operationId"] == second["operationId"]
    assert first["entityId"] == second["entityId"]


def test_source_free_learn_creates_rebuildable_enrichment_job(tmp_path: Path) -> None:
    request = sourced_request()
    request["sources"] = []
    service = KnowledgeService(tmp_path)

    response = service.learn(request)
    state = service.inspect_state()

    assert response["knowledgeStatus"] == "EVIDENCE_PENDING"
    assert len(state.jobs) == 1
    job = next(iter(state.jobs.values()))
    assert job["jobType"] == "EVIDENCE_ENRICHMENT"
    assert job["status"] == "QUEUED"
