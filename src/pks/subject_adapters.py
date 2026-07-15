from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import ProtocolError, canonical_json
from .imports import ImportEntity, SubjectAdapter
from .repositories import ADAPTER_SOURCE_FILES, resolve_repository_file


MATH_TYPES = {
    "CONCEPT": "concept",
    "PROCEDURE": "procedure",
    "CALCULATION_STRATEGY": "skill",
    "MATHEMATICAL_METHOD": "skill",
    "QUANTITY_MODEL": "model",
    "REPRESENTATION": "model",
}

CHINESE_CONTENT_TYPES = {
    "CHARACTER": "character",
    "WORD": "word",
    "IDIOM": "idiom",
}

ENGLISH_CONTENT_TYPES = {
    "LETTER": "letter",
    "PHONEME": "phoneme",
    "IPA_SYMBOL": "phoneme",
    "PHONEME_SEQUENCE": "phoneme",
    "WORD": "word",
    "PHRASE": "phrase",
    "GRAMMAR_RULE": "grammar",
    "SENTENCE_PATTERN": "grammar",
}


def _load_json(
    root: Path,
    relative: str,
    captured_files: Mapping[str, bytes] | None = None,
) -> Any:
    try:
        if captured_files is not None:
            content = captured_files[Path(relative).as_posix()].decode("utf-8-sig")
        else:
            path = resolve_repository_file(root, relative)
            content = path.read_text(encoding="utf-8-sig")
        return json.loads(content)
    except KeyError as exc:
        raise ProtocolError(
            "REPOSITORY_CAPTURE_INCOMPLETE",
            f"captured adapter source is missing: {relative}",
        ) from exc
    except OSError as exc:
        raise ProtocolError(
            "REPOSITORY_SOURCE_READ_FAILED",
            f"cannot read adapter source {relative}: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError(
            "REPOSITORY_SOURCE_INVALID_JSON",
            f"adapter source is invalid JSON: {relative}: {exc}",
        ) from exc


def _array(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProtocolError(
            "REPOSITORY_SOURCE_SCHEMA_INVALID",
            f"{label} must be an array of objects",
        )
    return value


def _required_text(record: dict[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(
            "REPOSITORY_SOURCE_SCHEMA_INVALID",
            f"{label}.{key} must be a nonempty string",
        )
    return value.strip()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _bullets(title: str, values: Any) -> str:
    entries = _strings(values)
    if not entries:
        return ""
    return f"## {title}\n\n" + "\n".join(f"- {entry}" for entry in entries)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section.strip())


def _source_refs(record: dict[str, Any], *keys: str) -> tuple[str, ...]:
    result: list[str] = []
    for key in keys:
        for value in _strings(record.get(key)):
            if value not in result:
                result.append(value)
    return tuple(result)


def _summary_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            lines.extend(_summary_lines(item))
        return lines
    if isinstance(value, dict):
        summary = value.get("summary")
        if isinstance(summary, str) and summary.strip():
            return [summary.strip()]
        return [canonical_json(value)] if value else []
    return [str(value)]


def _rich_section(title: str, value: Any) -> str:
    lines = _summary_lines(value)
    if not lines:
        return ""
    if len(lines) == 1:
        body = lines[0]
    else:
        body = "\n".join(f"- {line}" for line in lines)
    return f"## {title}\n\n{body}"


class MathCompiledAdapter:
    name = "math-compiled-v1"
    topics_path = "build/vault-compiled/topics.json"

    def source_files(self) -> tuple[str, ...]:
        return ADAPTER_SOURCE_FILES[self.name]

    def load(
        self,
        root: Path,
        snapshot: dict[str, Any],
        captured_files: Mapping[str, bytes] | None = None,
    ) -> list[ImportEntity]:
        del snapshot
        records = _array(
            _load_json(root, self.topics_path, captured_files), "topics"
        )
        result: list[ImportEntity] = []
        for record in records:
            external_id = _required_text(record, "id", "topics[]")
            title = _required_text(record, "name", external_id)
            description = record.get("description")
            claim = _join_sections(
                description.strip() if isinstance(description, str) else "",
                _bullets("可观察证据", record.get("evidence")),
                _bullets("常见误区", record.get("commonMisconceptions")),
            ) or title
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=(),
                    subject="math",
                    entity_type=MATH_TYPES.get(str(record.get("type")), "concept"),
                    grade_start=_optional_int(record.get("typicalGradeStart")),
                    grade_end=_optional_int(record.get("typicalGradeEnd")),
                    domain=record.get("domain") if isinstance(record.get("domain"), str) else None,
                    claim_value=claim,
                    source_refs=_source_refs(
                        record,
                        "standardRefs",
                        "curriculumRefs",
                        "textbookReviewRefs",
                        "textbookRefs",
                    ),
                    source_path=self.topics_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED"
                        if record.get("canonicalStatus") == "CORE"
                        else "EVIDENCE_PENDING"
                    ),
                )
            )
        return result


