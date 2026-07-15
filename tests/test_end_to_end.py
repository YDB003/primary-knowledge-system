from __future__ import annotations

from pathlib import Path

from pks.service import KnowledgeService


def test_missing_query_learn_query_and_full_rebuild(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    query = {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": "静夜思",
        "filters": {"subject": "chinese"},
    }
    assert service.query(query)["status"] == "LEARN_REQUIRED"
    events_before = len(list(service.events.iter_events()))
    learn = {
        "callerId": "homework-agent",
        "requestId": "poem-1",
        "schemaVersion": "1.0",
        "query": "静夜思的知识内容",
        "candidate": {
            "title": "静夜思",
            "entityType": "poem",
            "answer": "原文、词义、思想情感和考点共同构成该诗的知识内容。",
            "aliases": [],
        },
        "sources": [],
        "context": {"subject": "chinese", "actualStudyGrade": 2},
    }

    created = service.learn(learn)
    found = service.query(query)

    assert created["materializationStatus"] == "COMPLETE"
    assert created["indexStatus"] == "CURRENT"
    assert found["results"][0]["entityType"] == "poem"
    assert len(list(service.events.iter_events())) > events_before
    note = tmp_path / "knowledge" / "chinese" / f"{created['entityId']}.md"
    assert "思想情感" in note.read_text(encoding="utf-8")

    before = service.query(query)
    service.paths.sqlite.unlink()
    service.rebuild()
    after = service.query(query)
    assert after == before
    assert service.learn(learn) == created
