from __future__ import annotations

from pathlib import Path

from pks.service import KnowledgeService


def learn_request() -> dict:
    return {
        "callerId": "agent-a",
        "requestId": "request-1",
        "schemaVersion": "1.0",
        "query": "分数是什么意思？",
        "candidate": {
            "title": "分数",
            "answer": "分数表示整体的一部分。",
            "aliases": ["分数概念"],
        },
        "sources": [],
        "context": {"subject": "math", "actualStudyGrade": 3},
    }


def search_request(text: str, filters: dict | None = None) -> dict:
    return {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": text,
        "filters": filters or {},
    }


def test_search_returns_canonical_answer_and_pending_evidence(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    created = service.learn(learn_request())

    result = service.query(search_request("分数"))

    assert result["status"] == "OK"
    assert result["indexStatus"] == "CURRENT"
    assert result["results"][0]["entityId"] == created["entityId"]
    assert result["results"][0]["answer"] == "分数表示整体的一部分。"
    assert result["results"][0]["knowledgeStatus"] == "EVIDENCE_PENDING"
    assert result["results"][0]["matchType"] == "EXACT"


def test_search_supports_alias_text_and_filters(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    service.learn(learn_request())

    alias = service.query(search_request("分数概念"))
    text = service.query(search_request("分"))
    filtered = service.query(search_request("分数", {"subject": "english"}))

    assert alias["results"][0]["matchType"] == "ALIAS"
    assert text["results"][0]["matchType"] == "TEXT"
    assert filtered["status"] == "LEARN_REQUIRED"


def test_search_miss_is_read_only_and_requires_learn(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    before = len(list(service.events.iter_events()))

    result = service.query(search_request("不存在的知识"))

    assert result["status"] == "LEARN_REQUIRED"
    assert result["results"] == []
    assert len(list(service.events.iter_events())) == before


def test_entity_and_operation_query_modes(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    created = service.learn(learn_request())

    entity_result = service.query(
        {"schemaVersion": "1.0", "mode": "entity", "entityId": created["entityId"]}
    )
    operation_result = service.query(
        {
            "schemaVersion": "1.0",
            "mode": "operation",
            "operationId": created["operationId"],
        }
    )
    request_key_result = service.query(
        {
            "schemaVersion": "1.0",
            "mode": "operation",
            "callerId": "agent-a",
            "targetRequestId": "request-1",
        }
    )

    assert entity_result["entity"]["title"] == "分数"
    assert operation_result["operation"]["operationId"] == created["operationId"]
    assert request_key_result["operation"]["operationId"] == created["operationId"]


def test_query_falls_back_to_ledger_when_projection_rebuild_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = KnowledgeService(tmp_path)
    created = service.learn(learn_request())
    service.jobs.queue(
        service.inspect_state(),
        job_type="MATERIALIZE_ENTITY",
        entity_id=created["entityId"],
        entity_revision=created["entityRevision"],
    )
    monkeypatch.setattr(
        service.projection,
        "rebuild",
        lambda state: (_ for _ in ()).throw(OSError("injected projection failure")),
    )

    result = service.query(search_request("分数"))

    assert result["status"] == "OK"
    assert result["indexStatus"] == "STALE"
    assert result["results"][0]["entityId"].startswith("ent_")


def test_service_rebuild_restores_deleted_sqlite_with_same_query_result(
    tmp_path: Path,
) -> None:
    service = KnowledgeService(tmp_path)
    service.learn(learn_request())
    before = service.query(search_request("分数"))
    service.paths.sqlite.unlink()

    report = service.rebuild()
    after = service.query(search_request("分数"))

    assert report["indexStatus"] == "CURRENT"
    assert after == before
