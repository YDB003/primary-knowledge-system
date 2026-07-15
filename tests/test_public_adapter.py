from __future__ import annotations

import json
from pathlib import Path

import pytest

from pks.contracts import ProtocolError
from pks.imports import ImportEntity
from pks.public_data import (
    PublicEntity,
    PublicRelation,
    build_public_repository,
    write_public_sources,
)
from pks.subject_adapters import get_adapter


def source_entity(entity_id: str, title: str) -> PublicEntity:
    return PublicEntity.from_import_entity(
        ImportEntity(
            external_id=entity_id,
            title=title,
            aliases=(),
            subject="math",
            entity_type="concept",
            grade_start=3,
            grade_end=4,
            domain="number",
            claim_value=f"{title}的原创说明。",
            source_refs=("source-1",),
            source_path=f"entities/{entity_id}.json",
            import_metadata={},
        )
    )


def build_subject_root(tmp_path: Path) -> Path:
    entities = [
        source_entity("math-a", "知识甲"),
        source_entity("math-b", "知识乙"),
    ]
    relation = PublicRelation(
        relation_id="rel-a-b",
        subject="math",
        from_id="math-a",
        to_id="math-b",
        relation_type="prerequisite",
        reason="甲是乙的前置知识。",
        source_refs=("source-1",),
    )
    write_public_sources(tmp_path, entities, [relation])
    build_public_repository(tmp_path)
    return tmp_path / "subjects" / "math"


def test_public_adapter_maps_entities_and_relationship_metadata(
    tmp_path: Path,
) -> None:
    root = build_subject_root(tmp_path)

    rows = get_adapter("public-data-v1").load(
        root, {"contentHash": "snapshot"}
    )

    assert [row.external_id for row in rows] == ["math-a", "math-b"]
    first = rows[0]
    assert first.knowledge_status == "ACCEPTED"
    assert first.identity_repository_id == "cn-primary-math-taxonomy"
    assert first.source_refs == ("source-1",)
    assert first.import_metadata["licenseClass"] == "CC-BY-4.0"
    assert first.import_metadata["relations"] == [
        {
            "direction": "outgoing",
            "id": "rel-a-b",
            "otherEntityId": "math-b",
            "relationType": "prerequisite",
        }
    ]


def test_public_adapter_accepts_local_approved_tombstone(tmp_path: Path) -> None:
    root = build_subject_root(tmp_path)
    path = root / "dist" / "knowledge.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["entities"] = [
        {
            **bundle["entities"][0],
            "knowledgeStatus": "DELETED",
            "localReview": {
                "commit": "abc123",
                "decision": "ACCEPTED_LOCAL",
            },
        }
    ]
    bundle["relations"] = []
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    row = get_adapter("public-data-v1").load(
        root, {"contentHash": "snapshot"}
    )[0]

    assert row.knowledge_status == "DELETED"
    assert row.import_metadata["localReview"]["commit"] == "abc123"


def test_public_adapter_rejects_unknown_local_status(tmp_path: Path) -> None:
    root = build_subject_root(tmp_path)
    path = root / "dist" / "knowledge.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["entities"][0]["knowledgeStatus"] = "BYPASS_REVIEW"
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ProtocolError, match="knowledgeStatus"):
        get_adapter("public-data-v1").load(root, {"contentHash": "snapshot"})
