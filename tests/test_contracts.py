from __future__ import annotations

import copy

import pytest

from pks.contracts import (
    ProtocolError,
    canonical_json,
    payload_hash,
    validate_learn_request,
    validate_query_request,
)


def valid_learn_request() -> dict:
    return {
        "callerId": "agent-a",
        "requestId": "request-1",
        "schemaVersion": "1.0",
        "query": "What is a fraction?",
        "candidate": {
            "title": "Fraction",
            "answer": "A fraction represents a part of a whole.",
            "aliases": ["fraction concept"],
        },
        "sources": [],
        "context": {
            "subject": "math",
            "actualStudyGrade": 3,
            "textbookRef": None,
            "task": "homework-check",
        },
    }


def test_valid_learn_request_is_normalized_without_mutating_input() -> None:
    request = valid_learn_request()
    original = copy.deepcopy(request)

    result = validate_learn_request(request)

    assert result == original
    assert request == original
    assert result is not request


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("context", "studentName"), "Child A"),
        (("context", "school"), "Primary School"),
        (("candidate", "phone"), "13800000000"),
        (("sources", 0, "imagePath"), "C:" + "/homework/photo.jpg"),
    ],
)
def test_learn_rejects_student_identifiers(path: tuple, value: str) -> None:
    request = valid_learn_request()
    if path[0] == "sources":
        request["sources"].append({"sourceRef": "s1"})
    target = request
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ProtocolError, match="PRIVACY_FIELD_REJECTED"):
        validate_learn_request(request)


def test_learn_rejects_unknown_schema_version() -> None:
    request = valid_learn_request()
    request["schemaVersion"] = "2.0"

    with pytest.raises(ProtocolError, match="UNSUPPORTED_SCHEMA_VERSION"):
        validate_learn_request(request)


def test_learn_requires_nonempty_candidate_answer() -> None:
    request = valid_learn_request()
    request["candidate"]["answer"] = "  "

    with pytest.raises(ProtocolError, match="INVALID_REQUEST"):
        validate_learn_request(request)


def test_payload_hash_ignores_mapping_order() -> None:
    left = {"a": 1, "b": {"c": 2, "d": 3}}
    right = {"b": {"d": 3, "c": 2}, "a": 1}

    assert canonical_json(left) == canonical_json(right)
    assert payload_hash(left) == payload_hash(right)


@pytest.mark.parametrize("mode", ["search", "entity", "operation"])
def test_query_accepts_supported_modes(mode: str) -> None:
    request = {"schemaVersion": "1.0", "mode": mode}
    if mode == "search":
        request.update(query="fraction", filters={})
    elif mode == "entity":
        request["entityId"] = "entity-1"
    else:
        request["operationId"] = "operation-1"

    assert validate_query_request(request) == request


def test_query_rejects_unsupported_mode() -> None:
    with pytest.raises(ProtocolError, match="INVALID_QUERY_MODE"):
        validate_query_request({"schemaVersion": "1.0", "mode": "graph"})