class ChineseCompiledAdapter:
    name = "chinese-compiled-v1"
    base = "build/vault-compiled"
    ability_path = f"{base}/abilityTopics.json"
    content_path = f"{base}/contentItems.json"
    classical_path = f"{base}/classicalWorks.json"

    def source_files(self) -> tuple[str, ...]:
        return ADAPTER_SOURCE_FILES[self.name]

    def load(
        self,
        root: Path,
        snapshot: dict[str, Any],
        captured_files: Mapping[str, bytes] | None = None,
    ) -> list[ImportEntity]:
        del snapshot
        return [
            *self._load_abilities(root, captured_files),
            *self._load_content(root, captured_files),
            *self._load_classical(root, captured_files),
        ]

    def _load_abilities(
        self, root: Path, captured_files: Mapping[str, bytes] | None
    ) -> list[ImportEntity]:
        records = _array(
            _load_json(root, self.ability_path, captured_files), "abilityTopics"
        )
        result: list[ImportEntity] = []
        for record in records:
            external_id = _required_text(record, "id", "abilityTopics[]")
            title = _required_text(record, "name", external_id)
            description = record.get("description")
            claim = _join_sections(
                description.strip() if isinstance(description, str) else "",
                _bullets("掌握证据", record.get("masteryEvidence")),
                _bullets("常见错误", record.get("commonErrors")),
            ) or title
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=(),
                    subject="chinese",
                    entity_type=(
                        "concept" if record.get("abilityType") == "KNOWLEDGE" else "skill"
                    ),
                    grade_start=_optional_int(record.get("typicalGradeStart")),
                    grade_end=_optional_int(record.get("typicalGradeEnd")),
                    domain=record.get("domain") if isinstance(record.get("domain"), str) else None,
                    claim_value=claim,
                    source_refs=_source_refs(record, "curriculumRefs", "evidenceReviewIds"),
                    source_path=self.ability_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED"
                        if record.get("verificationState") == "SUPPORTED"
                        else "EVIDENCE_PENDING"
                    ),
                )
            )
        return result

    def _load_content(
        self, root: Path, captured_files: Mapping[str, bytes] | None
    ) -> list[ImportEntity]:
        records = _array(
            _load_json(root, self.content_path, captured_files), "contentItems"
        )
        result: list[ImportEntity] = []
        for record in records:
            external_id = _required_text(record, "id", "contentItems[]")
            title = _required_text(record, "name", external_id)
            content_type = str(record.get("contentType", ""))
            claim = _join_sections(
                _rich_section("内容属性", record.get("attributes")),
                _bullets("证据线", record.get("evidenceLines")),
            ) or title
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=(),
                    subject="chinese",
                    entity_type=CHINESE_CONTENT_TYPES.get(content_type, "concept"),
                    grade_start=_optional_int(record.get("typicalGradeStart")),
                    grade_end=_optional_int(record.get("typicalGradeEnd")),
                    domain=content_type or None,
                    claim_value=claim,
                    source_refs=_source_refs(record, "sourceRefs", "curriculumRefs"),
                    source_path=self.content_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED"
                        if record.get("verificationState") == "SUPPORTED"
                        else "EVIDENCE_PENDING"
                    ),
                )
            )
        return result

    def _load_classical(
        self, root: Path, captured_files: Mapping[str, bytes] | None
    ) -> list[ImportEntity]:
        records = _array(
            _load_json(root, self.classical_path, captured_files), "classicalWorks"
        )
        section_keys = (
            ("读音", "pronunciation"),
            ("词义", "lexicon"),
            ("逐句理解", "lineMeanings"),
            ("整体理解", "overview"),
            ("背景", "background"),
            ("意象", "imagery"),
            ("结构", "structure"),
            ("主题", "themes"),
            ("情感", "emotions"),
            ("手法", "techniques"),
            ("名句", "famousLines"),
            ("背诵", "recitation"),
            ("考点", "assessmentFocus"),
            ("易错点", "misconceptions"),
            ("练习检查", "practiceChecks"),
            ("相关作品", "relatedWorks"),
        )
        result: list[ImportEntity] = []
        for record in records:
            external_id = _required_text(record, "id", "classicalWorks[]")
            title = _required_text(record, "title", external_id)
            original_lines = _strings(record.get("originalTextLines"))
            original = "## 原文\n\n" + "\n".join(original_lines) if original_lines else ""
            author_bits = [
                value
                for value in (
                    record.get("dynasty"),
                    record.get("authorId"),
                    record.get("form"),
                )
                if isinstance(value, str) and value.strip()
            ]
            attribution = f"## 作品信息\n\n{' | '.join(author_bits)}" if author_bits else ""
            sections = [
                _rich_section(label, record.get(key)) for label, key in section_keys
            ]
            claim = _join_sections(attribution, original, *sections) or title
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=_strings(record.get("titleAliases")),
                    subject="chinese",
                    entity_type="poem",
                    grade_start=None,
                    grade_end=None,
                    domain="古诗词",
                    claim_value=claim,
                    source_refs=_source_refs(record, "sourceRefs", "curriculumRefs"),
                    source_path=self.classical_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED"
                        if record.get("verificationState") == "SUPPORTED"
                        else "EVIDENCE_PENDING"
                    ),
                )
            )
        return result


