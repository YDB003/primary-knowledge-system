from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import ProtocolError, payload_hash
from .paths import VaultPaths
from .public_data import DATA_VERSION, SCHEMA_VERSION, validate_public_repository
from .review import (
    ModelReviewer,
    PublicChange,
    ReviewContext,
    ReviewResult,
    RuleReviewer,
    review_change,
)
from .service import KnowledgeService


REPOSITORY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _short_path_id(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("PUBLIC_SYNC_STATE_INVALID", str(exc)) from exc
    if not isinstance(value, dict):
        raise ProtocolError("PUBLIC_SYNC_STATE_INVALID", f"object required: {path}")
    return value


def _run_git(arguments: list[str], *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProtocolError("PUBLIC_GIT_FAILED", str(exc)) from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise ProtocolError("PUBLIC_GIT_FAILED", message)
    return result.stdout.strip()


def _serialize_change(change: PublicChange) -> dict[str, Any]:
    return {
        "action": change.action,
        "recordKind": change.record_kind,
        "record": change.record,
        "previousRecord": change.previous_record,
        "repositoryId": change.repository_id,
        "commit": change.commit,
    }


def _deserialize_change(value: dict[str, Any]) -> PublicChange:
    return PublicChange(
        action=str(value["action"]),
        record_kind=str(value["recordKind"]),
        record=copy.deepcopy(value.get("record")),
        previous_record=copy.deepcopy(value.get("previousRecord")),
        repository_id=str(value["repositoryId"]),
        commit=str(value["commit"]),
    )


class PublicSyncService:
    def __init__(self, vault: str | Path, model_reviewer: ModelReviewer | None):
        self.paths = VaultPaths(Path(vault))
        self.paths.ensure_layout()
        self.model_reviewer = model_reviewer
        self.rule_reviewer = RuleReviewer()

    def sync(
        self,
        repository_id: str,
        repository_url: str,
        *,
        branch: str = "main",
    ) -> dict[str, Any]:
        if not REPOSITORY_ID_PATTERN.fullmatch(repository_id):
            raise ProtocolError("INVALID_REPOSITORY_ID", "public repositoryId is invalid")
        if branch != "main":
            raise ProtocolError(
                "PUBLIC_BRANCH_NOT_ALLOWED",
                "public synchronization only accepts the reviewed main branch",
            )
        state = self._load_state(repository_id, repository_url, branch)
        commit = self._resolve_commit(repository_url, branch)
        checkout = self._checkout(repository_id, repository_url, branch, commit)
        findings = validate_public_repository(checkout, check_dist=True)
        if findings:
            codes = ", ".join(sorted({item.code for item in findings}))
            raise ProtocolError(
                "PUBLIC_REPOSITORY_INVALID",
                f"public data validation failed: {codes}",
            )
        current_records = self._load_records(checkout)
        prior_records = state.get("upstreamRecords", {})
        changes = self._diff_records(
            repository_id, commit, prior_records, current_records
        )
        pending_values = state.get("pendingChanges", [])
        pending_changes = [
            _deserialize_change(value)
            for value in pending_values
            if isinstance(value, dict)
        ]
        pending_by_fingerprint = {
            change.fingerprint: change for change in pending_changes
        }
        for change in changes:
            pending_by_fingerprint.pop(change.fingerprint, None)
        if state.get("lastSeenCommit") == commit:
            changes = list(pending_by_fingerprint.values())
        else:
            changes.extend(pending_by_fingerprint.values())
        changes = sorted(
            {change.fingerprint: change for change in changes}.values(),
            key=lambda item: (item.record_kind, self._change_id(item), item.action),
        )

        accepted_records = copy.deepcopy(state.get("acceptedRecords", {}))
        accepted_reviews = copy.deepcopy(state.get("acceptedReviews", {}))
        review_attempts = dict(state.get("reviewAttempts", {}))
        next_pending: list[dict[str, Any]] = []
        counts = {"accepted": 0, "quarantined": 0, "pending": 0}
        for change in changes:
            latest = self._latest_review(repository_id, change.fingerprint)
            if (
                latest
                and latest.get("status")
                in {"ACCEPTED_LOCAL", "QUARANTINED_LOCAL"}
                and latest.get("change") == _serialize_change(change)
            ):
                attempt = int(latest["attempt"])
                result = ReviewResult(
                    status=str(latest["status"]),
                    reasons=tuple(str(item) for item in latest.get("reasons", [])),
                    rule_codes=tuple(
                        str(item) for item in latest.get("ruleCodes", [])
                    ),
                    model=(
                        str(latest["model"])
                        if latest.get("model") is not None
                        else None
                    ),
                    fingerprint=change.fingerprint,
                )
                review_record = latest
            else:
                result = review_change(
                    change,
                    self.rule_reviewer,
                    self.model_reviewer,
                    ReviewContext(),
                )
                latest_attempt = int(latest["attempt"]) if latest else 0
                attempt = max(
                    int(review_attempts.get(change.fingerprint, 0)),
                    latest_attempt,
                ) + 1
                review_record = self._persist_review(
                    repository_id, change, result, attempt
                )
            review_attempts[change.fingerprint] = attempt
            if result.status == "ACCEPTED_LOCAL":
                counts["accepted"] += 1
                self._apply_accepted_change(
                    accepted_records,
                    accepted_reviews,
                    change,
                    result,
                    review_record,
                )
            elif result.status == "QUARANTINED_LOCAL":
                counts["quarantined"] += 1
                self._persist_secondary(
                    self.paths.public_sync_quarantine,
                    repository_id,
                    change.fingerprint,
                    attempt,
                    review_record,
                )
            else:
                counts["pending"] += 1
                next_pending.append(_serialize_change(change))
                self._persist_secondary(
                    self.paths.public_sync_pending,
                    repository_id,
                    change.fingerprint,
                    attempt,
                    review_record,
                )

        approved_subjects = self._write_approved_bundles(
            repository_id,
            commit,
            accepted_records,
            accepted_reviews,
        )
        import_result = self._import_approved(repository_id, approved_subjects)
        next_state = {
            "schemaVersion": SCHEMA_VERSION,
            "repositoryId": repository_id,
            "repositoryUrl": repository_url,
            "branch": branch,
            "lastSeenCommit": commit,
            "upstreamRecords": current_records,
            "acceptedRecords": accepted_records,
            "acceptedReviews": accepted_reviews,
            "pendingChanges": next_pending,
            "reviewAttempts": review_attempts,
            "updatedAt": _utc_now(),
        }
        self._save_state(repository_id, next_state)
        return {
            "repositoryId": repository_id,
            "commit": commit,
            "commitUnchanged": state.get("lastSeenCommit") == commit,
            "reviewed": len(changes),
            **counts,
            **import_result,
        }

    @staticmethod
    def _change_id(change: PublicChange) -> str:
        record = change.record or change.previous_record or {}
        return str(record.get("id", ""))

    def _state_path(self, repository_id: str) -> Path:
        return self.paths.public_sync_states / f"{repository_id}.json"

    def _load_state(
        self, repository_id: str, repository_url: str, branch: str
    ) -> dict[str, Any]:
        path = self._state_path(repository_id)
        if not path.exists():
            return {
                "schemaVersion": SCHEMA_VERSION,
                "repositoryId": repository_id,
                "repositoryUrl": repository_url,
                "branch": branch,
                "upstreamRecords": {},
                "acceptedRecords": {},
                "acceptedReviews": {},
                "pendingChanges": [],
                "reviewAttempts": {},
            }
        state = _read_object(path)
        if state.get("repositoryUrl") != repository_url or state.get("branch") != branch:
            raise ProtocolError(
                "PUBLIC_REPOSITORY_ID_CONFLICT",
                "repositoryId is already bound to another public source",
            )
        return state

    def _save_state(self, repository_id: str, state: dict[str, Any]) -> None:
        _atomic_write_json(self._state_path(repository_id), state)

    @staticmethod
    def _resolve_commit(repository_url: str, branch: str) -> str:
        output = _run_git(["ls-remote", repository_url, f"refs/heads/{branch}"])
        first = output.splitlines()[0].split()[0] if output else ""
        if not COMMIT_PATTERN.fullmatch(first):
            raise ProtocolError(
                "PUBLIC_GIT_FAILED", f"cannot resolve reviewed branch: {branch}"
            )
        return first

    def _checkout(
        self,
        repository_id: str,
        repository_url: str,
        branch: str,
        commit: str,
    ) -> Path:
        repository_root = (
            self.paths.public_sync_upstreams / _short_path_id(repository_id)
        )
        target = repository_root / commit[:16]
        if target.is_dir():
            return target
        repository_root.mkdir(parents=True, exist_ok=True)
        temporary = repository_root / f"t-{uuid.uuid4().hex[:12]}"
        try:
            _run_git(
                [
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--branch",
                    branch,
                    repository_url,
                    str(temporary),
                ]
            )
            actual = _run_git(["-C", str(temporary), "rev-parse", "HEAD"])
            if actual != commit:
                raise ProtocolError(
                    "PUBLIC_HEAD_MOVED",
                    "public main changed during synchronization; retry",
                )
            try:
                os.replace(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return target

    @staticmethod
    def _load_records(checkout: Path) -> dict[str, dict[str, Any]]:
        manifest = _read_object(checkout / "manifest.json")
        subjects = manifest.get("subjects")
        if not isinstance(subjects, dict):
            raise ProtocolError("PUBLIC_REPOSITORY_INVALID", "manifest subjects missing")
        records: dict[str, dict[str, Any]] = {}
        for subject in sorted(subjects):
            entry = subjects[subject]
            if not isinstance(entry, dict) or not isinstance(entry.get("bundlePath"), str):
                raise ProtocolError("PUBLIC_REPOSITORY_INVALID", "bundle path missing")
            path = (checkout / entry["bundlePath"]).resolve()
            if checkout.resolve() not in path.parents:
                raise ProtocolError("PUBLIC_REPOSITORY_INVALID", "bundle path escapes root")
            bundle = _read_object(path)
            for kind, plural in (("entity", "entities"), ("relation", "relations")):
                values = bundle.get(plural)
                if not isinstance(values, list):
                    raise ProtocolError("PUBLIC_REPOSITORY_INVALID", f"{plural} missing")
                for value in values:
                    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                        raise ProtocolError("PUBLIC_REPOSITORY_INVALID", f"invalid {kind}")
                    key = f"{kind}:{value['id']}"
                    if key in records:
                        raise ProtocolError("PUBLIC_REPOSITORY_INVALID", f"duplicate {key}")
                    records[key] = copy.deepcopy(value)
        return records

    @staticmethod
    def _diff_records(
        repository_id: str,
        commit: str,
        prior: dict[str, dict[str, Any]],
        current: dict[str, dict[str, Any]],
    ) -> list[PublicChange]:
        changes: list[PublicChange] = []
        for key in sorted(set(prior) | set(current)):
            before = prior.get(key)
            after = current.get(key)
            if before == after:
                continue
            kind = key.split(":", 1)[0]
            action = "ADD" if before is None else "DELETE" if after is None else "UPDATE"
            changes.append(
                PublicChange(
                    action=action,
                    record_kind=kind,
                    record=copy.deepcopy(after),
                    previous_record=copy.deepcopy(before),
                    repository_id=repository_id,
                    commit=commit,
                )
            )
        return changes

    def _persist_review(
        self,
        repository_id: str,
        change: PublicChange,
        result: ReviewResult,
        attempt: int,
    ) -> dict[str, Any]:
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "repositoryId": repository_id,
            "commit": change.commit,
            "fingerprint": change.fingerprint,
            "attempt": attempt,
            "reviewedAt": _utc_now(),
            "status": result.status,
            "reasons": list(result.reasons),
            "ruleCodes": list(result.rule_codes),
            "model": result.model,
            "change": _serialize_change(change),
        }
        path = (
            self.paths.public_sync_reviews
            / _short_path_id(repository_id)
            / change.fingerprint[:32]
            / f"{attempt:06}.json"
        )
        if path.exists():
            raise ProtocolError("PUBLIC_REVIEW_CONFLICT", "review attempt already exists")
        _atomic_write_json(path, record)
        return record

    def _latest_review(
        self, repository_id: str, fingerprint: str
    ) -> dict[str, Any] | None:
        directory = (
            self.paths.public_sync_reviews
            / _short_path_id(repository_id)
            / fingerprint[:32]
        )
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        return _read_object(paths[-1]) if paths else None

    @staticmethod
    def _persist_secondary(
        base: Path,
        repository_id: str,
        fingerprint: str,
        attempt: int,
        record: dict[str, Any],
    ) -> None:
        _atomic_write_json(
            base
            / _short_path_id(repository_id)
            / fingerprint[:32]
            / f"{attempt:06}.json",
            record,
        )

    @staticmethod
    def _apply_accepted_change(
        accepted_records: dict[str, dict[str, Any]],
        accepted_reviews: dict[str, dict[str, Any]],
        change: PublicChange,
        result: ReviewResult,
        review_record: dict[str, Any],
    ) -> None:
        key = f"{change.record_kind}:{PublicSyncService._change_id(change)}"
        if change.action == "DELETE":
            if change.record_kind == "relation":
                accepted_records.pop(key, None)
                accepted_reviews.pop(key, None)
                return
            if key not in accepted_records and change.previous_record is None:
                return
            record = copy.deepcopy(
                accepted_records.get(key) or change.previous_record or {}
            )
            record["knowledgeStatus"] = "DELETED"
            accepted_records[key] = record
        else:
            if change.record is None:
                return
            accepted_records[key] = copy.deepcopy(change.record)
        accepted_reviews[key] = {
            "commit": change.commit,
            "decision": result.status,
            "fingerprint": change.fingerprint,
            "model": result.model,
            "reviewedAt": review_record["reviewedAt"],
        }

    def _write_approved_bundles(
        self,
        repository_id: str,
        commit: str,
        accepted_records: dict[str, dict[str, Any]],
        accepted_reviews: dict[str, dict[str, Any]],
    ) -> list[str]:
        subjects = sorted(
            {
                str(record["subject"])
                for record in accepted_records.values()
                if isinstance(record, dict) and isinstance(record.get("subject"), str)
            }
        )
        written: list[str] = []
        for subject in subjects:
            entities: list[dict[str, Any]] = []
            for key, source in accepted_records.items():
                if not key.startswith("entity:") or source.get("subject") != subject:
                    continue
                record = copy.deepcopy(source)
                record["localReview"] = copy.deepcopy(accepted_reviews.get(key, {}))
                entities.append(record)
            active_ids = {
                item["id"]
                for item in entities
                if item.get("knowledgeStatus", "ACCEPTED") != "DELETED"
            }
            relations: list[dict[str, Any]] = []
            for key, source in accepted_records.items():
                if not key.startswith("relation:") or source.get("subject") != subject:
                    continue
                if source.get("fromId") not in active_ids or source.get("toId") not in active_ids:
                    continue
                relations.append(copy.deepcopy(source))
            entities.sort(key=lambda item: item["id"])
            relations.sort(key=lambda item: item["id"])
            bundle_base = {
                "schemaVersion": SCHEMA_VERSION,
                "dataVersion": DATA_VERSION,
                "subject": subject,
                "sourceCommit": commit,
                "entities": entities,
                "relations": relations,
                "counts": {
                    "entities": len(entities),
                    "relations": len(relations),
                },
            }
            bundle = {**bundle_base, "bundleHash": payload_hash(bundle_base)}
            path = (
                self.paths.public_sync_approved
                / _short_path_id(repository_id)
                / "subjects"
                / subject
                / "dist"
                / "knowledge.json"
            )
            _atomic_write_json(path, bundle)
            written.append(subject)
        return written

    def _import_approved(
        self, repository_id: str, subjects: list[str]
    ) -> dict[str, int]:
        service = KnowledgeService(self.paths.root)
        totals = {
            "importedEntities": 0,
            "createdEntities": 0,
            "updatedEntities": 0,
            "unchangedEntities": 0,
        }
        for subject in subjects:
            root = (
                self.paths.public_sync_approved
                / _short_path_id(repository_id)
                / "subjects"
                / subject
            )
            local_repository_id = f"{repository_id}-{subject}"
            service.attach_repository(
                local_repository_id,
                root,
                subject,
                "public-data-v1",
            )
            response = service.import_repository(local_repository_id)
            changed = int(response["created"]) + int(response["updated"])
            totals["importedEntities"] += changed
            totals["createdEntities"] += int(response["created"])
            totals["updatedEntities"] += int(response["updated"])
            totals["unchangedEntities"] += int(response["unchanged"])
        return totals
