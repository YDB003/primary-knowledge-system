from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path

from .contracts import canonical_json
from .paths import VaultPaths


_id_lock = threading.Lock()
_last_nanosecond = 0


class LedgerIntegrityError(RuntimeError):
    """The immutable ledger contains conflicting or malformed data."""


class OperationClaimConflict(RuntimeError):
    def __init__(self, winning_operation_id: str):
        super().__init__(
            f"another operation already advanced this state: {winning_operation_id}"
        )
        self.winning_operation_id = winning_operation_id


def new_event_id() -> str:
    global _last_nanosecond
    with _id_lock:
        current = max(time.time_ns(), _last_nanosecond + 1)
        _last_nanosecond = current
    seconds, nanoseconds = divmod(current, 1_000_000_000)
    stamp = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}{nanoseconds:09d}Z-{uuid.uuid4().hex}"


class EventStore:
    ABANDONED_AFTER_SECONDS = 30.0

    def __init__(self, paths: VaultPaths):
        self.paths = paths
        self.paths.ensure_layout()
        self._remove_abandoned_pending_files()
        self._recover_operation_claims()

    def _remove_abandoned_pending_files(self) -> None:
        for path in self.paths.events_pending.iterdir():
            if path.is_file() and self._is_abandoned(path):
                path.unlink()

    def _recover_operation_claims(self) -> None:
        for claim in self.paths.operation_claims.glob("*.json"):
            try:
                event = json.loads(claim.read_text(encoding="utf-8"))
                if not isinstance(event, dict):
                    raise ValueError("operation claim is not an object")
                self.append(event)
            except (OSError, json.JSONDecodeError, ValueError, LedgerIntegrityError):
                if self._is_abandoned(claim):
                    quarantine = self.paths.quarantine / f"operation-claim-{claim.name}"
                    os.replace(claim, quarantine)

    def _is_abandoned(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age >= self.ABANDONED_AFTER_SECONDS

    def append_operation_once(
        self,
        operation_id: str,
        event: Mapping[str, object],
        *,
        coordination_key: str | None = None,
        wait_seconds: float = 10.0,
    ) -> bool:
        if self._find_operation_event(operation_id) is not None:
            return False

        claim_identity = coordination_key or operation_id
        digest = hashlib.sha256(claim_identity.encode("utf-8")).hexdigest()
        claim = self.paths.operation_claims / f"{digest}.json"
        content = canonical_json(dict(event)) + "\n"
        try:
            with claim.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                winning_operation_id = self._claim_operation_id(claim)
                if winning_operation_id and self._find_operation_event(
                    winning_operation_id
                ) is not None:
                    if winning_operation_id == operation_id:
                        return False
                    raise OperationClaimConflict(winning_operation_id)
                time.sleep(0.01)
            raise LedgerIntegrityError(
                f"operation {operation_id} is claimed but not committed"
            )

        self.append(event)
        return True

    @staticmethod
    def _claim_operation_id(claim: Path) -> str | None:
        try:
            event = json.loads(claim.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        operation = event.get("operation") if isinstance(event, dict) else None
        operation_id = operation.get("operationId") if isinstance(operation, dict) else None
        return operation_id if isinstance(operation_id, str) else None

    def _find_operation_event(self, operation_id: str) -> dict | None:
        for event in self.iter_events():
            operation = event.get("operation")
            if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                return event
        return None

    def append(self, event: Mapping[str, object]) -> Path:
        record = copy.deepcopy(dict(event))
        event_id = record.get("eventId")
        event_type = record.get("eventType")
        if not isinstance(event_id, str) or not event_id.strip():
            raise LedgerIntegrityError("eventId must be a nonempty string")
        if not isinstance(event_type, str) or not event_type.strip():
            raise LedgerIntegrityError("eventType must be a nonempty string")

        target = self.paths.resolve(Path(".pks/events/committed") / f"{event_id}.json")
        content = canonical_json(record) + "\n"
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing != content:
                raise LedgerIntegrityError(
                    f"event {event_id} already exists with different content"
                )
            return target

        pending = self.paths.resolve(
            Path(".pks/events/pending") / f"{event_id}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with pending.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, target)
        finally:
            if pending.exists():
                pending.unlink()
        return target

    def iter_events(self) -> Iterator[dict]:
        for path in sorted(self.paths.events_committed.glob("*.json")):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LedgerIntegrityError(f"cannot read event {path.name}: {exc}") from exc
            if not isinstance(event, dict):
                raise LedgerIntegrityError(f"event {path.name} is not an object")
            if event.get("eventId") != path.stem:
                raise LedgerIntegrityError(
                    f"eventId {event.get('eventId')!r} does not match filename {path.name}"
                )
            yield event
