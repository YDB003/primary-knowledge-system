from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pks.review import (
    ModelReviewUnavailable,
    ModelVerdict,
    OpenAICompatibleReviewer,
    PublicChange,
    ReviewContext,
    RuleReviewer,
    review_change,
)


def entity(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schemaVersion": "1.0",
        "id": "math-fraction",
        "originRepositoryId": "cn-primary-math-taxonomy",
        "subject": "math",
        "title": "分数的意义",
        "aliases": [],
        "entityType": "concept",
        "gradeStart": 3,
        "gradeEnd": 3,
        "domain": "number",
        "summary": "分数表示把一个整体平均分后的一份或若干份。",
        "sourceRefs": ["curriculum-anchor-1"],
        "licenseClass": "CC-BY-4.0",
    }
    value.update(overrides)
    from pks.contracts import payload_hash

    value["contentHash"] = payload_hash(value)
    return value


def change(record: dict[str, Any] | None = None) -> PublicChange:
    return PublicChange(
        action="ADD",
        record_kind="entity",
        record=record or entity(),
        previous_record=None,
        repository_id="public-cn-primary",
        commit="abc123",
    )


class RecordingModel:
    def __init__(self, verdict: ModelVerdict | Exception):
        self.verdict = verdict
        self.calls: list[PublicChange] = []

    def review(
        self, public_change: PublicChange, context: ReviewContext
    ) -> ModelVerdict:
        del context
        self.calls.append(public_change)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def accepted_verdict(**overrides: Any) -> ModelVerdict:
    values: dict[str, Any] = {
        "decision": "ACCEPT",
        "reasons": ("内容和归类均有依据。",),
        "subject": "math",
        "grade_start": 3,
        "grade_end": 3,
        "model": "review-model",
    }
    values.update(overrides)
    return ModelVerdict(**values)


def test_valid_rule_and_model_review_accepts_change() -> None:
    model = RecordingModel(accepted_verdict())

    result = review_change(change(), RuleReviewer(), model)

    assert result.status == "ACCEPTED_LOCAL"
    assert result.rule_codes == ()
    assert result.model == "review-model"


def test_missing_model_never_accepts() -> None:
    result = review_change(change(), RuleReviewer(), None)

    assert result.status == "PENDING_LOCAL_REVIEW"
    assert result.reasons == ("MODEL_REVIEW_NOT_CONFIGURED",)


def test_rule_failure_short_circuits_model() -> None:
    bad = entity(textbookText="forbidden")
    model = RecordingModel(accepted_verdict())

    result = review_change(change(bad), RuleReviewer(), model)

    assert result.status == "QUARANTINED_LOCAL"
    assert "FORBIDDEN_FIELD" in result.rule_codes
    assert model.calls == []


def test_origin_repository_must_match_subject() -> None:
    bad = entity(originRepositoryId="cn-primary-chinese-taxonomy")
    model = RecordingModel(accepted_verdict())

    result = review_change(change(bad), RuleReviewer(), model)

    assert result.status == "QUARANTINED_LOCAL"
    assert "INVALID_ORIGIN_REPOSITORY" in result.rule_codes
    assert model.calls == []


def test_model_unavailable_or_invalid_stays_pending() -> None:
    model = RecordingModel(ModelReviewUnavailable("invalid JSON"))

    result = review_change(change(), RuleReviewer(), model)

    assert result.status == "PENDING_LOCAL_REVIEW"
    assert "MODEL_REVIEW_UNAVAILABLE" in result.reasons[0]


def test_model_rejection_is_quarantined() -> None:
    model = RecordingModel(
        accepted_verdict(decision="REJECT", reasons=("来源不足。",))
    )

    result = review_change(change(), RuleReviewer(), model)

    assert result.status == "QUARANTINED_LOCAL"
    assert result.reasons == ("来源不足。",)


def test_model_classification_disagreement_is_quarantined() -> None:
    model = RecordingModel(accepted_verdict(subject="chinese"))

    result = review_change(change(), RuleReviewer(), model)

    assert result.status == "QUARANTINED_LOCAL"
    assert result.reasons == ("MODEL_CLASSIFICATION_MISMATCH",)


def test_openai_compatible_reviewer_uses_strict_json_response() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "decision": "ACCEPT",
                            "reasons": ["通过"],
                            "subject": "math",
                            "gradeStart": 3,
                            "gradeEnd": 3,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reviewer = OpenAICompatibleReviewer(
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            api_key="test-key",
            model="review-model",
            timeout=2,
        )
        verdict = reviewer.review(change(), ReviewContext())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert verdict == accepted_verdict(reasons=("通过",))
    assert received[0]["model"] == "review-model"
    assert received[0]["response_format"] == {"type": "json_object"}


def test_openai_compatible_reviewer_reports_unavailable_endpoint() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    reviewer = OpenAICompatibleReviewer(
        endpoint=f"http://127.0.0.1:{port}/v1/chat/completions",
        api_key=None,
        model="review-model",
        timeout=0.1,
    )

    try:
        reviewer.review(change(), ReviewContext())
    except ModelReviewUnavailable as exc:
        assert "request failed" in str(exc)
    else:
        raise AssertionError("unavailable endpoint must not produce a verdict")
