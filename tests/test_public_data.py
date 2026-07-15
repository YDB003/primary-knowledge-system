from __future__ import annotations

import json
from pathlib import Path

import pytest

from pks.imports import ImportEntity
from pks.cli import main
from pks.public_data import (
    PublicDataError,
    PublicEntity,
    PublicRelation,
    build_public_repository,
    validate_public_repository,
    write_public_sources,
)


def import_entity(**overrides: object) -> ImportEntity:
    values: dict[str, object] = {
        "external_id": "math-fraction",
        "title": "分数的意义",
        "aliases": ("分数意义",),
        "subject": "math",
        "entity_type": "concept",
        "grade_start": 3,
        "grade_end": 3,
        "domain": "number",
        "claim_value": "分数表示把一个整体平均分后的一份或若干份。",
        "source_refs": ("curriculum-anchor-1",),
        "source_path": "topics/math-fraction.md",
        "import_metadata": {"textbookText": "must never be exported"},
    }
    values.update(overrides)
    return ImportEntity(**values)  # type: ignore[arg-type]


def public_entities() -> list[PublicEntity]:
    return [
        PublicEntity.from_import_entity(import_entity()),
        PublicEntity.from_import_entity(
            import_entity(
                external_id="math-unit-fraction",
                title="单位分数",
                aliases=(),
                claim_value="分子为一的分数叫作单位分数。",
            )
        ),
    ]


def public_relation() -> PublicRelation:
    return PublicRelation(
        relation_id="rel-math-fraction-unit",
        subject="math",
        from_id="math-fraction",
        to_id="math-unit-fraction",
        relation_type="prerequisite",
        reason="先理解分数的意义，再识别单位分数。",
        source_refs=(),
        license_class="CC-BY-4.0",
    )


def finding_codes(root: Path, *, check_dist: bool = True) -> set[str]:
    return {
        finding.code
        for finding in validate_public_repository(root, check_dist=check_dist)
    }


def test_public_entity_contains_only_publishable_fields() -> None:
    record = PublicEntity.from_import_entity(import_entity()).to_dict()

    assert set(record) == {
        "schemaVersion",
        "id",
        "originRepositoryId",
        "subject",
        "title",
        "aliases",
        "entityType",
        "gradeStart",
        "gradeEnd",
        "domain",
        "summary",
        "sourceRefs",
        "licenseClass",
        "contentHash",
    }
    assert "textbookText" not in json.dumps(record, ensure_ascii=False)
    assert record["originRepositoryId"] == "cn-primary-math-taxonomy"


def test_chinese_poem_uses_share_alike_license() -> None:
    record = PublicEntity.from_import_entity(
        import_entity(
            external_id="poem-jing-ye-si",
            subject="chinese",
            entity_type="poem",
            title="静夜思",
        )
    ).to_dict()

    assert record["licenseClass"] == "CC-BY-SA-4.0"


def test_build_is_deterministic_and_validator_accepts_closed_graph(
    tmp_path: Path,
) -> None:
    write_public_sources(tmp_path, public_entities(), [public_relation()])

    first = build_public_repository(tmp_path)
    snapshot = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }
    second = build_public_repository(tmp_path)
    rebuilt = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }

    assert first == second
    assert snapshot == rebuilt
    assert first["totals"] == {"entities": 2, "relations": 1}
    assert validate_public_repository(tmp_path, check_dist=True) == []


def test_validator_rejects_forbidden_source_field(tmp_path: Path) -> None:
    write_public_sources(tmp_path, public_entities(), [public_relation()])
    build_public_repository(tmp_path)
    path = tmp_path / "subjects" / "math" / "entities" / "math-fraction.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["textbookText"] = "forbidden"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    assert "FORBIDDEN_FIELD" in finding_codes(tmp_path)


def test_validator_rejects_missing_relation_endpoint(tmp_path: Path) -> None:
    bad_relation = PublicRelation(
        relation_id="rel-missing",
        subject="math",
        from_id="math-fraction",
        to_id="math-not-present",
        relation_type="prerequisite",
        reason="Invalid fixture",
        source_refs=(),
        license_class="CC-BY-4.0",
    )
    write_public_sources(tmp_path, public_entities(), [bad_relation])

    assert "RELATION_TARGET_MISSING" in finding_codes(
        tmp_path, check_dist=False
    )
    with pytest.raises(PublicDataError, match="RELATION_TARGET_MISSING"):
        build_public_repository(tmp_path)


def test_validator_rejects_invalid_grade_and_absolute_path(tmp_path: Path) -> None:
    invalid = PublicEntity.from_import_entity(
        import_entity(
            external_id="bad-grade",
            grade_start=7,
            claim_value="Stored at " + "C:" + r"\private\source",
        )
    )
    write_public_sources(tmp_path, [invalid], [])

    assert finding_codes(tmp_path, check_dist=False) >= {
        "GRADE_OUT_OF_RANGE",
        "ABSOLUTE_LOCAL_PATH",
    }


def test_public_build_and_validate_cli(tmp_path: Path, capsys) -> None:
    write_public_sources(tmp_path, public_entities(), [public_relation()])

    assert main(["public-build", "--root", str(tmp_path)]) == 0
    build_result = json.loads(capsys.readouterr().out)
    assert build_result["totals"] == {"entities": 2, "relations": 1}

    assert (
        main(
            [
                "public-validate",
                "--root",
                str(tmp_path),
                "--check-dist",
            ]
        )
        == 0
    )
    validate_result = json.loads(capsys.readouterr().out)
    assert validate_result == {"findings": [], "status": "PASS"}


def test_build_removes_stale_bundle_after_last_entity_is_deleted(
    tmp_path: Path,
) -> None:
    write_public_sources(tmp_path, public_entities(), [public_relation()])
    build_public_repository(tmp_path)
    bundle = tmp_path / "subjects/math/dist/knowledge.json"
    assert bundle.is_file()
    for path in (tmp_path / "subjects/math/entities").glob("*.json"):
        path.unlink()
    for path in (tmp_path / "subjects/math/relations").glob("*.json"):
        path.unlink()

    manifest = build_public_repository(tmp_path)

    assert not bundle.exists()
    assert manifest["subjects"] == {}
    assert validate_public_repository(tmp_path, check_dist=True) == []
