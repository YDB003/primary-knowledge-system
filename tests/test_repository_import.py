from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pks.service import KnowledgeService
from pks.repositories import capture_repository as real_capture_repository


def write_math_topic(
    root: Path,
    description: str = "第一版说明",
    standard_ref: str = "math-standard",
) -> None:
    path = root / "build" / "vault-compiled" / "topics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "id": "cn-math-fraction",
                    "name": "分数的意义",
                    "domain": "数与代数",
                    "type": "CONCEPT",
                    "typicalGradeStart": 3,
                    "typicalGradeEnd": 3,
                    "description": description,
                    "evidence": ["能表示平均分后的部分。"],
                    "commonMisconceptions": [],
                    "canonicalStatus": "CORE",
                    "standardRefs": [standard_ref],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_chinese_same_title_entities(root: Path) -> None:
    base = root / "build" / "vault-compiled"
    base.mkdir(parents=True, exist_ok=True)
    (base / "abilityTopics.json").write_text("[]", encoding="utf-8")
    (base / "contentItems.json").write_text(
        json.dumps(
            [
                {
                    "id": "character-wind",
                    "name": "风",
                    "contentType": "CHARACTER",
                    "attributes": {"strokeCount": 4},
                    "typicalGradeStart": 1,
                    "typicalGradeEnd": 2,
                    "verificationState": "SUPPORTED",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "classicalWorks.json").write_text(
        json.dumps(
            [
                {
                    "id": "poem-wind",
                    "title": "风",
                    "titleAliases": [],
                    "originalTextLines": ["解落三秋叶"],
                    "themes": [{"summary": "表现风的力量。"}],
                    "verificationState": "SUPPORTED",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def attach_math(service: KnowledgeService, root: Path) -> dict:
    return service.attach_repository(
        "math-repo", root, "math", "math-compiled-v1"
    )


def search(service: KnowledgeService, text: str, subject: str) -> dict:
    return service.query(
        {
            "schemaVersion": "1.0",
            "mode": "search",
            "query": text,
            "filters": {"subject": subject},
        }
    )


def test_import_is_one_event_and_same_snapshot_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "math"
    write_math_topic(source)
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)

    first = service.import_repository("math-repo")
    first_events = list(service.events.iter_events())
    second = service.import_repository("math-repo")

    assert first["created"] == 1
    assert first["updated"] == 0
    assert first["unchanged"] == 0
    assert first_events[0]["eventType"] == "REPOSITORY_IMPORT_APPLIED"
    assert len(first_events) == 1
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1
    assert len(list(service.events.iter_events())) == len(first_events)
    assert service.inspect_state().entity_count == 1


def test_concurrent_identical_import_commits_one_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source)
    vault = tmp_path / "vault"
    first_service = KnowledgeService(vault)
    attach_math(first_service, source)
    second_service = KnowledgeService(vault)
    barrier = threading.Barrier(2)

    def synchronized_capture(record):
        capture = real_capture_repository(record)
        barrier.wait()
        return capture

    monkeypatch.setattr("pks.service.capture_repository", synchronized_capture)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda service: service.import_repository("math-repo"),
                (first_service, second_service),
            )
    )

    assert sum(response["eventCommitted"] for response in responses) == 1
    assert sum(
        event["eventType"] == "REPOSITORY_IMPORT_APPLIED"
        for event in first_service.events.iter_events()
    ) == 1
    assert first_service.inspect_state().entity_count == 1


def test_concurrent_different_snapshots_leave_stale_import_retriable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source, "版本 A")
    vault = tmp_path / "vault"
    first_service = KnowledgeService(vault)
    attach_math(first_service, source)
    second_service = KnowledgeService(vault)
    inspect_barrier = threading.Barrier(2)
    first_at_inspect = threading.Event()

    first_inspect = first_service.inspect_state
    second_inspect = second_service.inspect_state
    first_calls = 0
    second_calls = 0

    def synchronized_first_inspect():
        nonlocal first_calls
        state = first_inspect()
        if first_calls == 0:
            first_calls += 1
            first_at_inspect.set()
            inspect_barrier.wait()
        return state

    def synchronized_second_inspect():
        nonlocal second_calls
        state = second_inspect()
        if second_calls == 0:
            second_calls += 1
            inspect_barrier.wait()
        return state

    monkeypatch.setattr(first_service, "inspect_state", synchronized_first_inspect)
    monkeypatch.setattr(second_service, "inspect_state", synchronized_second_inspect)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_service.import_repository, "math-repo")
        assert first_at_inspect.wait(timeout=5)
        write_math_topic(source, "版本 B")
        second_future = executor.submit(second_service.import_repository, "math-repo")
        responses = [first_future.result(), second_future.result()]

    monkeypatch.setattr(first_service, "inspect_state", first_inspect)
    monkeypatch.setattr(second_service, "inspect_state", second_inspect)
    assert sorted(response["operationStatus"] for response in responses) == [
        "APPLIED",
        "CONFLICT",
    ]

    retried = second_service.import_repository("math-repo")
    state = second_service.inspect_state()
    entity = next(iter(state.entities.values()))

    assert retried["operationStatus"] == "APPLIED"
    assert "版本 B" in entity["claims"][0]["value"]
    assert state.repository_snapshots["math-repo"]["contentHash"] == retried[
        "snapshotContentHash"
    ]


def test_import_identity_uses_repository_and_external_id(tmp_path: Path) -> None:
    source = tmp_path / "chinese"
    write_chinese_same_title_entities(source)
    service = KnowledgeService(tmp_path / "vault")
    service.attach_repository(
        "chinese-repo", source, "chinese", "chinese-compiled-v1"
    )

    result = service.import_repository("chinese-repo")
    wind = search(service, "风", "chinese")

    assert result["created"] == 2
    assert len(wind["results"]) == 2
    assert {
        ref["externalId"]
        for row in wind["results"]
        for ref in service.query(
            {
                "schemaVersion": "1.0",
                "mode": "entity",
                "entityId": row["entityId"],
            }
        )["entity"]["externalRefs"]
    } == {"character-wind", "poem-wind"}


def test_changed_snapshot_updates_same_entity_and_replaces_import_claim(
    tmp_path: Path,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source, "第一版说明")
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)
    first = service.import_repository("math-repo")

    write_math_topic(source, "第二版说明")
    second = service.import_repository("math-repo")
    entity = service.query(
        {
            "schemaVersion": "1.0",
            "mode": "entity",
            "entityId": first["entityIds"][0],
        }
    )["entity"]

    assert second["created"] == 0
    assert second["updated"] == 1
    assert service.inspect_state().entity_count == 1
    assert entity["revision"] == 2
    assert "第二版说明" in entity["claims"][0]["value"]
    assert "第一版说明" not in entity["claims"][0]["value"]