class EnglishRuntimeAdapter:
    name = "english-runtime-v1"
    runtime_path = "dist/english-runtime.json"

    def source_files(self) -> tuple[str, ...]:
        return ADAPTER_SOURCE_FILES[self.name]

    def load(
        self,
        root: Path,
        snapshot: dict[str, Any],
        captured_files: Mapping[str, bytes] | None = None,
    ) -> list[ImportEntity]:
        del snapshot
        runtime = _load_json(root, self.runtime_path, captured_files)
        if not isinstance(runtime, dict):
            raise ProtocolError(
                "REPOSITORY_SOURCE_SCHEMA_INVALID",
                "english runtime must be an object",
            )
        result: list[ImportEntity] = []
        for record in _array(runtime.get("abilityTopics"), "abilityTopics"):
            external_id = _required_text(record, "id", "abilityTopics[]")
            title = _required_text(record, "title", external_id)
            body = record.get("body")
            summary = record.get("summary")
            claim = (
                body.strip()
                if isinstance(body, str) and body.strip()
                else summary.strip()
                if isinstance(summary, str) and summary.strip()
                else title
            )
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=(),
                    subject="english",
                    entity_type="skill",
                    grade_start=_optional_int(record.get("gradeStart")),
                    grade_end=_optional_int(record.get("gradeEnd")),
                    domain=record.get("domain") if isinstance(record.get("domain"), str) else None,
                    claim_value=claim,
                    source_refs=_source_refs(record, "curriculumAnchors"),
                    source_path=self.runtime_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED" if record.get("status") == "active" else "EVIDENCE_PENDING"
                    ),
                )
            )
        for record in _array(runtime.get("contentItems"), "contentItems"):
            external_id = _required_text(record, "id", "contentItems[]")
            title = _required_text(record, "title", external_id)
            content_type = str(record.get("contentType", ""))
            body = record.get("body")
            claim = body.strip() if isinstance(body, str) and body.strip() else title
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=title,
                    aliases=(),
                    subject="english",
                    entity_type=ENGLISH_CONTENT_TYPES.get(content_type, "concept"),
                    grade_start=_optional_int(record.get("gradeStart")),
                    grade_end=_optional_int(record.get("gradeEnd")),
                    domain=content_type or None,
                    claim_value=claim,
                    source_refs=_source_refs(record, "sources"),
                    source_path=self.runtime_path,
                    import_metadata=copy.deepcopy(record),
                    knowledge_status=(
                        "ACCEPTED" if record.get("status") == "active" else "EVIDENCE_PENDING"
                    ),
                )
            )
        return result


