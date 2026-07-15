from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pks.materialize import (
    CONTROLLED_END,
    CONTROLLED_START,
    FREE_MARKER,
    ManualEditConflict,
    Materializer,
    controlled_hash,
)
from pks.paths import VaultPaths


def canonical_entity() -> dict:
    return {
        "entityId": "ent_fraction",
        "title": "分数",
        "normalizedTitle": "分数",
        "subject": "math",
        "entityType": "concept",
        "revision": 1,
        "aliases": ["分数概念"],
        "normalizedAliases": ["分数概念"],
        "claims": [
            {
                "claimId": "claim-1",
                "fieldPath": "knowledgeContent",
                "value": "分数表示整体的一部分。",
                "state": "PROVISIONAL",
            }
        ],
        "sources": [],
        "artifacts": [],
        "evidenceLinks": [],
        "observations": [
            {
                "observationId": "obs-1",
                "observationType": "ACTUAL_STUDY_GRADE",
                "actualStudyGrade": 3,
                "task": "homework-check",
                "textbookRef": None,
            }
        ],
        "knowledgeStatus": "EVIDENCE_PENDING",
    }


def read_frontmatter(text: str) -> dict[str, str]:
    block = text.split("---", 2)[1]
    return {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip().strip('"')
        for line in block.splitlines()
        if ":" in line
    }


def test_materializes_stable_obsidian_note_with_provisional_marker(tmp_path: Path) -> None:
    materializer = Materializer(VaultPaths(tmp_path))

    path = materializer.materialize_entity(canonical_entity())
    text = path.read_text(encoding="utf-8")
    frontmatter = read_frontmatter(text)

    assert path == (tmp_path / "knowledge" / "math" / "ent_fraction.md").resolve()
    assert frontmatter["pksEntityId"] == "ent_fraction"
    assert frontmatter["pksMaterializedRevision"] == "1"
    assert frontmatter["pksControlledHash"] == controlled_hash(text)
    assert CONTROLLED_START in text
    assert "## 暂定知识" in text
    assert "EVIDENCE_PENDING" in text
    assert "分数表示整体的一部分。" in text
    assert FREE_MARKER in text


def test_rematerialization_preserves_free_notes(tmp_path: Path) -> None:
    materializer = Materializer(VaultPaths(tmp_path))
    entity = canonical_entity()
    path = materializer.materialize_entity(entity)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(FREE_MARKER, f"{FREE_MARKER}\n\n这是我的自由笔记。"),
        encoding="utf-8",
    )
    updated = copy.deepcopy(entity)
    updated["revision"] = 2
    updated["aliases"].append("部分与整体")

    materializer.materialize_entity(updated)

    result = path.read_text(encoding="utf-8")
    assert "这是我的自由笔记。" in result
    assert "部分与整体" in result
    assert read_frontmatter(result)["pksMaterializedRevision"] == "2"


def test_manual_controlled_edit_is_not_overwritten(tmp_path: Path) -> None:
    materializer = Materializer(VaultPaths(tmp_path))
    entity = canonical_entity()
    path = materializer.materialize_entity(entity)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "分数表示整体的一部分。", "人工直接改写的答案。"
        ),
        encoding="utf-8",
    )
    updated = copy.deepcopy(entity)
    updated["revision"] = 2

    with pytest.raises(ManualEditConflict, match="MANUAL_EDIT_CONFLICT"):
        materializer.materialize_entity(updated)

    assert "人工直接改写的答案。" in path.read_text(encoding="utf-8")


def test_older_revision_cannot_downgrade_note(tmp_path: Path) -> None:
    materializer = Materializer(VaultPaths(tmp_path))
    newest = canonical_entity()
    newest["revision"] = 3
    path = materializer.materialize_entity(newest)
    older = canonical_entity()
    older["claims"][0]["value"] = "旧内容"

    result = materializer.materialize_entity(older)

    assert result == path
    text = path.read_text(encoding="utf-8")
    assert "旧内容" not in text
    assert read_frontmatter(text)["pksMaterializedRevision"] == "3"


def test_materialize_all_reports_conflicts_without_stopping_other_entities(
    tmp_path: Path,
) -> None:
    materializer = Materializer(VaultPaths(tmp_path))
    first = canonical_entity()
    first_path = materializer.materialize_entity(first)
    first_path.write_text(
        first_path.read_text(encoding="utf-8").replace("分数表示", "人工修改"),
        encoding="utf-8",
    )
    first["revision"] = 2
    second = canonical_entity()
    second.update(entityId="ent_decimal", title="小数", normalizedTitle="小数")

    report = materializer.materialize_all(
        {first["entityId"]: first, second["entityId"]: second}
    )

    assert report.materialized == ["ent_decimal"]
    assert report.conflicts[0]["entityId"] == "ent_fraction"
    assert report.conflicts[0]["errorCode"] == "MANUAL_EDIT_CONFLICT"
    assert (tmp_path / "knowledge" / "math" / "ent_decimal.md").exists()


def test_controlled_hash_requires_markers() -> None:
    with pytest.raises(ValueError, match="controlled markers"):
        controlled_hash(f"{CONTROLLED_START}\nmissing end")


def test_rendered_note_has_one_complete_controlled_region(tmp_path: Path) -> None:
    text = Materializer(VaultPaths(tmp_path)).render(canonical_entity())

    assert text.count(CONTROLLED_START) == 1
    assert text.count(CONTROLLED_END) == 1
    assert text.count(FREE_MARKER) == 1
