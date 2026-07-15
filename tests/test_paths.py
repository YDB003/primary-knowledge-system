from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pks.paths import VaultPaths


def test_vault_paths_create_runtime_layout(tmp_path: Path) -> None:
    paths = VaultPaths(tmp_path)

    paths.ensure_layout()

    assert paths.events_committed.is_dir()
    assert paths.events_pending.is_dir()
    assert paths.runtime.is_dir()
    assert paths.quarantine.is_dir()


def test_vault_resolves_safe_relative_path(tmp_path: Path) -> None:
    paths = VaultPaths(tmp_path)

    assert paths.resolve("math/entity-1.md") == (
        tmp_path / "math" / "entity-1.md"
    ).resolve()


@pytest.mark.parametrize("relative", ["../escape.json", "math/../../escape.md"])
def test_vault_rejects_traversal(tmp_path: Path, relative: str) -> None:
    paths = VaultPaths(tmp_path)

    with pytest.raises(ValueError, match="outside Vault"):
        paths.resolve(relative)


def test_vault_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    paths = VaultPaths(tmp_path / "vault")
    outside = tmp_path / "outside.json"

    with pytest.raises(ValueError, match="outside Vault"):
        paths.resolve(outside)


def test_concurrent_windows_resolution_keeps_equivalent_extended_paths_inside(
    tmp_path: Path,
) -> None:
    paths = VaultPaths(tmp_path / "vault")
    paths.ensure_layout()
    barrier = threading.Barrier(32)

    def resolve_and_create(index: int) -> Path:
        barrier.wait()
        target = paths.resolve(
            Path("knowledge") / "math" / f"entity-{index}.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    with ThreadPoolExecutor(max_workers=32) as executor:
        targets = list(executor.map(resolve_and_create, range(32)))

    assert len(targets) == 32
    assert all(target.name.startswith("entity-") for target in targets)
