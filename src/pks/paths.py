from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _without_windows_extended_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _comparison_key(path: Path) -> str:
    return os.path.normcase(
        os.path.abspath(_without_windows_extended_prefix(str(path)))
    )


@dataclass(frozen=True)
class VaultPaths:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    def resolve(self, relative: str | Path) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (self.root / candidate).resolve()
        root_key = _comparison_key(self.root)
        target_key = _comparison_key(target)
        try:
            inside_root = os.path.commonpath((root_key, target_key)) == root_key
        except ValueError:
            inside_root = False
        if not inside_root:
            raise ValueError("path is outside Vault")
        return Path(_without_windows_extended_prefix(str(target)))

    @property
    def pks(self) -> Path:
        return self.resolve(".pks")

    @property
    def events_committed(self) -> Path:
        return self.resolve(".pks/events/committed")

    @property
    def events_pending(self) -> Path:
        return self.resolve(".pks/events/pending")

    @property
    def operation_claims(self) -> Path:
        return self.resolve(".pks/events/operation-claims")

    @property
    def quarantine(self) -> Path:
        return self.resolve(".pks/events/quarantine")

    @property
    def runtime(self) -> Path:
        return self.resolve(".pks/runtime")

    @property
    def sqlite(self) -> Path:
        return self.resolve(".pks/runtime/pks.sqlite3")

    @property
    def repositories_registry(self) -> Path:
        return self.resolve(".pks/repositories.json")

    @property
    def repositories_dir(self) -> Path:
        return self.resolve(".pks/repositories")

    @property
    def repository_path_claims(self) -> Path:
        return self.resolve(".pks/repository-paths")

    @property
    def public_sync(self) -> Path:
        return self.resolve(".pks/public-sync")

    @property
    def public_sync_upstreams(self) -> Path:
        return self.resolve(".pks/public-sync/upstreams")

    @property
    def public_sync_reviews(self) -> Path:
        return self.resolve(".pks/public-sync/reviews")

    @property
    def public_sync_quarantine(self) -> Path:
        return self.resolve(".pks/public-sync/quarantine")

    @property
    def public_sync_pending(self) -> Path:
        return self.resolve(".pks/public-sync/pending")

    @property
    def public_sync_approved(self) -> Path:
        return self.resolve(".pks/public-sync/approved")

    @property
    def public_sync_states(self) -> Path:
        return self.resolve(".pks/public-sync/states")

    @property
    def tombstones(self) -> Path:
        return self.resolve(".pks/tombstones")

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.events_committed,
            self.events_pending,
            self.operation_claims,
            self.quarantine,
            self.runtime,
            self.repositories_dir,
            self.repository_path_claims,
            self.public_sync_upstreams,
            self.public_sync_reviews,
            self.public_sync_quarantine,
            self.public_sync_pending,
            self.public_sync_approved,
            self.public_sync_states,
            self.tombstones,
        ):
            directory.mkdir(parents=True, exist_ok=True)