class PublicDataAdapter:
    name = "public-data-v1"
    bundle_path = "dist/knowledge.json"

    def source_files(self) -> tuple[str, ...]:
        return ADAPTER_SOURCE_FILES[self.name]

    def load(
        self,
        root: Path,
        snapshot: dict[str, Any],
        captured_files: Mapping[str, bytes] | None = None,
    ) -> list[ImportEntity]:
        del snapshot
        bundle = _load_json(root, self.bundle_path, captured_files)
        if not isinstance(bundle, dict):
            raise ProtocolError(
                "REPOSITORY_SOURCE_SCHEMA_INVALID",
                "public bundle must be an object",
            )
        subject = _required_text(bundle, "subject", "public bundle")
        records = _array(bundle.get("entities"), "public bundle.entities")
        relationships = _array(
            bundle.get("relations", []), "public bundle.relations"
        )
        relations_by_entity: dict[str, list[dict[str, str]]] = {}
        for relation in relationships:
            relation_id = _required_text(relation, "id", "public relation")
            from_id = _required_text(relation, "fromId", relation_id)
            to_id = _required_text(relation, "toId", relation_id)
            relation_type = _required_text(relation, "relationType", relation_id)
            relations_by_entity.setdefault(from_id, []).append(
                {
                    "direction": "outgoing",
                    "id": relation_id,
                    "otherEntityId": to_id,
                    "relationType": relation_type,
                }
            )
            relations_by_entity.setdefault(to_id, []).append(
                {
                    "direction": "incoming",
                    "id": relation_id,
                    "otherEntityId": from_id,
                    "relationType": relation_type,
                }
            )

        result: list[ImportEntity] = []
        for record in records:
            external_id = _required_text(record, "id", "public entity")
            if _required_text(record, "subject", external_id) != subject:
                raise ProtocolError(
                    "REPOSITORY_SOURCE_SCHEMA_INVALID",
                    f"{external_id}.subject does not match bundle subject",
                )
            status = record.get("knowledgeStatus", "ACCEPTED")
            if status not in {"ACCEPTED", "DELETED"}:
                raise ProtocolError(
                    "REPOSITORY_SOURCE_SCHEMA_INVALID",
                    f"{external_id}.knowledgeStatus is invalid",
                )
            local_review = record.get("localReview")
            if local_review is not None and not isinstance(local_review, dict):
                raise ProtocolError(
                    "REPOSITORY_SOURCE_SCHEMA_INVALID",
                    f"{external_id}.localReview must be an object",
                )
            metadata: dict[str, Any] = {
                "contentHash": _required_text(record, "contentHash", external_id),
                "licenseClass": _required_text(record, "licenseClass", external_id),
                "originRepositoryId": _required_text(
                    record, "originRepositoryId", external_id
                ),
                "relations": sorted(
                    relations_by_entity.get(external_id, []),
                    key=lambda item: (item["id"], item["direction"]),
                ),
            }
            if local_review is not None:
                metadata["localReview"] = copy.deepcopy(local_review)
            domain = record.get("domain")
            result.append(
                ImportEntity(
                    external_id=external_id,
                    title=_required_text(record, "title", external_id),
                    aliases=_strings(record.get("aliases")),
                    subject=subject,
                    entity_type=_required_text(record, "entityType", external_id),
                    grade_start=_optional_int(record.get("gradeStart")),
                    grade_end=_optional_int(record.get("gradeEnd")),
                    domain=domain if isinstance(domain, str) and domain else None,
                    claim_value=_required_text(record, "summary", external_id),
                    source_refs=_strings(record.get("sourceRefs")),
                    source_path=f"{self.bundle_path}#{external_id}",
                    import_metadata=metadata,
                    knowledge_status=status,
                    identity_repository_id=metadata["originRepositoryId"],
                )
            )
        return result


_ADAPTERS: dict[str, SubjectAdapter] = {
    adapter.name: adapter
    for adapter in (
        MathCompiledAdapter(),
        ChineseCompiledAdapter(),
        EnglishRuntimeAdapter(),
        PublicDataAdapter(),
    )
}


def get_adapter(name: str) -> SubjectAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise ProtocolError("UNKNOWN_ADAPTER", f"adapter is not installed: {name}") from exc
