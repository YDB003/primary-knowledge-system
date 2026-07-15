from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import VaultPaths


CONTROLLED_START = "<!-- PKS:CONTROLLED:START -->"
CONTROLLED_END = "<!-- PKS:CONTROLLED:END -->"
FREE_MARKER = "<!-- PKS:FREE -->"
TEMPLATE_VERSION = "1.0"


class ManualEditConflict(RuntimeError):
    def __init__(self, entity_id: str, path: Path):
        self.entity_id = entity_id
        self.path = path
        super().__init__(
            f"MANUAL_EDIT_CONFLICT: controlled content changed in {path}"
        )


@dataclass
class MaterializationReport:
    materialized: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _controlled_region(text: str) -> str:
    start = text.find(CONTROLLED_START)
    end = text.find(CONTROLLED_END)
    if start < 0 or end < 0 or end < start:
        raise ValueError("controlled markers are missing or out of order")
    return text[start : end + len(CONTROLLED_END)]


def controlled_hash(text: str) -> str:
    return hashlib.sha256(_controlled_region(text).encode("utf-8")).hexdigest()


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("frontmatter is missing")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is incomplete")
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        values[key.strip()] = str(value)
    return values


def _yaml_scalar(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


class Materializer:
    def __init__(self, paths: VaultPaths):
        self.paths = paths
        self.paths.ensure_layout()

    def entity_path(self, entity: dict[str, Any]) -> Path:
        subject = entity["subject"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", subject):
            raise ValueError("invalid subject path segment")
        entity_id = entity["entityId"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entity_id):
            raise ValueError("invalid entityId path segment")
        return self.paths.resolve(Path("knowledge") / subject / f"{entity_id}.md")

    def tombstone_path(self, entity: dict[str, Any]) -> Path:
        subject = entity["subject"]
        entity_id = entity["entityId"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", subject):
            raise ValueError("invalid subject path segment")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entity_id):
            raise ValueError("invalid entityId path segment")
        return self.paths.tombstones / subject / f"{entity_id}.json"

    def render(
        self,
        entity: dict[str, Any],
        free_content: str | None = None,
    ) -> str:
        controlled = self._render_controlled(entity)
        digest = hashlib.sha256(controlled.encode("utf-8")).hexdigest()
        frontmatter = "\n".join(
            [
                "---",
                f"pksEntityId: {_yaml_scalar(entity['entityId'])}",
                f"pksSubject: {_yaml_scalar(entity['subject'])}",
                f"pksEntityType: {_yaml_scalar(entity['entityType'])}",
                f"pksMaterializedRevision: {entity['revision']}",
                f"pksControlledHash: {_yaml_scalar(digest)}",
                f"pksTemplateVersion: {_yaml_scalar(TEMPLATE_VERSION)}",
                "---",
            ]
        )
        if free_content is None:
            free_content = f"{FREE_MARKER}\n\n## 自由笔记\n"
        elif not free_content.startswith(FREE_MARKER):
            raise ValueError("free content must begin with the free marker")
        return f"{frontmatter}\n\n{controlled}\n\n{free_content.rstrip()}\n"

    @staticmethod
    def _render_controlled(entity: dict[str, Any]) -> str:
        lines = [
            CONTROLLED_START,
            f"# {entity['title']}",
            "",
            f"- ID: `{entity['entityId']}`",
            f"- 学科: `{entity['subject']}`",
            f"- 类型: `{entity['entityType']}`",
            f"- revision: `{entity['revision']}`",
            f"- 知识状态: `{entity['knowledgeStatus']}`",
        ]
        grade_start = entity.get("gradeStart")
        grade_end = entity.get("gradeEnd")
        if grade_start is not None or grade_end is not None:
            start = grade_start if grade_start is not None else grade_end
            end = grade_end if grade_end is not None else grade_start
            grade_range = str(start) if start == end else f"{start}-{end}"
            lines.append(f"- 年级范围: `{grade_range}`")

        external_refs = entity.get("externalRefs", [])
        if external_refs:
            lines.extend(["", "## 外部身份", ""])
            for external_ref in external_refs:
                lines.append(
                    f"- `{external_ref['repositoryId']}` / `{external_ref['externalId']}`"
                )
        aliases = entity.get("aliases", [])
        if aliases:
            lines.extend(["", "## 别名", "", "、".join(aliases)])

        accepted = [claim for claim in entity.get("claims", []) if claim["state"] == "ACCEPTED"]
        provisional = [claim for claim in entity.get("claims", []) if claim["state"] != "ACCEPTED"]
        if accepted:
            lines.extend(["", "## 已采纳知识", ""])
            for claim in accepted:
                lines.extend([claim["value"], ""])
        if provisional:
            lines.extend(["", "## 暂定知识", "", "> [!warning] EVIDENCE_PENDING", ""])
            for claim in provisional:
                lines.extend([claim["value"], ""])

        sources = entity.get("sources", [])
        if sources:
            lines.extend(["", "## 来源", ""])
            for source in sources:
                title = source.get("title") or "未命名来源"
                publisher = source.get("publisher") or "未知主体"
                lines.append(f"- {title} | {publisher} | 等级 `{source['grade']}`")

        observations = entity.get("observations", [])
        if observations:
            lines.extend(["", "## 学习观察", ""])
            for observation in observations:
                grade = observation.get("actualStudyGrade")
                task = observation.get("task") or "unspecified"
                lines.append(f"- 实际学习年级：{grade}；场景：`{task}`")

        lines.append(CONTROLLED_END)
        return "\n".join(lines)

    def materialize_entity(self, entity: dict[str, Any]) -> Path:
        path = self.entity_path(entity)
        tombstone_path = self.tombstone_path(entity)
        free_content = None
        current: str | None = None
        if path.exists():
            current = path.read_text(encoding="utf-8")
            metadata = _parse_frontmatter(current)
            if metadata.get("pksEntityId") != entity["entityId"]:
                raise ManualEditConflict(entity["entityId"], path)
            try:
                current_hash = controlled_hash(current)
            except ValueError as exc:
                raise ManualEditConflict(entity["entityId"], path) from exc
            if metadata.get("pksControlledHash") != current_hash:
                raise ManualEditConflict(entity["entityId"], path)
            try:
                materialized_revision = int(metadata["pksMaterializedRevision"])
            except (KeyError, ValueError) as exc:
                raise ManualEditConflict(entity["entityId"], path) from exc
            if (
                materialized_revision >= entity["revision"]
                and entity.get("knowledgeStatus") != "DELETED"
            ):
                tombstone_path.unlink(missing_ok=True)
                return path
            marker_index = current.find(FREE_MARKER)
            if marker_index < 0:
                raise ManualEditConflict(entity["entityId"], path)
            free_content = current[marker_index:]
        elif tombstone_path.exists():
            try:
                tombstone = json.loads(
                    tombstone_path.read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ManualEditConflict(entity["entityId"], tombstone_path) from exc
            if not isinstance(tombstone, dict):
                raise ManualEditConflict(entity["entityId"], tombstone_path)
            stored_entity = tombstone.get("entity")
            if (
                entity.get("knowledgeStatus") == "DELETED"
                and isinstance(stored_entity, dict)
                and int(stored_entity.get("revision", 0)) >= entity["revision"]
            ):
                return tombstone_path
            stored_note = tombstone.get("noteContent")
            if isinstance(stored_note, str):
                marker_index = stored_note.find(FREE_MARKER)
                if marker_index >= 0:
                    free_content = stored_note[marker_index:]

        if entity.get("knowledgeStatus") == "DELETED":
            tombstone = {
                "schemaVersion": "1.0",
                "entity": entity,
                "noteContent": current,
            }
            self._atomic_write_json(tombstone_path, tombstone)
            path.unlink(missing_ok=True)
            return tombstone_path

        rendered = self.render(entity, free_content)
        self._atomic_write(path, rendered)
        tombstone_path.unlink(missing_ok=True)
        return path

    def materialize_all(
        self,
        entities: dict[str, dict[str, Any]],
    ) -> MaterializationReport:
        report = MaterializationReport()
        for entity_id in sorted(entities):
            try:
                self.materialize_entity(entities[entity_id])
            except ManualEditConflict as exc:
                report.conflicts.append(
                    {
                        "entityId": entity_id,
                        "errorCode": "MANUAL_EDIT_CONFLICT",
                        "path": str(exc.path),
                    }
                )
            else:
                report.materialized.append(entity_id)
        return report

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _atomic_write_json(path: Path, value: object) -> None:
        content = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        Materializer._atomic_write(path, content)
