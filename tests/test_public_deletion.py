from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def entity() -> PublicEntity:
    return PublicEntity.from_import_entity(
        ImportEntity(
            external_id="math-fraction",
            title="分数的意义",
            aliases=(),
            subject="math",
            entity_type="concept",
            grade_start=3,
            grade_end=3,
            domain="number",
            claim_value="分数表示整体平均分后的一份或若干份。",
            source_refs=("curriculum-anchor-1",),
            source_path="topics/math-fraction.md",
            import_metadata={},
        )
    )


def initialize_repository(root: Path) -> None:
    root.mkdir()
    write_public_sources(root, [entity()], [])
    build_public_repository(root)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "PKS Tests")
    git(root, "add", ".")
    git(root, "commit", "-m", "add entity")


def delete_entity(repository: Path) -> None:
    path = repository / "subjects/math/entities/math-fraction.json"
    path.unlink()
    build_public_repository(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "delete entity")


def restore_entity(repository: Path) -> None:
    write_public_sources(repository, [entity()], [])
    build_public_repository(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "restore entity")


class DecisionModel:
    def __init__(self, decision: str):
        self.decision = decision

    def review(
        self, change: PublicChange, context: ReviewContext
    ) -> ModelVerdict:
        del context
        record = change.previous_record if change.action == "DELETE" else change.record
        assert record is not None
        return ModelVerdict(
            decision=self.decision,
            reasons=("测试模型结论。",),
            subject=record["subject"],
            grade_start=record.get("gradeStart"),
            grade_end=record.get("gradeEnd"),
            model="deletion-test-model",
        )


def search(vault: Path) -> list[dict]:
    return KnowledgeService(vault).query(
        {
            "schemaVersion": "1.0",
            "mode": "search",
            "query": "分数的意义",
            "filters": {"subject": "math"},
        }
    )["results"]


def test_accepted_public_deletion_hides_entity_and_writes_tombstone(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "public-data"
    initialize_repository(repository)
    vault = tmp_path / "vault"
    service = PublicSyncService(vault, DecisionModel("ACCEPT"))
    service.sync("cn-primary-knowledge-base", str(repository))
    result_before = search(vault)[0]
    active_note = (
        vault / "knowledge" / "math" / f"{result_before['entityId']}.md"
    )
    assert active_note.is_file()
    delete_entity(repository)

    result = service.sync("cn-primary-knowledge-base", str(repository))

    assert result["accepted"] == 1
    assert search(vault) == []
    assert not active_note.exists()
    tombstone = (
        vault
        / ".pks"
        / "tombstones"
        / "math"
        / f"{result_before['entityId']}.json"
    )
    assert tombstone.is_file()
    stored = json.loads(tombstone.read_text(encoding="utf-8"))
    assert stored["entity"]["knowledgeStatus"] == "DELETED"

    KnowledgeService(vault).rebuild()
    assert search(vault) == []


def test_rejected_public_deletion_keeps_existing_entity(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_repository(repository)
    vault = tmp_path / "vault"
    PublicSyncService(vault, DecisionModel("ACCEPT")).sync(
        "cn-primary-knowledge-base", str(repository)
    )
    delete_entity(repository)

    result = PublicSyncService(vault, DecisionModel("REJECT")).sync(
        "cn-primary-knowledge-base", str(repository)
    )

    assert result["quarantined"] == 1
    assert len(search(vault)) == 1


def test_readding_deleted_entity_restores_free_obsidian_notes(tmp_path: Path) -> None:
    repository = tmp_path / "public-data"
    initialize_repository(repository)
    vault = tmp_path / "vault"
    service = PublicSyncService(vault, DecisionModel("ACCEPT"))
    service.sync("cn-primary-knowledge-base", str(repository))
    result = search(vault)[0]
    active_note = vault / "knowledge/math" / f"{result['entityId']}.md"
    active_note.write_text(
        active_note.read_text(encoding="utf-8") + "\n家长自由笔记。\n",
        encoding="utf-8",
    )
    delete_entity(repository)
    service.sync("cn-primary-knowledge-base", str(repository))
    restore_entity(repository)

    service.sync("cn-primary-knowledge-base", str(repository))

    assert len(search(vault)) == 1
    assert "家长自由笔记。" in active_note.read_text(encoding="utf-8")