def test_projection_failure_keeps_committed_import(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "math"
    write_math_topic(source)
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)
    monkeypatch.setattr(
        service.projection,
        "rebuild",
        lambda state: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    result = service.import_repository("math-repo")

    assert result["indexStatus"] == "STALE"
    assert service.inspect_state().entity_count == 1
    assert any(
        event["eventType"] == "REPOSITORY_IMPORT_APPLIED"
        for event in service.events.iter_events()
    )


def test_repository_can_revert_to_a_previously_imported_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source, "版本 A")
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)
    first = service.import_repository("math-repo")

    write_math_topic(source, "版本 B")
    service.import_repository("math-repo")
    write_math_topic(source, "版本 A")
    reverted = service.import_repository("math-repo")
    entity = service.query(
        {
            "schemaVersion": "1.0",
            "mode": "entity",
            "entityId": first["entityIds"][0],
        }
    )["entity"]

    assert reverted["updated"] == 1
    assert reverted["unchanged"] == 0
    assert reverted["eventCommitted"] is True
    assert entity["revision"] == 3
    assert "版本 A" in entity["claims"][0]["value"]
    assert len(list(service.events.iter_events())) == 3


def test_changed_source_references_replace_repository_source_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source, standard_ref="source-A")
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)
    first = service.import_repository("math-repo")

    write_math_topic(source, standard_ref="source-B")
    service.import_repository("math-repo")
    entity = service.query(
        {
            "schemaVersion": "1.0",
            "mode": "entity",
            "entityId": first["entityIds"][0],
        }
    )["entity"]

    assert entity["sources"][0]["externalSourceRefs"] == ["source-B"]


def test_import_projection_rebuild_preserves_identity_and_query(
    tmp_path: Path,
) -> None:
    source = tmp_path / "math"
    write_math_topic(source)
    service = KnowledgeService(tmp_path / "vault")
    attach_math(service, source)
    imported = service.import_repository("math-repo")
    before = search(service, "分数的意义", "math")
    service.paths.sqlite.unlink()

    rebuilt = service.rebuild()
    after = search(service, "分数的意义", "math")

    assert rebuilt["indexStatus"] == "CURRENT"
    assert after == before
    assert after["results"][0]["entityId"] == imported["entityIds"][0]
    assert service.inspect_state().entities[imported["entityIds"][0]][
        "externalRefs"
    ][0]["externalId"] == "cn-math-fraction"
