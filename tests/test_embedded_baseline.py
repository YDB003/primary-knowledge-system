from __future__ import annotations

import json
from pathlib import Path
import subprocess

from pks.public_data import validate_public_repository


ROOT = Path(__file__).resolve().parents[1]


def test_embedded_public_baseline_is_complete() -> None:
    manifest_path = ROOT / "manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["totals"] == {"entities": 1_541, "relations": 1_192}
    assert {
        subject: (entry["entities"], entry["relations"])
        for subject, entry in manifest["subjects"].items()
    } == {
        "chinese": (735, 471),
        "english": (432, 404),
        "math": (374, 317),
    }

    assert len(list((ROOT / "subjects").glob("*/entities/*.json"))) == 1_541
    assert len(list((ROOT / "subjects").glob("*/relations/*.json"))) == 1_192
    assert validate_public_repository(ROOT, check_dist=True) == []


def test_embedded_runtime_bundles_are_versioned() -> None:
    result = subprocess.run(
        ["git", "ls-files", "subjects/*/dist/knowledge.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = set(result.stdout.splitlines())
    assert tracked == {
        "subjects/chinese/dist/knowledge.json",
        "subjects/english/dist/knowledge.json",
        "subjects/math/dist/knowledge.json",
    }
