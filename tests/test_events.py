from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from pks.events import EventStore, LedgerIntegrityError, new_event_id
from pks.paths import VaultPaths


def test_event_ids_sort_in_creation_order() -> None:
    first = new_event_id()
    second = new_event_id()

    assert first < second
    assert first.endswith("Z") is False
    assert len(first.split("-", 1)[1]) == 32


def test_append_is_immutable_and_idempotent(tmp_path: Path) -> None:
    store = EventStore(VaultPaths(tmp_path))
    event = {
        "eventId": new_event_id(),
        "eventType": "OPERATION_APPLIED",
        "policyVersion": "1.0",
    }

    first_path = store.append(event)
    second_path = store.append(event)

    assert first_path == second_path
    assert list(store.iter_events()) == [event]
    assert not list(store.paths.events_pending.iterdir())


def test_same_event_id_with_different_content_is_rejected(tmp_path: Path) -> None:
    store = EventStore(VaultPaths(tmp_path))
    event_id = new_event_id()
    store.append({"eventId": event_id, "eventType": "FIRST"})

    with pytest.raises(LedgerIntegrityError, match="different content"):
        store.append({"eventId": event_id, "eventType": "SECOND"})


def test_iter_events_uses_event_id_order(tmp_path: Path) -> None:
    store = EventStore(VaultPaths(tmp_path))
    first = {"eventId": new_event_id(), "eventType": "FIRST"}
    second = {"eventId": new_event_id(), "eventType": "SECOND"}
    store.append(second)
    store.append(first)

    assert [event["eventType"] for event in store.iter_events()] == ["FIRST", "SECOND"]


def test_iter_events_rejects_filename_payload_mismatch(tmp_path: Path) -> None:
    store = EventStore(VaultPaths(tmp_path))
    store.paths.ensure_layout()
    event_id = new_event_id()
    wrong_id = new_event_id()
    path = store.paths.events_committed / f"{event_id}.json"
    path.write_text(json.dumps({"eventId": wrong_id, "eventType": "BAD"}), encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="does not match filename"):
        list(store.iter_events())


def test_startup_removes_abandoned_pending_files(tmp_path: Path) -> None:
    paths = VaultPaths(tmp_path)
    paths.ensure_layout()
    abandoned = paths.events_pending / "abandoned.tmp"
    abandoned.write_text("partial", encoding="utf-8")
    old = time.time() - 60
    os.utime(abandoned, (old, old))

    EventStore(paths)

    assert not abandoned.exists()


def test_startup_keeps_fresh_pending_file_owned_by_another_writer(
    tmp_path: Path,
) -> None:
    paths = VaultPaths(tmp_path)
    paths.ensure_layout()
    active = paths.events_pending / "active.tmp"
    active.write_text("partial", encoding="utf-8")

    EventStore(paths)

    assert active.exists()
def test_startup_keeps_fresh_partial_operation_claim(tmp_path: Path) -> None:
    paths = VaultPaths(tmp_path)
    paths.ensure_layout()
    active = paths.operation_claims / "active.json"
    active.write_text('{"eventId":', encoding="utf-8")

    EventStore(paths)

    assert active.exists()
