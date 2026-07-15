from __future__ import annotations

from pathlib import Path

from pks.jobs import JobQueue
from pks.service import KnowledgeService


def learn_request() -> dict:
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
                "sourceRef": "s1",
                "title": "Guide",
                "publisher": "Publisher",
                "excerpt": "A fraction is part of a whole.",
            }
        ],
        "context": {"subject": "math", "actualStudyGrade": 3},
    }


def test_projection_failure_keeps_committed_knowledge_and_queues_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = KnowledgeService(tmp_path)
    monkeypatch.setattr(
        service.projection,
        "rebuild",
        lambda state: (_ for _ in ()).throw(OSError("injected projection failure")),
    )

    response = service.learn(learn_request())
    state = service.inspect_state()

    assert response["operationStatus"] == "APPLIED"
    assert response["materializationStatus"] == "COMPLETE"
    assert response["indexStatus"] == "STALE"
    assert state.entities[response["entityId"]]["title"] == "Fraction"
    repair_jobs = [job for job in state.jobs.values() if job["jobType"] == "REBUILD_PROJECTION"]
    assert len(repair_jobs) == 1
    assert repair_jobs[0]["status"] == "QUEUED"


def test_repair_completes_projection_job_after_restart(tmp_path: Path, monkeypatch) -> None:
    service = KnowledgeService(tmp_path)
    original_rebuild = service.projection.rebuild
    monkeypatch.setattr(
        service.projection,
        "rebuild",
        lambda state: (_ for _ in ()).throw(OSError("injected projection failure")),
    )
    response = service.learn(learn_request())
    monkeypatch.setattr(service.projection, "rebuild", original_rebuild)

    recovered = KnowledgeService(tmp_path)
    report = recovered.repair()

    assert report["indexStatus"] == "CURRENT"
    assert recovered.query(
        {"schemaVersion": "1.0", "mode": "entity", "entityId": response["entityId"]}
    )["entity"]["title"] == "Fraction"
    jobs = recovered.inspect_state().jobs.values()
    projection_job = next(job for job in jobs if job["jobType"] == "REBUILD_PROJECTION")
    assert projection_job["status"] == "COMPLETED"


def test_materialization_failure_queues_entity_repair(tmp_path: Path, monkeypatch) -> None:
    service = KnowledgeService(tmp_path)
    original_materialize = service.materializer.materialize_entity
    monkeypatch.setattr(
        service.materializer,
        "materialize_entity",
        lambda entity: (_ for _ in ()).throw(OSError("injected materialization failure")),
    )

    response = service.learn(learn_request())

    assert response["materializationStatus"] == "PENDING"
    state = service.inspect_state()
    job = next(job for job in state.jobs.values() if job["jobType"] == "MATERIALIZE_ENTITY")
    assert job["entityId"] == response["entityId"]
    monkeypatch.setattr(service.materializer, "materialize_entity", original_materialize)
    report = service.repair()
    assert report["materializationStatus"] == "COMPLETE"
    assert (tmp_path / "knowledge" / "math" / f"{response['entityId']}.md").exists()


def test_abandoned_running_job_returns_to_queue(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    response = service.learn(learn_request())
    queue = JobQueue(service.events)
    state = service.inspect_state()
    job = queue.queue(
        state,
        job_type="MATERIALIZE_ENTITY",
        entity_id=response["entityId"],
        entity_revision=response["entityRevision"],
    )
    queue.transition(job, "RUNNING")

    recovered = KnowledgeService(tmp_path)
    requeued = recovered.jobs.requeue_running(recovered.inspect_state())

    assert requeued == [job["jobId"]]
    assert recovered.inspect_state().jobs[job["jobId"]]["status"] == "QUEUED"
    assert recovered.inspect_state().jobs[job["jobId"]]["recoveryReason"] == "ABANDONED_RUNNING_JOB"


def test_repair_job_identity_is_deterministic(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path)
    queue = JobQueue(service.events)
    state = service.inspect_state()

    first = queue.queue(state, job_type="REBUILD_PROJECTION")
    second = queue.queue(service.inspect_state(), job_type="REBUILD_PROJECTION")

    assert second["jobId"] == first["jobId"]
    assert len(service.inspect_state().jobs) == 1
