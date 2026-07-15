from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "1.0"
SUBJECTS = {"math", "chinese", "english"}
PRIVACY_KEYS = {
    "studentname",
    "student_name",
    "childname",
    "child_name",
    "school",
    "schoolname",
    "school_name",
    "phone",
    "telephone",
    "email",
    "address",
    "image",
    "imagepath",
    "image_path",
    "photopath",
    "photo_path",
    "homeworkphoto",
    "homework_photo",
}


class ProtocolError(ValueError):
    """A stable, machine-readable protocol error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def canonical_json(data: object) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def payload_hash(data: object) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError("INVALID_REQUEST", f"{name} must be an object")
    return value


def _require_string(
    value: Any,
    name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ProtocolError("INVALID_REQUEST", f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ProtocolError("INVALID_REQUEST", f"{name} must not be empty")
    if len(value) > maximum:
        raise ProtocolError("FIELD_TOO_LONG", f"{name} exceeds {maximum} characters")
    return value


def _reject_privacy_fields(value: Any, path: str = "$" ) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = str(key).replace("-", "_").casefold()
            compact_key = normalized_key.replace("_", "")
            if normalized_key in PRIVACY_KEYS or compact_key in PRIVACY_KEYS:
                raise ProtocolError(
                    "PRIVACY_FIELD_REJECTED",
                    f"student-identifying field is not allowed at {path}.{key}",
                )
            _reject_privacy_fields(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_privacy_fields(child, f"{path}[{index}]")


def _validate_schema_version(data: Mapping[str, Any]) -> None:
    version = data.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise ProtocolError(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"schemaVersion must be {SCHEMA_VERSION}",
        )


def validate_learn_request(data: object) -> dict[str, Any]:
    request = copy.deepcopy(dict(_require_mapping(data, "request")))
    _reject_privacy_fields(request)
    _validate_schema_version(request)

    _require_string(request.get("callerId"), "callerId", maximum=128)
    _require_string(request.get("requestId"), "requestId", maximum=256)
    _require_string(request.get("query"), "query", maximum=4_000)

    candidate = _require_mapping(request.get("candidate"), "candidate")
    _require_string(candidate.get("title"), "candidate.title", maximum=300)
    _require_string(candidate.get("answer"), "candidate.answer", maximum=50_000)
    aliases = candidate.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 100:
        raise ProtocolError("INVALID_REQUEST", "candidate.aliases must be a list of at most 100 strings")
    for index, alias in enumerate(aliases):
        _require_string(alias, f"candidate.aliases[{index}]", maximum=300)

    sources = request.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 100:
        raise ProtocolError("INVALID_REQUEST", "sources must be a list of at most 100 objects")
    for index, source_value in enumerate(sources):
        source = _require_mapping(source_value, f"sources[{index}]")
        _require_string(source.get("sourceRef"), f"sources[{index}].sourceRef", maximum=256)
        for key, maximum in (("title", 500), ("publisher", 300), ("excerpt", 20_000)):
            if source.get(key) is not None:
                _require_string(source[key], f"sources[{index}].{key}", maximum=maximum)
        url = source.get("url")
        if url is not None:
            _require_string(url, f"sources[{index}].url", maximum=2_000)

    context = _require_mapping(request.get("context"), "context")
    subject = context.get("subject")
    if subject not in SUBJECTS:
        raise ProtocolError("INVALID_REQUEST", f"context.subject must be one of {sorted(SUBJECTS)}")
    grade = context.get("actualStudyGrade")
    if grade is not None and (isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 6):
        raise ProtocolError("INVALID_REQUEST", "context.actualStudyGrade must be an integer from 1 to 6")
    for key in ("textbookRef", "task"):
        if context.get(key) is not None:
            _require_string(context[key], f"context.{key}", maximum=500)

    return request
def validate_query_request(data: object) -> dict[str, Any]:
    request = copy.deepcopy(dict(_require_mapping(data, "request")))
    _validate_schema_version(request)
    mode = request.get("mode")
    if mode not in {"search", "entity", "operation"}:
        raise ProtocolError("INVALID_QUERY_MODE", "mode must be search, entity, or operation")

    if request.get("callerId") is not None:
        _require_string(request["callerId"], "callerId", maximum=128)
    if request.get("traceId") is not None:
        _require_string(request["traceId"], "traceId", maximum=256)

    if mode == "search":
        _require_string(request.get("query"), "query", maximum=4_000)
        filters = _require_mapping(request.get("filters", {}), "filters")
        subject = filters.get("subject")
        if subject is not None and subject not in SUBJECTS:
            raise ProtocolError("INVALID_REQUEST", "filters.subject is unsupported")
        grade = filters.get("grade")
        if grade is not None and (isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 6):
            raise ProtocolError("INVALID_REQUEST", "filters.grade must be an integer from 1 to 6")
        if filters.get("entityType") is not None:
            _require_string(filters["entityType"], "filters.entityType", maximum=128)
    elif mode == "entity":
        _require_string(request.get("entityId"), "entityId", maximum=128)
    else:
        has_operation = isinstance(request.get("operationId"), str) and bool(request["operationId"].strip())
        has_request_key = all(
            isinstance(request.get(key), str) and bool(request[key].strip())
            for key in ("callerId", "targetRequestId")
        )
        if not (has_operation or has_request_key):
            raise ProtocolError(
                "INVALID_REQUEST",
                "operation mode requires operationId or callerId plus targetRequestId",
            )
    return request
