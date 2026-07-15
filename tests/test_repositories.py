from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pks.contracts import ProtocolError
from pks.paths import VaultPaths
from pks.repositories import (
    RepositoryRegistry,
    capture_repository,
    resolve_repository_file,
    scan_repository,
)
from pks.subject_adapters import get_adapter


def make_math_source(root: Path) -> Path:
    source_file = root / "build" / "vault-compiled" / "topics.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(json.dumps([{"id": "topic-1"}]), encoding="utf-8")
    return source_file


def test_attach_accepts_arbitrary_non_git_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))

    record = registry.attach(
        "custom-repo", source, "math", "math-compiled-v1"
    )

    assert record["root"] == str(source.resolve())
    assert registry.get("custom-repo") == record


def test_attach_same_repository_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))

    first = registry.attach("repo-a", source, "math", "math-compiled-v1")
    second = registry.attach("repo-a", source, "math", "math-compiled-v1")

    assert second == first
    assert registry.list() == [first]


def test_same_path_cannot_be_registered_under_two_ids(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    registry.attach("repo-a", source, "math", "math-compiled-v1")

    with pytest.raises(ProtocolError, match="REPOSITORY_PATH_CONFLICT"):
        registry.attach("repo-b", source, "math", "math-compiled-v1")


def test_concurrent_distinct_repository_attaches_are_all_preserved(
    tmp_path: Path,
) -> None:
    roots = [tmp_path / f"source-{index}" for index in range(16)]
    for root in roots:
        root.mkdir()
    vault = tmp_path / "vault"
    registry = RepositoryRegistry(VaultPaths(vault))
    barrier = threading.Barrier(len(roots))

    def attach(index: int) -> dict:
        barrier.wait()
        return registry.attach(
            f"repo-{index:02d}",
            roots[index],
            "math",
            "math-compiled-v1",
        )

    with ThreadPoolExecutor(max_workers=len(roots)) as executor:
        records = list(executor.map(attach, range(len(roots))))

    assert len(records) == len(roots)
    assert {record["repositoryId"] for record in RepositoryRegistry(
        VaultPaths(vault)
    ).list()} == {f"repo-{index:02d}" for index in range(len(roots))}


def test_losing_concurrent_id_attach_releases_its_path_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    roots = [tmp_path / "source-a", tmp_path / "source-b"]
    for root in roots:
        root.mkdir()
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    original_load = registry._load
    barrier = threading.Barrier(2)

    def synchronized_load():
        value = original_load()
        barrier.wait()
        return value

    monkeypatch.setattr(registry, "_load", synchronized_load)

    def attach(root: Path):
        try:
            return registry.attach("repo", root, "math", "math-compiled-v1")
        except ProtocolError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attach, roots))

    monkeypatch.setattr(registry, "_load", original_load)
    winner = next(result for result in results if isinstance(result, dict))
    loser_root = next(root for root in roots if str(root.resolve()) != winner["root"])

    recovered = registry.attach(
        "recovered-repo",
        loser_root,
        "math",
        "math-compiled-v1",
    )

    assert recovered["root"] == str(loser_root.resolve())


def test_registry_read_never_observes_partially_published_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    partial_written = threading.Event()
    release_writer = threading.Event()
    original_open = Path.open

    class PausingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, content: str):
            midpoint = len(content) // 2
            self.handle.write(content[:midpoint])
            self.handle.flush()
            partial_written.set()
            assert release_writer.wait(timeout=5)
            return midpoint + self.handle.write(content[midpoint:])

        def flush(self):
            return self.handle.flush()

        def fileno(self):
            return self.handle.fileno()

    def pausing_open(path: Path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        mode = kwargs.get("mode", args[0] if args else "r")
        if path.parent == registry.paths.repositories_dir and "x" in mode:
            return PausingWriter(handle)
        return handle

    monkeypatch.setattr(Path, "open", pausing_open)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            registry.attach,
            "repo",
            source,
            "math",
            "math-compiled-v1",
        )
        assert partial_written.wait(timeout=5)
        try:
            visible_during_publish = registry.list()
        finally:
            release_writer.set()
        record = future.result()

    assert visible_during_publish == []
    assert registry.list() == [record]


def test_resolve_repository_file_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ProtocolError, match="REPOSITORY_PATH_ESCAPE"):
        resolve_repository_file(root, "../outside.json")


def test_scan_non_git_repository_hashes_declared_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_math_source(root)
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    record = registry.attach("math", root, "math", "math-compiled-v1")

    snapshot = scan_repository(record)

    assert snapshot["repositoryId"] == "math"
    assert snapshot["git"] is None
    assert snapshot["dirty"] is None
    assert len(snapshot["files"]) == 1
    assert snapshot["files"][0]["relativePath"] == "build/vault-compiled/topics.json"
    assert len(snapshot["contentHash"]) == 64


def test_capture_parses_the_same_bytes_that_were_hashed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source_file = make_math_source(root)
    source_file.write_text(
        json.dumps(
            [
                {
                    "id": "topic-1",
                    "name": "Topic 1",
                    "type": "CONCEPT",
                    "canonicalStatus": "CORE",
                }
            ]
        ),
        encoding="utf-8",
    )
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    record = registry.attach("math", root, "math", "math-compiled-v1")

    capture = capture_repository(record)
    source_file.write_text(json.dumps([{"id": "topic-2"}]), encoding="utf-8")
    rows = get_adapter("math-compiled-v1").load(
        root,
        capture.snapshot,
        capture.files,
    )

    assert rows[0].external_id == "topic-1"
    assert json.loads(
        capture.files["build/vault-compiled/topics.json"].decode("utf-8")
    )[0]["id"] == "topic-1"
    assert capture.snapshot["contentHash"] != scan_repository(record)["contentHash"]


def test_scan_reports_dirty_git_repository_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source_file = make_math_source(root)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    source_file.write_text(json.dumps([{"id": "topic-2"}]), encoding="utf-8")
    registry = RepositoryRegistry(VaultPaths(tmp_path / "vault"))
    record = registry.attach("math", root, "math", "math-compiled-v1")

    snapshot = scan_repository(record)

    assert snapshot["git"] is not None
    assert len(snapshot["git"]["commit"]) == 40
    assert snapshot["dirty"] is True
