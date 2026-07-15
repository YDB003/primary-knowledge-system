from __future__ import annotations

from pathlib import Path

from scripts.release_check import check_release_tree, scan_release_tree


def finding_codes(root: Path) -> set[str]:
    return {finding.code for finding in scan_release_tree(root)}


def test_release_check_rejects_absolute_windows_path(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "Vault lives at " + "F:" + r"\private\vault",
        encoding="utf-8",
    )

    assert "ABSOLUTE_LOCAL_PATH" in finding_codes(tmp_path)


def test_release_check_rejects_private_fields(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text(
        '{"student' + 'Name":"Example Child"}',
        encoding="utf-8",
    )

    assert "PRIVATE_FIELD" in finding_codes(tmp_path)


def test_release_check_rejects_secret_patterns(tmp_path: Path) -> None:
    (tmp_path / "config.txt").write_text(
        "token=" + "gho_" + "abcdefghijklmnopqrstuvwxyz1234567890",
        encoding="utf-8",
    )

    assert "POSSIBLE_SECRET" in finding_codes(tmp_path)


def test_release_check_does_not_treat_knowledge_task_ids_as_secrets(
    tmp_path: Path,
) -> None:
    (tmp_path / "relation.json").write_text(
        '{"id":"writing-task-short-letter-supports-expression"}',
        encoding="utf-8",
    )

    assert scan_release_tree(tmp_path) == []


def test_release_check_ignores_git_and_runtime_directories(tmp_path: Path) -> None:
    for relative in (".git/config", "vault/private.json", ".pytest_cache/x"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"student' + 'Name":"ignored"}', encoding="utf-8")

    assert scan_release_tree(tmp_path) == []


def test_release_tree_requires_public_metadata(tmp_path: Path) -> None:
    findings = check_release_tree(tmp_path)

    assert {finding.code for finding in findings} == {
        "MISSING_LICENSE",
        "MISSING_README",
    }


def test_release_tree_accepts_clean_minimum_project(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Public project\n", encoding="utf-8")

    assert check_release_tree(tmp_path) == []
