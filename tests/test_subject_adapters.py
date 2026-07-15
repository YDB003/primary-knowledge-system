from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pks.subject_adapters import get_adapter


MATH_ROOT = Path(os.environ.get("PKS_MATH_ROOT", "__missing_math_root__"))
CHINESE_ROOT = Path(os.environ.get("PKS_CHINESE_ROOT", "__missing_chinese_root__"))
ENGLISH_ROOT = Path(os.environ.get("PKS_ENGLISH_ROOT", "__missing_english_root__"))


def write_json(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def math_repo(tmp_path: Path) -> Path:
    root = tmp_path / "math"
    write_json(
        root,
        "build/vault-compiled/topics.json",
        [
            {
                "id": "cn-math-fraction",
                "subject": "数学",
                "domain": "数与代数",
                "name": "分数的意义",
                "type": "CONCEPT",
                "stage": "第二学段",
                "typicalGradeStart": 3,
                "typicalGradeEnd": 3,
                "description": "把一个整体平均分成若干份，其中的一份或几份可以用分数表示。",
                "evidence": ["能用分数表示平均分后的部分。"],
                "commonMisconceptions": ["没有平均分也直接写分数。"],
                "canonicalStatus": "CORE",
                "standardRefs": ["math-standard"],
            }
        ],
    )
    return root


@pytest.fixture
def chinese_repo(tmp_path: Path) -> Path:
    root = tmp_path / "chinese"
    base = "build/vault-compiled"
    write_json(
        root,
        f"{base}/abilityTopics.json",
        [
            {
                "id": "ability-read",
                "name": "借助拼音阅读",
                "domain": "识字与写字",
                "abilityType": "STRATEGY",
                "typicalGradeStart": 1,
                "typicalGradeEnd": 2,
                "description": "借助拼音读准字音。",
                "masteryEvidence": ["能读准字音。"],
                "commonErrors": ["忽略声调。"],
                "verificationState": "SUPPORTED",
            }
        ],
    )
    write_json(
        root,
        f"{base}/contentItems.json",
        [
            {
                "id": "character-wind",
                "contentType": "CHARACTER",
                "name": "风",
                "attributes": {"strokeCount": 4},
                "typicalGradeStart": 1,
                "typicalGradeEnd": 2,
                "sourceRefs": ["character-source"],
                "verificationState": "SUPPORTED",
            }
        ],
    )
    write_json(
        root,
        f"{base}/classicalWorks.json",
        [
            {
                "id": "poem-wind",
                "title": "风",
                "titleAliases": [],
                "authorId": "author-li-qiao",
                "dynasty": "唐",
                "form": "古典诗词",
                "originalTextLines": ["解落三秋叶", "能开二月花"],
                "sourceRefs": ["poem-source"],
                "lexicon": [{"summary": "解落：吹落。"}],
                "themes": [{"summary": "表现风的力量。"}],
                "emotions": [],
                "verificationState": "SUPPORTED",
                "primarySuitability": "CURATED_PRIMARY",
            }
        ],
    )
    return root


@pytest.fixture
def english_repo(tmp_path: Path) -> Path:
    root = tmp_path / "english"
    write_json(
        root,
        "dist/english-runtime.json",
        {
            "schemaVersion": "1.0",
            "abilityTopics": [
                {
                    "id": "eng-ability-greet",
                    "title": "使用简单问候语",
                    "type": "ability",
                    "domain": "pragmatics",
                    "summary": "在见面场景中使用简单问候语。",
                    "gradeStart": 3,
                    "gradeEnd": 4,
                    "prerequisites": ["eng-ability-listen"],
                    "status": "active",
                    "body": "## 能力说明\n\n在见面场景中使用简单问候语。",
                }
            ],
            "contentItems": [
                {
                    "id": "eng-word-hello",
                    "title": "hello",
                    "contentType": "WORD",
                    "gradeStart": 3,
                    "gradeEnd": 3,
                    "sources": ["english-source"],
                    "supports": ["eng-ability-greet"],
                    "status": "active",
                    "body": "用于见面问候。",
                }
            ],
        },
    )
    return root


def test_math_adapter_maps_one_topic(math_repo: Path) -> None:
    rows = get_adapter("math-compiled-v1").load(
        math_repo, {"contentHash": "snapshot"}
    )

    assert [
        (row.external_id, row.title, row.subject, row.grade_start)
        for row in rows
    ] == [
        ("cn-math-fraction", "分数的意义", "math", 3)
    ]
    assert rows[0].entity_type == "concept"
    assert "常见误区" in rows[0].claim_value
    assert rows[0].import_metadata["standardRefs"] == ["math-standard"]


def test_chinese_adapter_keeps_same_title_entities(chinese_repo: Path) -> None:
    rows = get_adapter("chinese-compiled-v1").load(
        chinese_repo, {"contentHash": "snapshot"}
    )

    wind = [row for row in rows if row.title == "风"]
    assert {row.external_id for row in wind} == {"character-wind", "poem-wind"}
    poem = next(row for row in wind if row.external_id == "poem-wind")
    assert poem.entity_type == "poem"
    assert "解落三秋叶" in poem.claim_value
    assert "表现风的力量" in poem.claim_value


def test_english_adapter_reads_runtime_collections(english_repo: Path) -> None:
    rows = get_adapter("english-runtime-v1").load(
        english_repo, {"contentHash": "snapshot"}
    )

    assert {row.external_id for row in rows} == {
        "eng-ability-greet",
        "eng-word-hello",
    }
    word = next(row for row in rows if row.external_id == "eng-word-hello")
    ability = next(row for row in rows if row.external_id == "eng-ability-greet")
    assert word.entity_type == "word"
    assert word.grade_start == 3
    assert word.source_refs == ("english-source",)
    assert ability.source_refs == ()
    assert ability.import_metadata["prerequisites"] == ["eng-ability-listen"]


@pytest.mark.skipif(
    not (MATH_ROOT.exists() and CHINESE_ROOT.exists() and ENGLISH_ROOT.exists()),
    reason="real subject repositories are not available",
)
def test_real_subject_snapshot_entity_counts() -> None:
    math = get_adapter("math-compiled-v1").load(
        MATH_ROOT, {"contentHash": "math-snapshot"}
    )
    chinese = get_adapter("chinese-compiled-v1").load(
        CHINESE_ROOT, {"contentHash": "chinese-snapshot"}
    )
    english = get_adapter("english-runtime-v1").load(
        ENGLISH_ROOT, {"contentHash": "english-snapshot"}
    )

    assert len(math) == 374
    assert len(chinese) == 216 + 444 + 75
    assert len(english) == 210 + 222
    assert len(math) + len(chinese) + len(english) == 1_541
