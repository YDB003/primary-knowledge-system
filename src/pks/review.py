from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import SUBJECTS, canonical_json, payload_hash
from .public_data import (
    ENTITY_KEYS,
    FORBIDDEN_FIELDS,
    LICENSE_CLASSES,
    ORIGIN_REPOSITORIES,
    RELATION_KEYS,
    SCHEMA_VERSION,
)


WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]+")
UNIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^\s\"'<>]+")


class ModelReviewUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicChange:
    action: str
    record_kind: str
    record: dict[str, Any] | None
    previous_record: dict[str, Any] | None
    repository_id: str
    commit: str

    @property
    def fingerprint(self) -> str:
        record = self.record or self.previous_record or {}
        return payload_hash(
            {
                "repositoryId": self.repository_id,
                "commit": self.commit,
                "action": self.action,
                "recordKind": self.record_kind,
                "recordId": record.get("id"),
                "contentHash": record.get("contentHash"),
            }
        )


@dataclass(frozen=True)
class ReviewContext:
    existing_entities: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class RuleVerdict:
    accepted: bool
    codes: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ModelVerdict:
    decision: str
    reasons: tuple[str, ...]
    subject: str
    grade_start: int | None
    grade_end: int | None
    model: str


@dataclass(frozen=True)
class ReviewResult:
    status: str
    reasons: tuple[str, ...]
    rule_codes: tuple[str, ...]
    model: str | None
    fingerprint: str


class ModelReviewer(Protocol):
    def review(
        self, public_change: PublicChange, context: ReviewContext
    ) -> ModelVerdict: ...


def _contains_absolute_path(value: object) -> bool:
    if isinstance(value, str):
        return bool(WINDOWS_PATH.search(value) or UNIX_HOME_PATH.search(value))
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    return False


class RuleReviewer:
    def review(
        self, public_change: PublicChange, context: ReviewContext
    ) -> RuleVerdict:
        del context
        codes: list[str] = []
        reasons: list[str] = []
        if public_change.action not in {"ADD", "UPDATE", "DELETE"}:
            codes.append("INVALID_CHANGE_ACTION")
            reasons.append("action must be ADD, UPDATE, or DELETE")
        if public_change.record_kind not in {"entity", "relation"}:
            codes.append("INVALID_RECORD_KIND")
            reasons.append("record kind must be entity or relation")
        record = (
            public_change.previous_record
            if public_change.action == "DELETE"
            else public_change.record
        )
        if not isinstance(record, dict):
            codes.append("MISSING_RECORD")
            reasons.append("reviewable record is missing")
            return RuleVerdict(False, tuple(codes), tuple(reasons))

        expected_keys = (
            ENTITY_KEYS if public_change.record_kind == "entity" else RELATION_KEYS
        )
        actual_keys = set(record)
        extra = actual_keys - expected_keys
        missing = expected_keys - actual_keys
        normalized_keys = {
            re.sub(r"[^a-z0-9]", "", key.casefold()) for key in actual_keys
        }
        if extra or normalized_keys & FORBIDDEN_FIELDS:
            codes.append("FORBIDDEN_FIELD")
            reasons.append("record contains fields outside the public contract")
        if missing:
            codes.append("MISSING_FIELD")
            reasons.append("record is incomplete")
        if record.get("schemaVersion") != SCHEMA_VERSION:
            codes.append("INVALID_SCHEMA_VERSION")
            reasons.append("schema version is unsupported")
        if record.get("subject") not in SUBJECTS:
            codes.append("INVALID_SUBJECT")
            reasons.append("subject is unsupported")
        if record.get("licenseClass") not in LICENSE_CLASSES:
            codes.append("INVALID_LICENSE")
            reasons.append("license class is unsupported")
        if _contains_absolute_path(record):
            codes.append("ABSOLUTE_LOCAL_PATH")
            reasons.append("record contains a machine-specific path")

        claimed_hash = record.get("contentHash")
        hash_input = {key: value for key, value in record.items() if key != "contentHash"}
        if not isinstance(claimed_hash, str) or claimed_hash != payload_hash(hash_input):
            codes.append("CONTENT_HASH_MISMATCH")
            reasons.append("content hash does not match record")

        if public_change.record_kind == "entity":
            if record.get("originRepositoryId") != ORIGIN_REPOSITORIES.get(
                str(record.get("subject"))
            ):
                codes.append("INVALID_ORIGIN_REPOSITORY")
                reasons.append("origin repository does not match subject")
            if not isinstance(record.get("title"), str) or not record.get("title", "").strip():
                codes.append("INVALID_TITLE")
                reasons.append("title is required")
            if not isinstance(record.get("summary"), str) or not record.get("summary", "").strip():
                codes.append("INVALID_SUMMARY")
                reasons.append("summary is required")
            for key in ("gradeStart", "gradeEnd"):
                grade = record.get(key)
                if grade is not None and (
                    not isinstance(grade, int) or not 1 <= grade <= 6
                ):
                    codes.append("GRADE_OUT_OF_RANGE")
                    reasons.append(f"{key} must be 1-6 or null")
            start, end = record.get("gradeStart"), record.get("gradeEnd")
            if isinstance(start, int) and isinstance(end, int) and start > end:
                codes.append("INVALID_GRADE_RANGE")
                reasons.append("gradeStart exceeds gradeEnd")
        else:
            if not isinstance(record.get("fromId"), str) or not isinstance(
                record.get("toId"), str
            ):
                codes.append("INVALID_RELATION_ENDPOINT")
                reasons.append("relation endpoints are required")
        unique_codes = tuple(dict.fromkeys(codes))
        unique_reasons = tuple(dict.fromkeys(reasons))
        return RuleVerdict(not unique_codes, unique_codes, unique_reasons)


