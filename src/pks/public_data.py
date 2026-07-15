from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import SUBJECTS, canonical_json, payload_hash
from .imports import ImportEntity
from .subject_adapters import get_adapter


SCHEMA_VERSION = "1.0"
DATA_VERSION = "0.1.0"
LICENSE_CLASSES = {"CC-BY-4.0", "CC-BY-SA-4.0", "PUBLIC-DOMAIN"}
ORIGIN_REPOSITORIES = {
    "math": "cn-primary-math-taxonomy",
    "chinese": "cn-primary-chinese-taxonomy",
    "english": "cn-primary-english-taxonomy",
}
ENTITY_KEYS = {
    "schemaVersion",
    "id",
    "originRepositoryId",
    "subject",
    "title",
    "aliases",
    "entityType",
    "gradeStart",
    "gradeEnd",
    "domain",
    "summary",
    "sourceRefs",
    "licenseClass",
    "contentHash",
}
RELATION_KEYS = {
    "schemaVersion",
    "id",
    "subject",
    "fromId",
    "toId",
    "relationType",
    "reason",
    "sourceRefs",
    "licenseClass",
    "contentHash",
}
FORBIDDEN_FIELDS = {
    "answer",
    "homeworkimage",
    "illustration",
    "mistakerecord",
    "scannedpage",
    "sourcetext",
    "studentid",
    "studentname",
    "teacherguide",
    "textbooktext",
}
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}")
TYPE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]+")
UNIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^\s\"'<>]+")


class PublicDataError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class ValidationFinding:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class PublicEntity:
    entity_id: str
    origin_repository_id: str
    subject: str
    title: str
    aliases: tuple[str, ...]
    entity_type: str
    grade_start: int | None
    grade_end: int | None
    domain: str | None
    summary: str
    source_refs: tuple[str, ...]
    license_class: str

    @classmethod
    def from_import_entity(cls, entity: ImportEntity) -> "PublicEntity":
        license_class = (
            "CC-BY-SA-4.0"
            if entity.subject == "chinese" and entity.entity_type == "poem"
            else "CC-BY-4.0"
        )
        return cls(
            entity_id=entity.external_id,
            origin_repository_id=ORIGIN_REPOSITORIES[entity.subject],
            subject=entity.subject,
            title=entity.title,
            aliases=tuple(entity.aliases),
            entity_type=entity.entity_type,
            grade_start=entity.grade_start,
            grade_end=entity.grade_end,
            domain=entity.domain,
            summary=entity.claim_value,
            source_refs=tuple(entity.source_refs),
            license_class=license_class,
        )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schemaVersion": SCHEMA_VERSION,
            "id": self.entity_id,
            "originRepositoryId": self.origin_repository_id,
            "subject": self.subject,
            "title": self.title,
            "aliases": list(self.aliases),
            "entityType": self.entity_type,
            "gradeStart": self.grade_start,
            "gradeEnd": self.grade_end,
            "domain": self.domain,
            "summary": self.summary,
            "sourceRefs": list(self.source_refs),
            "licenseClass": self.license_class,
        }
        value["contentHash"] = payload_hash(value)
        return value


@dataclass(frozen=True)
class PublicRelation:
    relation_id: str
    subject: str
    from_id: str
    to_id: str
    relation_type: str
    reason: str
    source_refs: tuple[str, ...]
    license_class: str = "CC-BY-4.0"

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schemaVersion": SCHEMA_VERSION,
            "id": self.relation_id,
            "subject": self.subject,
            "fromId": self.from_id,
            "toId": self.to_id,
            "relationType": self.relation_type,
            "reason": self.reason,
            "sourceRefs": list(self.source_refs),
            "licenseClass": self.license_class,
        }
        value["contentHash"] = payload_hash(value)
        return value


def _safe_filename(record_id: str) -> str:
    if not ID_PATTERN.fullmatch(record_id):
        raise PublicDataError(f"INVALID_ID: {record_id}")
    return f"{record_id}.json"


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


