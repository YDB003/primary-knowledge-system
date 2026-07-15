from __future__ import annotations

from pathlib import Path

from pks.materialize import Materializer
from pks.paths import VaultPaths
from pks.projection import Projection
from pks.state import KnowledgeState


def imported_entity(
    entity_id: str,
    title: str,
    repository_id: str,
    external_id: str,
) -> dict:
    return {
        "entityId": entity_id,
        "title": title,
        "normalizedTitle": title,
        "subject": "chinese",
        "entityType": "concept",
        "revision": 1,
        "aliases": [],
        "normalizedAliases": [],
        "claims": [],
        "sources": [],
        "artifacts": [],
        "evidenceLinks": [],
        "observations": [],
        "knowledgeStatus": "ACCEPTED",
        "externalRefs": [
            {
                "repositoryId": repository_id,
                "externalId": external_id,
                "sourcePath": "compiled.json",
                "snapshotRevision": "snapshot-1",
                "contentHash": f"hash-{external_id}",
            }
        ],
        "gradeStart": 1,
        "gradeEnd": 6,
        "domain": "识字与古诗词",
        "importMetadata": {},
    }


def test_projection_allows_same_subject_same_title(tmp_path: Path) -> None:
    state = KnowledgeState()
    state.entities = {
        "ent-a": imported_entity("ent-a", "风", "repo", "character-wind"),
        "ent-b": imported_entity("ent-b", "风", "repo", "poem-wind"),
    }
    state.entities["ent-a"]["domain"] = "CHARACTER"
    state.entities["ent-b"]["entityType"] = "poem"
    state.entities["ent-b"]["gradeStart"] = None
    state.entities["ent-b"]["gradeEnd"] = None
    state.entities["ent-b"]["domain"] = "古诗词"
    state.entities["ent-b"]["importMetadata"] = {
        "authorId": "classical-author-li-qiao",
        "dynasty": "唐",
    }

    projection = Projection(VaultPaths(tmp_path))
    projection.rebuild(state)

    results = projection.search("风", {"subject": "chinese"})
    assert [row["entityId"] for row in results] == ["ent-a", "ent-b"]
    assert results[0]["domain"] == "CHARACTER"
    assert results[0]["gradeStart"] == 1
    assert results[0]["externalRefs"][0]["externalId"] == "character-wind"
    assert results[1]["author"] == "classical-author-li-qiao"
    assert results[1]["dynasty"] == "唐"


def test_state_resolves_external_identity() -> None:
    state = KnowledgeState()
    state.entities["ent-a"] = imported_entity(
        "ent-a", "风", "repo", "poem-wind"
    )

    entity = state.find_entity_by_external_ref("repo", "poem-wind")

    assert entity is not None
    assert entity["entityId"] == "ent-a"


def test_materialized_note_shows_grade_and_external_identity(tmp_path: Path) -> None:
    entity = imported_entity("ent-a", "风", "repo", "poem-wind")

    rendered = Materializer(VaultPaths(tmp_path)).render(entity)

    assert "年级范围: `1-6`" in rendered
    assert "`repo` / `poem-wind`" in rendered
