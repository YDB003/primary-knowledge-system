from __future__ import annotations

from pathlib import Path

from pks.projection import Projection
from pks.service import KnowledgeService


def request_with_alias() -> dict:
    return {
        "callerId": "agent-a",
        "requestId": "request-1",
        "schemaVersion": "1.0",
        "query": "What is a fraction?",
        "candidate": {
            "title": "Fraction",
            "answer": "A fraction represents a part of a whole.",
            "aliases": ["fraction concept"],
        },
        "sources": [],
        "context": {"subject": "math", "actualStudyGrade": 3},
    }


def test_rebuild_projects_complete_canonical_state(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    response = service.learn(request_with_alias())
    state = service.inspect_state()
    projection = Projection(service.paths)

    projection.rebuild(state)

    assert projection.is_current(state.event_ids)
    entity = projection.get_entity(response["entityId"])
    assert entity["title"] == "Fraction"
    assert entity["revision"] == 1
    assert entity["aliases"] == ["fraction concept"]
    assert entity["claims"][0]["value"].startswith("A fraction")
    assert len(entity["observations"]) == 1
    operation = projection.get_operation(operation_id=response["operationId"])
    assert operation["response"]["entityId"] == response["entityId"]
    assert len(projection.list_jobs()) == 1


def test_projection_search_ranks_exact_alias_and_text_matches(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    service.learn(request_with_alias())
    projection = Projection(service.paths)
    projection.rebuild(service.inspect_state())

    exact = projection.search("Fraction", {})
    alias = projection.search("fraction concept", {})
    text = projection.search("frac", {})

    assert exact[0]["matchType"] == "EXACT"
    assert alias[0]["matchType"] == "ALIAS"
    assert text[0]["matchType"] == "TEXT"


def test_projection_filters_subject_and_entity_type(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    service.learn(request_with_alias())
    projection = Projection(service.paths)
    projection.rebuild(service.inspect_state())

    assert projection.search("fraction", {"subject": "math", "entityType": "concept"})
    assert projection.search("fraction", {"subject": "english"}) == []
    assert projection.search("fraction", {"entityType": "skill"}) == []


def test_projection_staleness_detects_new_ledger_event(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    service.learn(request_with_alias())
    projection = Projection(service.paths)
    first_state = service.inspect_state()
    projection.rebuild(first_state)
    service.jobs.queue(first_state, job_type="REBUILD_PROJECTION")

    assert not projection.is_current(service.inspect_state().event_ids)


def test_deleted_projection_rebuilds_same_entity_and_operation(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    response = service.learn(request_with_alias())
    first_state = service.inspect_state()
    projection = Projection(service.paths)
    projection.rebuild(first_state)
    before_entity = projection.get_entity(response["entityId"])
    before_operation = projection.get_operation(operation_id=response["operationId"])
    service.paths.sqlite.unlink()

    projection.rebuild(service.inspect_state())

    assert projection.get_entity(response["entityId"]) == before_entity
    assert projection.get_operation(operation_id=response["operationId"]) == before_operation