def _replace_source_directory(directory: Path, records: Iterable[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    for record in records:
        filename = _safe_filename(str(record["id"]))
        expected.add(filename)
        _atomic_write_json(directory / filename, record)
    for path in directory.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def write_public_sources(
    root: str | Path,
    entities: Iterable[PublicEntity],
    relations: Iterable[PublicRelation],
) -> None:
    resolved_root = Path(root).resolve()
    entities_by_subject: dict[str, list[dict[str, Any]]] = {}
    relations_by_subject: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        entities_by_subject.setdefault(entity.subject, []).append(entity.to_dict())
    for relation in relations:
        relations_by_subject.setdefault(relation.subject, []).append(relation.to_dict())
    subjects = set(entities_by_subject) | set(relations_by_subject)
    for subject in subjects:
        if subject not in SUBJECTS:
            raise PublicDataError(f"INVALID_SUBJECT: {subject}")
        subject_root = resolved_root / "subjects" / subject
        _replace_source_directory(
            subject_root / "entities",
            sorted(entities_by_subject.get(subject, []), key=lambda item: item["id"]),
        )
        _replace_source_directory(
            subject_root / "relations",
            sorted(relations_by_subject.get(subject, []), key=lambda item: item["id"]),
        )


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicDataError(f"INVALID_JSON: {path}: {exc}") from exc


def _load_source_records(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    subjects_root = root / "subjects"
    if not subjects_root.is_dir():
        return entities, relations
    for subject_root in sorted(path for path in subjects_root.iterdir() if path.is_dir()):
        for kind, target in (("entities", entities), ("relations", relations)):
            directory = subject_root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                value = _load_json(path)
                if not isinstance(value, dict):
                    raise PublicDataError(f"INVALID_RECORD: {path}")
                record = dict(value)
                record["__path"] = path.relative_to(root).as_posix()
                record["__pathSubject"] = subject_root.name
                target.append(record)
    return entities, relations


def _finding(code: str, record: Mapping[str, Any], message: str) -> ValidationFinding:
    return ValidationFinding(code, str(record.get("__path", ".")), message)


def _contains_absolute_path(value: object) -> bool:
    if isinstance(value, str):
        return bool(WINDOWS_PATH.search(value) or UNIX_HOME_PATH.search(value))
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    return False


def _validate_common(
    record: Mapping[str, Any],
    expected_keys: set[str],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    actual_keys = {key for key in record if not key.startswith("__")}
    extra = actual_keys - expected_keys
    missing = expected_keys - actual_keys
    if extra:
        findings.append(
            _finding("FORBIDDEN_FIELD", record, f"unexpected fields: {sorted(extra)}")
        )
    if missing:
        findings.append(
            _finding("MISSING_FIELD", record, f"missing fields: {sorted(missing)}")
        )
    normalized_keys = {re.sub(r"[^a-z0-9]", "", key.casefold()) for key in actual_keys}
    forbidden = normalized_keys & FORBIDDEN_FIELDS
    if forbidden:
        findings.append(
            _finding("FORBIDDEN_FIELD", record, f"forbidden fields: {sorted(forbidden)}")
        )
    if record.get("schemaVersion") != SCHEMA_VERSION:
        findings.append(_finding("INVALID_SCHEMA_VERSION", record, "unsupported schema"))
    record_id = record.get("id")
    if not isinstance(record_id, str) or not ID_PATTERN.fullmatch(record_id):
        findings.append(_finding("INVALID_ID", record, "id is not filesystem safe"))
    elif Path(str(record.get("__path", ""))).stem != record_id:
        findings.append(_finding("ID_PATH_MISMATCH", record, "id does not match filename"))
    subject = record.get("subject")
    if subject not in SUBJECTS or subject != record.get("__pathSubject"):
        findings.append(_finding("INVALID_SUBJECT", record, "subject does not match path"))
    elif (
        "originRepositoryId" in expected_keys
        and record.get("originRepositoryId") != ORIGIN_REPOSITORIES[subject]
    ):
        findings.append(
            _finding(
                "INVALID_ORIGIN_REPOSITORY",
                record,
                "originRepositoryId does not match subject",
            )
        )
    if record.get("licenseClass") not in LICENSE_CLASSES:
        findings.append(_finding("INVALID_LICENSE", record, "licenseClass is unsupported"))
    if _contains_absolute_path({k: v for k, v in record.items() if not k.startswith("__")}):
        findings.append(_finding("ABSOLUTE_LOCAL_PATH", record, "absolute local path found"))
    claimed_hash = record.get("contentHash")
    hash_input = {
        key: value
        for key, value in record.items()
        if not key.startswith("__") and key != "contentHash"
    }
    if not isinstance(claimed_hash, str) or claimed_hash != payload_hash(hash_input):
        findings.append(_finding("CONTENT_HASH_MISMATCH", record, "content hash is invalid"))
    return findings


def _validate_entity(record: Mapping[str, Any]) -> list[ValidationFinding]:
    findings = _validate_common(record, ENTITY_KEYS)
    if not isinstance(record.get("title"), str) or not record.get("title", "").strip():
        findings.append(_finding("INVALID_TITLE", record, "title is required"))
    if not isinstance(record.get("summary"), str) or not record.get("summary", "").strip():
        findings.append(_finding("INVALID_SUMMARY", record, "summary is required"))
    elif len(str(record["summary"])) > 12_000:
        findings.append(_finding("SUMMARY_TOO_LARGE", record, "summary exceeds 12000 characters"))
    if not isinstance(record.get("entityType"), str) or not TYPE_PATTERN.fullmatch(
        str(record.get("entityType", ""))
    ):
        findings.append(_finding("INVALID_ENTITY_TYPE", record, "entityType is invalid"))
    for key in ("gradeStart", "gradeEnd"):
        grade = record.get(key)
        if grade is not None and (not isinstance(grade, int) or not 1 <= grade <= 6):
            findings.append(_finding("GRADE_OUT_OF_RANGE", record, f"{key} must be 1-6 or null"))
    grade_start = record.get("gradeStart")
    grade_end = record.get("gradeEnd")
    if isinstance(grade_start, int) and isinstance(grade_end, int) and grade_start > grade_end:
        findings.append(_finding("INVALID_GRADE_RANGE", record, "gradeStart exceeds gradeEnd"))
    if not isinstance(record.get("aliases"), list) or not all(
        isinstance(item, str) for item in record.get("aliases", [])
    ):
        findings.append(_finding("INVALID_ALIASES", record, "aliases must be strings"))
    if not isinstance(record.get("sourceRefs"), list) or not all(
        isinstance(item, str) for item in record.get("sourceRefs", [])
    ):
        findings.append(_finding("INVALID_SOURCE_REFS", record, "sourceRefs must be strings"))
    return findings


def _validate_relation(record: Mapping[str, Any]) -> list[ValidationFinding]:
    findings = _validate_common(record, RELATION_KEYS)
    for key in ("fromId", "toId"):
        if not isinstance(record.get(key), str) or not ID_PATTERN.fullmatch(
            str(record.get(key, ""))
        ):
            findings.append(_finding("INVALID_RELATION_ENDPOINT", record, f"{key} is invalid"))
    if not isinstance(record.get("relationType"), str) or not TYPE_PATTERN.fullmatch(
        str(record.get("relationType", ""))
    ):
        findings.append(_finding("INVALID_RELATION_TYPE", record, "relationType is invalid"))
    if not isinstance(record.get("reason"), str):
        findings.append(_finding("INVALID_RELATION_REASON", record, "reason must be text"))
    if not isinstance(record.get("sourceRefs"), list) or not all(
        isinstance(item, str) for item in record.get("sourceRefs", [])
    ):
        findings.append(_finding("INVALID_SOURCE_REFS", record, "sourceRefs must be strings"))
    return findings


def _expected_outputs(
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    clean_entities = [{k: v for k, v in item.items() if not k.startswith("__")} for item in entities]
    clean_relations = [{k: v for k, v in item.items() if not k.startswith("__")} for item in relations]
    bundles: dict[str, dict[str, Any]] = {}
    subjects_manifest: dict[str, Any] = {}
    for subject in sorted({item["subject"] for item in clean_entities} | {item["subject"] for item in clean_relations}):
        subject_entities = sorted(
            (item for item in clean_entities if item["subject"] == subject),
            key=lambda item: item["id"],
        )
        subject_relations = sorted(
            (item for item in clean_relations if item["subject"] == subject),
            key=lambda item: item["id"],
        )
        bundle_base = {
            "schemaVersion": SCHEMA_VERSION,
            "dataVersion": DATA_VERSION,
            "subject": subject,
            "entities": subject_entities,
            "relations": subject_relations,
            "counts": {
                "entities": len(subject_entities),
                "relations": len(subject_relations),
            },
        }
        bundle = {**bundle_base, "bundleHash": payload_hash(bundle_base)}
        bundles[subject] = bundle
        subjects_manifest[subject] = {
            "bundlePath": f"subjects/{subject}/dist/knowledge.json",
            "bundleHash": bundle["bundleHash"],
            **bundle["counts"],
        }
    manifest_base = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": DATA_VERSION,
        "subjects": subjects_manifest,
        "totals": {
            "entities": len(clean_entities),
            "relations": len(clean_relations),
        },
        "licenses": {
            "originalData": "CC-BY-4.0",
            "shareAlikeTranscriptions": "CC-BY-SA-4.0",
        },
    }
    manifest = {**manifest_base, "manifestHash": payload_hash(manifest_base)}
    return bundles, manifest


def validate_public_repository(
    root: str | Path,
    *,
    check_dist: bool = True,
) -> list[ValidationFinding]:
    resolved_root = Path(root).resolve()
    try:
        entities, relations = _load_source_records(resolved_root)
    except PublicDataError as exc:
        return [ValidationFinding("INVALID_JSON", ".", str(exc))]
    findings: list[ValidationFinding] = []
    seen_entity_ids: set[str] = set()
    for record in entities:
        findings.extend(_validate_entity(record))
        entity_id = record.get("id")
        if isinstance(entity_id, str):
            if entity_id in seen_entity_ids:
                findings.append(_finding("DUPLICATE_ENTITY_ID", record, "entity id is duplicated"))
            seen_entity_ids.add(entity_id)
    seen_relation_ids: set[str] = set()
    for record in relations:
        findings.extend(_validate_relation(record))
        relation_id = record.get("id")
        if isinstance(relation_id, str):
            if relation_id in seen_relation_ids:
                findings.append(_finding("DUPLICATE_RELATION_ID", record, "relation id is duplicated"))
            seen_relation_ids.add(relation_id)
        if record.get("fromId") not in seen_entity_ids:
            findings.append(_finding("RELATION_SOURCE_MISSING", record, "fromId is not exported"))
        if record.get("toId") not in seen_entity_ids:
            findings.append(_finding("RELATION_TARGET_MISSING", record, "toId is not exported"))
    if check_dist and not findings:
        bundles, manifest = _expected_outputs(entities, relations)
        subjects_root = resolved_root / "subjects"
        if subjects_root.is_dir():
            for path in subjects_root.glob("*/dist/knowledge.json"):
                if path.parents[1].name not in bundles:
                    findings.append(
                        ValidationFinding(
                            "DIST_UNREFERENCED",
                            path.relative_to(resolved_root).as_posix(),
                            "bundle is not referenced by the manifest",
                        )
                    )
        for subject, expected in bundles.items():
            path = resolved_root / "subjects" / subject / "dist" / "knowledge.json"
            try:
                actual = _load_json(path)
            except PublicDataError:
                actual = None
            if actual != expected:
                findings.append(
                    ValidationFinding("DIST_MISMATCH", path.relative_to(resolved_root).as_posix(), "bundle is missing or stale")
                )
        manifest_path = resolved_root / "manifest.json"
        try:
            actual_manifest = _load_json(manifest_path)
        except PublicDataError:
            actual_manifest = None
        if actual_manifest != manifest:
            findings.append(
                ValidationFinding("MANIFEST_MISMATCH", "manifest.json", "manifest is missing or stale")
            )
    return sorted(set(findings))


def build_public_repository(root: str | Path) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    findings = validate_public_repository(resolved_root, check_dist=False)
    if findings:
        codes = ", ".join(sorted({item.code for item in findings}))
        raise PublicDataError(codes)
    entities, relations = _load_source_records(resolved_root)
    bundles, manifest = _expected_outputs(entities, relations)
    subjects_root = resolved_root / "subjects"
    if subjects_root.is_dir():
        for path in subjects_root.glob("*/dist/knowledge.json"):
            if path.parents[1].name not in bundles:
                path.unlink()
    for subject, bundle in bundles.items():
        _atomic_write_json(
            resolved_root / "subjects" / subject / "dist" / "knowledge.json",
            bundle,
        )
    _atomic_write_json(resolved_root / "manifest.json", manifest)
    return manifest


def _stable_relation_id(subject: str, value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"rel-{subject}-{digest[:24]}"


def _extract_relations(subject: str, root: Path) -> list[PublicRelation]:
    if subject == "math":
        value = _load_json(root / "build" / "vault-compiled" / "dependencies.json")
        if not isinstance(value, list):
            raise PublicDataError("INVALID_MATH_RELATIONS")
        return [
            PublicRelation(
                relation_id=_stable_relation_id(
                    subject,
                    {
                        "from": item["prerequisiteId"],
                        "to": item["topicId"],
                        "type": "prerequisite",
                    },
                ),
                subject=subject,
                from_id=str(item["prerequisiteId"]),
                to_id=str(item["topicId"]),
                relation_type="prerequisite",
                reason=str(item.get("reason") or ""),
                source_refs=(),
            )
            for item in value
            if isinstance(item, dict)
        ]
    if subject == "chinese":
        value = _load_json(root / "build" / "vault-compiled" / "relationships.json")
        if not isinstance(value, list):
            raise PublicDataError("INVALID_CHINESE_RELATIONS")
        return [
            PublicRelation(
                relation_id=str(item["id"]),
                subject=subject,
                from_id=str(item["fromId"]),
                to_id=str(item["toId"]),
                relation_type=str(item["relationshipType"]),
                reason=str(item.get("reason") or ""),
                source_refs=tuple(str(ref) for ref in item.get("sourceRefs", [])),
            )
            for item in value
            if isinstance(item, dict)
        ]
    if subject == "english":
        value = _load_json(root / "dist" / "english-runtime.json")
        if not isinstance(value, dict) or not isinstance(value.get("relationships"), list):
            raise PublicDataError("INVALID_ENGLISH_RELATIONS")
        return [
            PublicRelation(
                relation_id=str(item["id"]),
                subject=subject,
                from_id=str(item["from"]),
                to_id=str(item["to"]),
                relation_type=str(item["relation"]),
                reason=str(item.get("body") or ""),
                source_refs=tuple(str(ref) for ref in item.get("sources", [])),
            )
            for item in value["relationships"]
            if isinstance(item, dict)
        ]
    raise PublicDataError(f"INVALID_SUBJECT: {subject}")


def export_three_subjects(
    output_root: str | Path,
    sources: Mapping[str, tuple[Path, str]],
) -> dict[str, Any]:
    entities: list[PublicEntity] = []
    relations: list[PublicRelation] = []
    for subject in sorted(sources):
        root, adapter_name = sources[subject]
        rows = get_adapter(adapter_name).load(root, {"contentHash": "public-export"})
        entities.extend(PublicEntity.from_import_entity(row) for row in rows)
        relations.extend(_extract_relations(subject, root))
    write_public_sources(output_root, entities, relations)
    return build_public_repository(output_root)