class OpenAICompatibleReviewer:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None,
        model: str,
        timeout: float = 30,
    ):
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("model endpoint must be HTTP or HTTPS")
        if not model.strip():
            raise ValueError("model name is required")
        if timeout <= 0:
            raise ValueError("model timeout must be positive")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def review(
        self, public_change: PublicChange, context: ReviewContext
    ) -> ModelVerdict:
        prompt = {
            "task": "Review one public primary-school knowledge change.",
            "requirements": [
                "Judge factual correctness and whether the source references support the claim.",
                "Judge subject and canonical grade range for Chinese primary school.",
                "Reject copied textbook prose, exercises, answers, or private student data.",
                "Return JSON only with decision, reasons, subject, gradeStart, gradeEnd.",
            ],
            "change": {
                "action": public_change.action,
                "recordKind": public_change.record_kind,
                "record": public_change.record,
                "previousRecord": public_change.previous_record,
            },
            "existingCandidates": list(context.existing_entities),
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict knowledge-governance reviewer. "
                        "Do not approve uncertain or unsupported content."
                    ),
                },
                {
                    "role": "user",
                    "content": canonical_json(prompt),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=canonical_json(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ModelReviewUnavailable(f"request failed: {exc}") from exc
        try:
            envelope = json.loads(raw)
            content = envelope["choices"][0]["message"]["content"]
            value = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ModelReviewUnavailable("response is not valid review JSON") from exc
        return self._parse_verdict(value)

    def _parse_verdict(self, value: object) -> ModelVerdict:
        if not isinstance(value, dict):
            raise ModelReviewUnavailable("review result must be an object")
        if set(value) != {
            "decision",
            "reasons",
            "subject",
            "gradeStart",
            "gradeEnd",
        }:
            raise ModelReviewUnavailable("review result fields are invalid")
        decision = value["decision"]
        reasons = value["reasons"]
        subject = value["subject"]
        grade_start = value["gradeStart"]
        grade_end = value["gradeEnd"]
        if decision not in {"ACCEPT", "REJECT"}:
            raise ModelReviewUnavailable("decision must be ACCEPT or REJECT")
        if not isinstance(reasons, list) or not reasons or not all(
            isinstance(item, str) and item.strip() for item in reasons
        ):
            raise ModelReviewUnavailable("reasons must be nonempty strings")
        if subject not in SUBJECTS:
            raise ModelReviewUnavailable("subject is invalid")
        for grade in (grade_start, grade_end):
            if grade is not None and (
                not isinstance(grade, int) or not 1 <= grade <= 6
            ):
                raise ModelReviewUnavailable("grades must be 1-6 or null")
        return ModelVerdict(
            decision=decision,
            reasons=tuple(item.strip() for item in reasons),
            subject=subject,
            grade_start=grade_start,
            grade_end=grade_end,
            model=self.model,
        )


def review_change(
    public_change: PublicChange,
    rule_reviewer: RuleReviewer,
    model_reviewer: ModelReviewer | None,
    context: ReviewContext | None = None,
) -> ReviewResult:
    review_context = context or ReviewContext()
    rule_verdict = rule_reviewer.review(public_change, review_context)
    if not rule_verdict.accepted:
        return ReviewResult(
            status="QUARANTINED_LOCAL",
            reasons=rule_verdict.reasons,
            rule_codes=rule_verdict.codes,
            model=None,
            fingerprint=public_change.fingerprint,
        )
    if model_reviewer is None:
        return ReviewResult(
            status="PENDING_LOCAL_REVIEW",
            reasons=("MODEL_REVIEW_NOT_CONFIGURED",),
            rule_codes=(),
            model=None,
            fingerprint=public_change.fingerprint,
        )
    try:
        model_verdict = model_reviewer.review(public_change, review_context)
    except ModelReviewUnavailable as exc:
        return ReviewResult(
            status="PENDING_LOCAL_REVIEW",
            reasons=(f"MODEL_REVIEW_UNAVAILABLE: {exc}",),
            rule_codes=(),
            model=None,
            fingerprint=public_change.fingerprint,
        )
    if model_verdict.decision == "REJECT":
        return ReviewResult(
            status="QUARANTINED_LOCAL",
            reasons=model_verdict.reasons,
            rule_codes=(),
            model=model_verdict.model,
            fingerprint=public_change.fingerprint,
        )
    record = (
        public_change.previous_record
        if public_change.action == "DELETE"
        else public_change.record
    ) or {}
    if public_change.record_kind == "entity" and (
        model_verdict.subject != record.get("subject")
        or model_verdict.grade_start != record.get("gradeStart")
        or model_verdict.grade_end != record.get("gradeEnd")
    ):
        return ReviewResult(
            status="QUARANTINED_LOCAL",
            reasons=("MODEL_CLASSIFICATION_MISMATCH",),
            rule_codes=(),
            model=model_verdict.model,
            fingerprint=public_change.fingerprint,
        )
    return ReviewResult(
        status="ACCEPTED_LOCAL",
        reasons=model_verdict.reasons,
        rule_codes=(),
        model=model_verdict.model,
        fingerprint=public_change.fingerprint,
    )
