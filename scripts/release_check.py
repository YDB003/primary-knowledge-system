from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "vault",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PRIVATE_FIELDS = (
    "studentName",
    "studentId",
    "childName",
    "schoolName",
    "homeworkImage",
    "handwritingImage",
    "masteryStatus",
    "mistakeRecord",
)
SECRET_PATTERNS = (
    re.compile((r"(?<![A-Za-z0-9])gh" + "[opsu]_[A-Za-z0-9]{30,}")),
    re.compile((r"(?<![A-Za-z0-9])sk" + "-[A-Za-z0-9_-]{20,}")),
    re.compile("-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]+")
UNIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^\s\"'<>]+")


@dataclass(frozen=True, order=True)
class Finding:
    code: str
    path: str
    message: str


def _iter_candidate_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def scan_release_tree(root: str | Path) -> list[Finding]:
    resolved_root = Path(root).resolve()
    findings: list[Finding] = []
    private_key_pattern = re.compile(
        r'["\'](?:' + "|".join(map(re.escape, PRIVATE_FIELDS)) + r')["\']\s*[:=]',
        re.IGNORECASE,
    )
    for path in _iter_candidate_files(resolved_root):
        text = _read_text(path)
        if text is None:
            continue
        relative = path.relative_to(resolved_root).as_posix()
        if WINDOWS_ABSOLUTE_PATH.search(text) or UNIX_HOME_PATH.search(text):
            findings.append(
                Finding(
                    "ABSOLUTE_LOCAL_PATH",
                    relative,
                    "file contains a machine-specific absolute path",
                )
            )
        if private_key_pattern.search(text):
            findings.append(
                Finding(
                    "PRIVATE_FIELD",
                    relative,
                    "file contains a private student-data field",
                )
            )
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            findings.append(
                Finding(
                    "POSSIBLE_SECRET",
                    relative,
                    "file contains a token or private-key pattern",
                )
            )
    return sorted(set(findings))


def check_release_tree(root: str | Path) -> list[Finding]:
    resolved_root = Path(root).resolve()
    findings = scan_release_tree(resolved_root)
    if not any((resolved_root / name).is_file() for name in ("LICENSE", "LICENSE-DATA")):
        findings.append(
            Finding("MISSING_LICENSE", ".", "LICENSE or LICENSE-DATA is required")
        )
    if not (resolved_root / "README.md").is_file():
        findings.append(Finding("MISSING_README", ".", "README.md is required"))
    return sorted(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_check")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    findings = check_release_tree(args.root)
    print(
        json.dumps(
            {
                "status": "PASS" if not findings else "FAIL",
                "findings": [asdict(item) for item in findings],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
