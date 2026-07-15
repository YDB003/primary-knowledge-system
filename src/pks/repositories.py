from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .contracts import ProtocolError, SUBJECTS, canonical_json, payload_hash
from .paths import VaultPaths


ADAPTER_SOURCE_FILES: dict[str, tuple[str, ...]] = {
    "math-compiled-v1": ("build/vault-compiled/topics.json",),
    "chinese-compiled-v1": (
        "build/vault-compiled/abilityTopics.json",
        "build/vault-compiled/contentItems.json",
        "build/vault-compiled/classicalWorks.json",
    ),
    "english-runtime-v1": ("dist/english-runtime.json",),
    "public-data-v1": ("dist/knowledge.json",),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RepositoryCapture:
    snapshot: dict[str, Any]
    files: Mapping[str, bytes]


def resolve_repository_file(root: str | Path, relative: str | Path) -> Path:
    resolved_root = Path(root).resolve()
    candidate = Path(relative)
    target = (
        candidate.resolve()
        if candidate.is_absolute()
        else (resolved_root / candidate).resolve()
    )
    if target != resolved_root and resolved_root not in target.parents:
        raise ProtocolError(
            "REPOSITORY_PATH_ESCAPE",
            f"path escapes repository root: {relative}",
        )
    return target


class RepositoryRegistry:
    def __init__(self, paths: VaultPaths):
        self.paths = paths
        self.paths.ensure_layout()

    def attach(
        self,
        repository_id: str,
        root: str | Path,
        subject: str,
        adapter: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", repository_id):
            raise ProtocolError("INVALID_REPOSITORY_ID", "repositoryId is invalid")
        if subject not in SUBJECTS:
            raise ProtocolError("INVALID_REQUEST", "repository subject is unsupported")
        if adapter not in ADAPTER_SOURCE_FILES:
            raise ProtocolError("UNKNOWN_ADAPTER", f"adapter is not installed: {adapter}")

        resolved_root = Path(root).resolve()
        if not resolved_root.is_dir():
            raise ProtocolError(
                "REPOSITORY_ROOT_NOT_FOUND",
                f"repository root is not a directory: {resolved_root}",
            )

        for current in self._load()["repositories"]:
            if current["repositoryId"] == repository_id:
                expected = {
                    "root": str(resolved_root),
                    "subject": subject,
                    "adapter": adapter,
                }
                if all(current[key] == value for key, value in expected.items()):
                    return current
                raise ProtocolError(
                    "REPOSITORY_ID_CONFLICT",
                    f"repositoryId already identifies another repository: {repository_id}",
                )
            if Path(current["root"]).resolve() == resolved_root:
                raise ProtocolError(
                    "REPOSITORY_PATH_CONFLICT",
                    f"repository path is already attached as {current['repositoryId']}",
                )

        record = {
            "repositoryId": repository_id,
            "root": str(resolved_root),
            "subject": subject,
            "adapter": adapter,
            "attachedAt": _utc_now(),
        }
        path_claim = {
            "repositoryId": repository_id,
            "root": str(resolved_root),
        }
        claim_path = self._path_claim_path(resolved_root)
        claim_created = self._write_new(claim_path, path_claim)
        if not claim_created:
            current_claim = self._read_object(claim_path)
            if current_claim != path_claim:
                raise ProtocolError(
                    "REPOSITORY_PATH_CONFLICT",
                    f"repository path is already attached as {current_claim.get('repositoryId')}",
                )

        record_path = self.paths.repositories_dir / f"{repository_id}.json"
        record_created = False
        try:
            record_created = self._write_new(record_path, record)
            if record_created:
                return record
            current = self._read_object(record_path)
            expected = {
                "root": str(resolved_root),
                "subject": subject,
                "adapter": adapter,
            }
            if all(current.get(key) == value for key, value in expected.items()):
                return current
            raise ProtocolError(
                "REPOSITORY_ID_CONFLICT",
                f"repositoryId already identifies another repository: {repository_id}",
            )
        except Exception:
            if claim_created and not record_created:
                claim_path.unlink(missing_ok=True)
            raise

    def get(self, repository_id: str) -> dict[str, Any]:
        for record in self._load()["repositories"]:
            if record["repositoryId"] == repository_id:
                return record
        raise ProtocolError(
            "REPOSITORY_NOT_FOUND",
            f"repository is not attached: {repository_id}",
        )

    def list(self) -> list[dict[str, Any]]:
        return self._load()["repositories"]

    def _load(self) -> dict[str, Any]:
        records_by_id: dict[str, dict[str, Any]] = {}
        legacy_path = self.paths.repositories_registry
        if legacy_path.exists():
            try:
                legacy = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProtocolError(
                    "REPOSITORY_REGISTRY_INVALID",
                    f"cannot read repository registry: {exc}",
                ) from exc
            if not isinstance(legacy, dict) or not isinstance(
                legacy.get("repositories"), list
            ):
                raise ProtocolError(
                    "REPOSITORY_REGISTRY_INVALID",
                    "repository registry must contain a repositories array",
                )
            for record in legacy["repositories"]:
                if not isinstance(record, dict) or not isinstance(
                    record.get("repositoryId"), str
                ):
                    raise ProtocolError(
                        "REPOSITORY_REGISTRY_INVALID",
                        "repository registry contains an invalid record",
                    )
                records_by_id[record["repositoryId"]] = record

        for path in sorted(self.paths.repositories_dir.glob("*.json")):
            record = self._read_object(path)
            repository_id = record.get("repositoryId")
            if not isinstance(repository_id, str) or path.stem != repository_id:
                raise ProtocolError(
                    "REPOSITORY_REGISTRY_INVALID",
                    f"repository record does not match filename: {path.name}",
                )
            existing = records_by_id.get(repository_id)
            if existing and any(
                existing.get(key) != record.get(key)
                for key in ("root", "subject", "adapter")
            ):
                raise ProtocolError(
                    "REPOSITORY_REGISTRY_INVALID",
                    f"conflicting repository records: {repository_id}",
                )
            records_by_id[repository_id] = record
        return {
            "schemaVersion": "1.0",
            "repositories": [
                records_by_id[key] for key in sorted(records_by_id)
            ],
        }

    def _path_claim_path(self, root: Path) -> Path:
        normalized = os.path.normcase(str(root.resolve())).encode("utf-8")
        digest = hashlib.sha256(normalized).hexdigest()
        return self.paths.repository_path_claims / f"{digest}.json"

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError(
                "REPOSITORY_REGISTRY_INVALID",
                f"cannot read repository record {path.name}: {exc}",
            ) from exc
        if not isinstance(value, dict):
            raise ProtocolError(
                "REPOSITORY_REGISTRY_INVALID",
                f"repository record must be an object: {path.name}",
            )
        return value

    @staticmethod
    def _write_new(path: Path, value: Mapping[str, Any]) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)


def _run_git(root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_metadata(root: Path) -> tuple[dict[str, str] | None, bool | None]:
    commit = _run_git(root, "rev-parse", "HEAD")
    if not commit:
        return None, None
    branch = _run_git(root, "branch", "--show-current") or ""
    status = _run_git(root, "status", "--porcelain")
    return {"commit": commit, "branch": branch}, bool(status)


def capture_repository(record: Mapping[str, Any]) -> RepositoryCapture:
    root = Path(str(record["root"])).resolve()
    adapter = str(record["adapter"])
    try:
        declared_files = ADAPTER_SOURCE_FILES[adapter]
    except KeyError as exc:
        raise ProtocolError("UNKNOWN_ADAPTER", f"adapter is not installed: {adapter}") from exc

    files: list[dict[str, Any]] = []
    captured_files: dict[str, bytes] = {}
    for relative in declared_files:
        path = resolve_repository_file(root, relative)
        if not path.is_file():
            raise ProtocolError(
                "REPOSITORY_SOURCE_MISSING",
                f"adapter source file does not exist: {relative}",
            )
        normalized_relative = Path(relative).as_posix()
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ProtocolError(
                "REPOSITORY_SOURCE_READ_FAILED",
                f"cannot read adapter source {relative}: {exc}",
            ) from exc
        captured_files[normalized_relative] = content
        files.append(
            {
                "relativePath": normalized_relative,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    git, dirty = _git_metadata(root)
    content_hash = payload_hash(
        {
            "repositoryId": record["repositoryId"],
            "adapter": adapter,
            "files": files,
        }
    )
    snapshot = {
        "repositoryId": record["repositoryId"],
        "root": str(root),
        "subject": record["subject"],
        "adapter": adapter,
        "git": git,
        "dirty": dirty,
        "files": files,
        "contentHash": content_hash,
        "snapshotRevision": git["commit"] if git else content_hash,
        "scannedAt": _utc_now(),
    }
    return RepositoryCapture(
        snapshot=snapshot,
        files=MappingProxyType(captured_files),
    )


def scan_repository(record: Mapping[str, Any]) -> dict[str, Any]:
    return capture_repository(record).snapshot
