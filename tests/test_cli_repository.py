from __future__ import annotations

import json
from pathlib import Path

from pks.cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_math_repository(root: Path) -> None:
    path = root / "build" / "vault-compiled" / "topics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "id": "math-topic-1",
                    "name": "整数加法",
                    "domain": "数与代数",
                    "type": "PROCEDURE",
                    "typicalGradeStart": 1,
                    "typicalGradeEnd": 2,
                    "description": "把两个整数合并求总数。",
                    "canonicalStatus": "CORE",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_attach_scan_import_cli_round_trip(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "math"
    vault = tmp_path / "vault"
    write_math_repository(source)

    assert (
        main(
            [
                "attach",
                "--vault",
                str(vault),
                "--repository-id",
                "math-repo",
                "--root",
                str(source),
                "--subject",
                "math",
                "--adapter",
                "math-compiled-v1",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "scan",
                "--vault",
                str(vault),
                "--repository-id",
                "math-repo",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "import",
                "--vault",
                str(vault),
                "--repository-id",
                "math-repo",
            ]
        )
        == 0
    )

    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert outputs[0]["repositoryId"] == "math-repo"
    assert outputs[1]["contentHash"]
    assert outputs[2]["created"] == 1
    assert outputs[2]["totalEntities"] == 1
    assert outputs[2]["indexStatus"] == "CURRENT"


def test_import_cli_reports_unknown_repository_as_protocol_error(
    tmp_path: Path,
    capsys,
) -> None:
    code = main(
        [
            "import",
            "--vault",
            str(tmp_path / "vault"),
            "--repository-id",
            "missing",
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert code == 2
    assert error["errorCode"] == "REPOSITORY_NOT_FOUND"


def test_three_subject_import_script_uses_current_repository_roots() -> None:
    script = (
        PROJECT_ROOT / "scripts" / "import-three-subjects.ps1"
    ).read_text(encoding="utf-8")

    assert "cn-primary-math-taxonomy" in script
    assert "cn-primary-chinese-taxonomy" in script
    assert "cn-primary-english-taxonomy" in script
    assert "math-compiled-v1" in script
    assert "chinese-compiled-v1" in script
    assert "english-runtime-v1" in script
    assert "python -m pks import" in script
    assert "[string]$MathRoot" in script
    assert "[string]$ChineseRoot" in script
    assert "[string]$EnglishRoot" in script
