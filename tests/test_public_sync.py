from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import pks.cli as cli_module
from pks.cli import main
from pks.contracts import ProtocolError
from pks.imports import ImportEntity
from pks.public_data import PublicEntity, build_public_repository, write_public_sources
from pks.public_sync import PublicSyncService
from pks.review import ModelVerdict, PublicChange, ReviewContext
from pks.service import KnowledgeService


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def public_entity(summary: str = "分数表示整体平均分后的一份或若干份。") -> PublicEntity:
    return PublicEntity.from_import_entity(
        ImportEntity(
            external_id="math-fraction",
            title="分数的意义",
            aliases=("分数意义",),
            subject="math",
            entity_type="concept",
            grade_start=3,
            grade_end=3,
            domain="number",
            claim_value=summary,
            source_refs=("curriculum-anchor-1",),
            source_path="topics/math-fraction.md",
            import_metadata={},
        )
    )


def initialize_public_repo(root: Path, summary: str | None = None) -> str:
    root.mkdir()
    write_public_sources(root, [public_entity(summary or "分数表示整体平均分后的一份或若干份。")], [])
    build_public_repository(root)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "PKS Tests")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial public data")
    return git(root, "rev-parse", "HEAD")


def update_public_repo(root: Path, summary: str) -> str:
    write_public_sources(root, [public_entity(summary)], [])
    build_public_repository(root)
    git(root, "add", ".")
    git(root, "commit", "-m", "update public data")
    return git(root, "rev-parse", "HEAD")


class AcceptingModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def review(
        self, change: PublicChange, context: ReviewContext
    ) -> ModelVerdict:
        del context
        self.calls.append(change.fingerprint)
        record = change.previous_record if change.action == "DELETE" else change.record
        assert record is not None
        return ModelVerdict(
            decision="ACCEPT",
            reasons=("通过本地模型复核。",),
            subject=record["subject"],
            grade_start=record.get("gradeStart"),
            grade_end=record.get("gradeEnd"),
            model="accepting-test-model",
        )


class RejectingModel(AcceptingModel):
    def review(
        self, change: PublicChange, context: ReviewContext
    ) -> ModelVerdict:
        accepted = super().review(change, context)
        return ModelVerdict(
            decision="REJECT",
            reasons=("测试拒绝。",),
            subject=accepted.subject,
            grade_start=accepted.grade_start,
            grade_end=accepted.grade_end,
            model=accepted.model,
        )


def query_fraction(vault: Path) -> dict[str, Any]:
    return KnowledgeService(vault).query(
        {
            "schemaVersion": "1.0",
            "mode": "search",
            "query": "分数的意义",
            "filters": {"subject": "math"},
        }
    )


