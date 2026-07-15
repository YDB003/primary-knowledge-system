from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from .contracts import canonical_json, payload_hash
from .paths import VaultPaths
from .policy import normalize_identity
from .state import KnowledgeState


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events_applied(event_id TEXT PRIMARY KEY);
CREATE TABLE entities(
    entity_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    subject TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    revision INTEGER NOT NULL,
    knowledge_status TEXT NOT NULL,
    entity_json TEXT NOT NULL
);
CREATE INDEX entity_identity_idx ON entities(subject, normalized_title);
CREATE TABLE external_refs(
    repository_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    source_path TEXT,
    snapshot_revision TEXT,
    content_hash TEXT,
    PRIMARY KEY(repository_id, external_id)
);
CREATE INDEX external_refs_entity_idx ON external_refs(entity_id);
CREATE TABLE aliases(
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    UNIQUE(entity_id, normalized_alias)
);
CREATE INDEX aliases_normalized_idx ON aliases(normalized_alias);
CREATE TABLE claims(claim_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, claim_json TEXT NOT NULL);
CREATE TABLE sources(source_id TEXT NOT NULL, entity_id TEXT NOT NULL, source_json TEXT NOT NULL, PRIMARY KEY(source_id, entity_id));
CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, artifact_json TEXT NOT NULL);
CREATE TABLE evidence_links(evidence_link_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, link_json TEXT NOT NULL);
CREATE TABLE observations(observation_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, observation_json TEXT NOT NULL);
CREATE TABLE operations(
    operation_id TEXT PRIMARY KEY,
    caller_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    operation_json TEXT NOT NULL,
    UNIQUE(caller_id, request_id)
);
CREATE TABLE jobs(job_id TEXT PRIMARY KEY, status TEXT NOT NULL, job_json TEXT NOT NULL);
CREATE TABLE conflicts(conflict_id INTEGER PRIMARY KEY AUTOINCREMENT, conflict_json TEXT NOT NULL);
"""


class Projection:
    def __init__(self, paths: VaultPaths):
        self.paths = paths
        self.paths.ensure_layout()

    @staticmethod
    def _ledger_fingerprint(event_ids: Iterable[str]) -> str:
        return payload_hash(sorted(event_ids))

    def is_current(self, event_ids: Iterable[str]) -> bool:
        if not self.paths.sqlite.exists():
            return False
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'ledgerFingerprint'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return bool(row) and row[0] == self._ledger_fingerprint(event_ids)

    def rebuild(self, state: KnowledgeState) -> None:
        temporary = self.paths.runtime / f"pks.{uuid.uuid4().hex}.tmp.sqlite3"
        try:
            connection = sqlite3.connect(temporary)
            try:
                connection.executescript(SCHEMA)
                self._insert_state(connection, state)
                connection.commit()
            finally:
                connection.close()
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, self.paths.sqlite)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _insert_state(self, connection: sqlite3.Connection, state: KnowledgeState) -> None:
        fingerprint = self._ledger_fingerprint(state.event_ids)
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            [("schemaVersion", "1"), ("ledgerFingerprint", fingerprint)],
        )
        connection.executemany(
            "INSERT INTO events_applied(event_id) VALUES(?)",
            [(event_id,) for event_id in sorted(state.event_ids)],
        )
        for entity_id in sorted(state.entities):
            entity = state.entities[entity_id]
            connection.execute(
                "INSERT INTO entities VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    entity["title"],
                    entity["normalizedTitle"],
                    entity["subject"],
                    entity["entityType"],
                    entity["revision"],
                    entity["knowledgeStatus"],
                    canonical_json(entity),
                ),
            )
            connection.executemany(
                "INSERT INTO aliases(entity_id, alias, normalized_alias) VALUES(?, ?, ?)",
                [
                    (entity_id, alias, normalized)
                    for alias, normalized in zip(
                        entity.get("aliases", []),
                        entity.get("normalizedAliases", []),
                        strict=True,
                    )
                ],
            )
            connection.executemany(
                "INSERT INTO external_refs VALUES(?, ?, ?, ?, ?, ?)",
                [
                    (
                        external_ref["repositoryId"],
                        external_ref["externalId"],
                        entity_id,
                        external_ref.get("sourcePath"),
                        external_ref.get("snapshotRevision"),
                        external_ref.get("contentHash"),
                    )
                    for external_ref in entity.get("externalRefs", [])
                ],
            )
            for claim in entity.get("claims", []):
                connection.execute(
                    "INSERT INTO claims VALUES(?, ?, ?)",
                    (claim["claimId"], entity_id, canonical_json(claim)),
                )
            for source in entity.get("sources", []):
                connection.execute(
                    "INSERT INTO sources VALUES(?, ?, ?)",
                    (source["sourceId"], entity_id, canonical_json(source)),
                )
            for artifact in entity.get("artifacts", []):
                connection.execute(
                    "INSERT INTO artifacts VALUES(?, ?, ?)",
                    (artifact["artifactId"], entity_id, canonical_json(artifact)),
                )
            for link in entity.get("evidenceLinks", []):
                connection.execute(
                    "INSERT INTO evidence_links VALUES(?, ?, ?)",
                    (link["evidenceLinkId"], entity_id, canonical_json(link)),
                )
            for observation in entity.get("observations", []):
                connection.execute(
                    "INSERT INTO observations VALUES(?, ?, ?)",
                    (
                        observation["observationId"],
                        entity_id,
                        canonical_json(observation),
                    ),
                )
        for operation_id in sorted(state.operations_by_id):
            operation = state.operations_by_id[operation_id]
            connection.execute(
                "INSERT INTO operations VALUES(?, ?, ?, ?, ?)",
                (
                    operation_id,
                    operation["callerId"],
                    operation["requestId"],
                    operation["status"],
                    canonical_json(operation),
                ),
            )
        for job_id in sorted(state.jobs):
            job = state.jobs[job_id]
            connection.execute(
                "INSERT INTO jobs VALUES(?, ?, ?)",
                (job_id, job["status"], canonical_json(job)),
            )
        connection.executemany(
            "INSERT INTO conflicts(conflict_json) VALUES(?)",
            [(canonical_json(conflict),) for conflict in state.conflicts],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.sqlite)
        connection.row_factory = sqlite3.Row
        return connection

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT entity_json FROM entities WHERE entity_id = ?", (entity_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_operation(
        self,
        *,
        operation_id: str | None = None,
        caller_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            if operation_id:
                row = connection.execute(
                    "SELECT operation_json FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT operation_json FROM operations WHERE caller_id = ? AND request_id = ?",
                    (caller_id, request_id),
                ).fetchone()
        return json.loads(row[0]) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT job_json FROM jobs ORDER BY job_id"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def search(self, text: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clauses: list[str] = ["knowledge_status != 'DELETED'"]
        parameters: list[Any] = []
        if filters.get("subject"):
            clauses.append("subject = ?")
            parameters.append(filters["subject"])
        if filters.get("entityType"):
            clauses.append("entity_type = ?")
            parameters.append(filters["entityType"])
        statement = "SELECT entity_json FROM entities"
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, parameters).fetchall()
        entities = [json.loads(row[0]) for row in rows]
        return self.search_entities(entities, text, filters)

    @staticmethod
    def search_entities(
        entities: Iterable[dict[str, Any]],
        text: str,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        query = normalize_identity(text)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for entity in entities:
            if entity.get("knowledgeStatus") == "DELETED":
                continue
            if filters.get("subject") and entity["subject"] != filters["subject"]:
                continue
            if filters.get("entityType") and entity["entityType"] != filters["entityType"]:
                continue
            title = entity["normalizedTitle"]
            aliases = entity.get("normalizedAliases", [])
            if query == title:
                score, match_type = 0, "EXACT"
            elif query in aliases:
                score, match_type = 1, "ALIAS"
            elif query in title or title in query or any(
                query in alias or alias in query for alias in aliases
            ):
                score, match_type = 2, "TEXT"
            else:
                continue
            ranked.append((score, Projection._search_result(entity, match_type)))
        ranked.sort(key=lambda item: (item[0], item[1]["title"], item[1]["entityId"]))
        return [result for _, result in ranked]

    @staticmethod
    def _search_result(entity: dict[str, Any], match_type: str) -> dict[str, Any]:
        accepted = [
            claim["value"]
            for claim in entity.get("claims", [])
            if claim["state"] == "ACCEPTED"
        ]
        provisional = [
            claim["value"]
            for claim in entity.get("claims", [])
            if claim["state"] != "ACCEPTED"
        ]
        answer_parts = accepted or provisional
        import_metadata = entity.get("importMetadata", {})
        return {
            "entityId": entity["entityId"],
            "revision": entity["revision"],
            "title": entity["title"],
            "subject": entity["subject"],
            "entityType": entity["entityType"],
            "aliases": entity.get("aliases", []),
            "gradeStart": entity.get("gradeStart"),
            "gradeEnd": entity.get("gradeEnd"),
            "domain": entity.get("domain"),
            "externalRefs": entity.get("externalRefs", []),
            "author": import_metadata.get("author")
            or import_metadata.get("authorId"),
            "dynasty": import_metadata.get("dynasty"),
            "answer": "\n\n".join(answer_parts),
            "acceptedClaims": accepted,
            "provisionalClaims": provisional,
            "knowledgeStatus": entity["knowledgeStatus"],
            "matchType": match_type,
        }