def test_first_sync_reviews_imports_and_records_commit(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    commit = initialize_public_repo(repository)
    vault = tmp_path / "vault"
    model = AcceptingModel()

    result = PublicSyncService(vault, model).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    assert result["commit"] == commit
    assert result["reviewed"] == 1
    assert result["accepted"] == 1
    assert result["quarantined"] == 0
    assert result["pending"] == 0
    assert result["importedEntities"] == 1
    assert query_fraction(vault)["results"][0]["answer"].startswith("分数表示")
    review_files = list((vault / ".pks/public-sync/reviews").rglob("*.json"))
    assert len(review_files) == 1
    review = json.loads(review_files[0].read_text(encoding="utf-8"))
    assert review["status"] == "ACCEPTED_LOCAL"


def test_repeated_sync_of_same_commit_is_idempotent(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    vault = tmp_path / "vault"
    model = AcceptingModel()
    service = PublicSyncService(vault, model)

    first = service.sync("cn-primary-knowledge-base", str(repository))
    event_count = len(list((vault / ".pks/events/committed").glob("*.json")))
    second = service.sync("cn-primary-knowledge-base", str(repository))

    assert first["accepted"] == 1
    assert second["commitUnchanged"] is True
    assert second["reviewed"] == 0
    assert len(model.calls) == 1
    assert len(list((vault / ".pks/events/committed").glob("*.json"))) == event_count


def test_pending_change_retries_when_model_becomes_available(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    vault = tmp_path / "vault"

    pending = PublicSyncService(vault, None).sync(
        "cn-primary-knowledge-base", str(repository)
    )
    assert pending["pending"] == 1
    assert query_fraction(vault)["results"] == []

    accepted = PublicSyncService(vault, AcceptingModel()).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    assert accepted["accepted"] == 1
    assert accepted["pending"] == 0
    assert len(query_fraction(vault)["results"]) == 1
    assert len(list((vault / ".pks/public-sync/reviews").rglob("*.json"))) == 2


def test_rejected_change_is_quarantined_and_not_imported(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    vault = tmp_path / "vault"

    result = PublicSyncService(vault, RejectingModel()).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    assert result["quarantined"] == 1
    assert result["importedEntities"] == 0
    assert query_fraction(vault)["results"] == []
    assert len(list((vault / ".pks/public-sync/quarantine").rglob("*.json"))) == 1


def test_changed_public_entity_updates_same_local_entity(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    vault = tmp_path / "vault"
    service = PublicSyncService(vault, AcceptingModel())
    service.sync("cn-primary-knowledge-base", str(repository))
    before = query_fraction(vault)["results"][0]
    commit = update_public_repo(repository, "更新后的分数意义说明。")

    result = service.sync("cn-primary-knowledge-base", str(repository))
    after = query_fraction(vault)["results"][0]

    assert result["commit"] == commit
    assert result["accepted"] == 1
    assert result["updatedEntities"] == 1
    assert after["entityId"] == before["entityId"]
    assert after["answer"] == "更新后的分数意义说明。"


def test_sync_rejects_non_main_branch(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)

    with pytest.raises(ProtocolError, match="PUBLIC_BRANCH_NOT_ALLOWED"):
        PublicSyncService(tmp_path / "vault", AcceptingModel()).sync(
            "cn-primary-knowledge-base", str(repository), branch="feature"
        )


def test_sync_state_migrates_legacy_public_repository_url(tmp_path: Path) -> None:
    service = PublicSyncService(tmp_path / "vault", AcceptingModel())
    repository_id = "cn-primary-knowledge-base"
    legacy_url = "https://github.com/YDB003/cn-primary-knowledge-base.git"
    unified_url = "https://github.com/YDB003/primary-knowledge-system.git"
    state = service._load_state(repository_id, legacy_url, "main")
    state["acceptedRecords"] = {"entity:math-fraction": {"id": "math-fraction"}}
    service._save_state(repository_id, state)

    migrated = service._load_state(repository_id, unified_url, "main")

    assert migrated["repositoryUrl"] == unified_url
    assert migrated["repositoryUrlHistory"] == [legacy_url]
    assert migrated["acceptedRecords"] == state["acceptedRecords"]


def test_sync_state_rejects_unapproved_repository_url_change(tmp_path: Path) -> None:
    service = PublicSyncService(tmp_path / "vault", AcceptingModel())
    repository_id = "another-public-source"
    state = service._load_state(repository_id, "https://example.com/one.git", "main")
    service._save_state(repository_id, state)

    with pytest.raises(ProtocolError, match="PUBLIC_REPOSITORY_ID_CONFLICT"):
        service._load_state(repository_id, "https://example.com/two.git", "main")


def test_public_sync_reuses_existing_origin_repository_identity(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-math"
    topic_path = legacy / "build" / "vault-compiled" / "topics.json"
    topic_path.parent.mkdir(parents=True)
    topic_path.write_text(
        json.dumps(
            [
                {
                    "id": "math-fraction",
                    "subject": "math",
                    "domain": "number",
                    "name": "分数的意义",
                    "type": "CONCEPT",
                    "stage": 2,
                    "typicalGradeStart": 3,
                    "typicalGradeEnd": 3,
                    "description": "旧库说明。",
                    "evidence": [],
                    "commonMisconceptions": [],
                    "curriculumRefs": [],
                    "textbookReviewRefs": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    service = KnowledgeService(vault)
    service.attach_repository(
        "cn-primary-math-taxonomy",
        legacy,
        "math",
        "math-compiled-v1",
    )
    service.import_repository("cn-primary-math-taxonomy")

    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    PublicSyncService(vault, AcceptingModel()).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    state = KnowledgeService(vault).inspect_state()
    assert state.entity_count == 1
    result = query_fraction(vault)
    assert len(result["results"]) == 1
    assert {
        ref["repositoryId"] for ref in result["results"][0]["externalRefs"]
    } == {
        "cn-primary-math-taxonomy",
        "cn-primary-knowledge-base-math",
    }


def test_sync_recovers_after_import_before_state_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    vault = tmp_path / "vault"
    model = AcceptingModel()
    interrupted = PublicSyncService(vault, model)

    def fail_state_write(repository_id: str, state: dict[str, Any]) -> None:
        del repository_id, state
        raise OSError("simulated state write failure")

    monkeypatch.setattr(interrupted, "_save_state", fail_state_write)
    with pytest.raises(OSError, match="simulated state write failure"):
        interrupted.sync("cn-primary-knowledge-base", str(repository))
    event_count = len(list((vault / ".pks/events/committed").glob("*.json")))

    recovered = PublicSyncService(vault, model).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    assert recovered["accepted"] == 1
    assert len(model.calls) == 1
    assert len(list((vault / ".pks/events/committed").glob("*.json"))) == event_count
    assert len(query_fraction(vault)["results"]) == 1


def test_public_sync_cli_without_model_keeps_changes_pending(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    for name in (
        "PKS_REVIEW_ENDPOINT",
        "PKS_REVIEW_MODEL",
        "PKS_REVIEW_API_KEY",
        "PKS_REVIEW_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    code = main(
        [
            "public-sync",
            "--vault",
            str(tmp_path / "vault"),
            "--repository-id",
            "cn-primary-knowledge-base",
            "--url",
            str(repository),
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["pending"] == 1
    assert result["accepted"] == 0


def test_public_sync_cli_builds_configured_model_reviewer(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "public-data"
    initialize_public_repo(repository)
    captured: dict[str, Any] = {}

    def reviewer_factory(**kwargs: Any) -> AcceptingModel:
        captured.update(kwargs)
        return AcceptingModel()

    monkeypatch.setenv("PKS_REVIEW_ENDPOINT", "http://127.0.0.1:9999/v1/chat/completions")
    monkeypatch.setenv("PKS_REVIEW_MODEL", "local-review-model")
    monkeypatch.setenv("PKS_REVIEW_API_KEY", "local-key")
    monkeypatch.setenv("PKS_REVIEW_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setattr(cli_module, "OpenAICompatibleReviewer", reviewer_factory)

    code = main(
        [
            "public-sync",
            "--vault",
            str(tmp_path / "vault"),
            "--repository-id",
            "cn-primary-knowledge-base",
            "--url",
            str(repository),
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["accepted"] == 1
    assert captured == {
        "endpoint": "http://127.0.0.1:9999/v1/chat/completions",
        "api_key": "local-key",
        "model": "local-review-model",
        "timeout": 12.5,
    }


def test_public_sync_cli_rejects_partial_model_configuration(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PKS_REVIEW_ENDPOINT", "http://127.0.0.1:9999")
    monkeypatch.delenv("PKS_REVIEW_MODEL", raising=False)

    code = main(
        [
            "public-sync",
            "--vault",
            str(tmp_path / "vault"),
            "--repository-id",
            "cn-primary-knowledge-base",
            "--url",
            str(tmp_path / "repository"),
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert code == 2
    assert error["errorCode"] == "CLI_INPUT_ERROR"
    assert "must be configured together" in error["message"]
