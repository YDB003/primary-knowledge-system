from __future__ import annotations

import copy

from pks.policy import evaluate_learn, normalize_identity, source_grade
from pks.state import KnowledgeState


def learn_request() -> dict:
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


def existing_state() -> KnowledgeState:
    state = KnowledgeState()
    state.entities["entity-existing"] = {
        "entityId": "entity-existing",
        "title": "Fraction",
        "normalizedTitle": "fraction",
        "subject": "math",
        "entityType": "concept",
        "revision": 4,
        "aliases": ["fraction concept"],
        "normalizedAliases": ["fractionconcept"],
        "claims": [],
        "sources": [],
        "artifacts": [],
        "evidenceLinks": [],
        "observations": [],
        "knowledgeStatus": "EVIDENCE_PENDING",
    }
    return state


def test_identity_normalization_uses_nfkc_casefold_and_no_whitespace() -> None:
    assert normalize_identity("  ＦＲＡＣＴＩＯＮ\nConcept ") == "fractionconcept"


def test_source_free_answer_is_provisional() -> None:
    decision = evaluate_learn(learn_request(), KnowledgeState())

    assert decision.decision == "CREATED"
    assert decision.claim["state"] == "PROVISIONAL"
    assert decision.knowledge_status == "EVIDENCE_PENDING"
    assert decision.sources == []
    assert decision.jobs[0]["jobType"] == "EVIDENCE_ENRICHMENT"


def test_structurally_complete_source_accepts_claim() -> None:
    request = learn_request()
    request["sources"] = [
        {
            "sourceRef": "source-1",
            "title": "Primary mathematics guide",
            "url": None,
            "publisher": "Education Publisher",
            "excerpt": "A fraction can represent part of a whole.",
        }
    ]

    decision = evaluate_learn(request, KnowledgeState())

    assert source_grade(request["sources"][0]) == "C"
    assert decision.sources[0]["grade"] == "C"
    assert decision.claim["state"] == "ACCEPTED"
    assert decision.knowledge_status == "ACCEPTED"
    assert decision.artifacts[0]["sourceId"] == decision.sources[0]["sourceId"]
    assert decision.evidence_links[0]["claimId"] == decision.claim["claimId"]
    assert decision.evidence_links[0]["stance"] == "SUPPORTS"


def test_incomplete_source_remains_grade_d() -> None:
    source = {"sourceRef": "source-1", "title": "A title", "excerpt": "support"}

    assert source_grade(source) == "D"


def test_actual_grade_creates_observation_not_canonical_placement() -> None:
    decision = evaluate_learn(learn_request(), KnowledgeState())

    assert decision.observation["actualStudyGrade"] == 3
    assert decision.observation["observationType"] == "ACTUAL_STUDY_GRADE"
    assert "gradeStart" not in decision.entity
    assert "gradeEnd" not in decision.entity


def test_exact_title_reuses_existing_entity() -> None:
    decision = evaluate_learn(learn_request(), existing_state())

    assert decision.entity["entityId"] == "entity-existing"
    assert decision.parent_revision == 4
    assert decision.new_revision == 5
    assert decision.decision == "ENRICHED"


def test_exact_alias_reuses_existing_entity() -> None:
    request = learn_request()
    request["candidate"]["title"] = "Fraction concept"
    request["candidate"]["aliases"] = []

    decision = evaluate_learn(request, existing_state())

    assert decision.entity["entityId"] == "entity-existing"


def test_same_identity_in_another_subject_does_not_merge() -> None:
    request = copy.deepcopy(learn_request())
    request["context"]["subject"] = "english"

    decision = evaluate_learn(request, existing_state())

    assert decision.entity["entityId"] != "entity-existing"
    assert decision.parent_revision == 0


def test_chinese_poem_keeps_one_entity_with_answer_as_one_claim() -> None:
    request = learn_request()
    request["context"]["subject"] = "chinese"
    request["candidate"].update(
        title="静夜思",
        entityType="poem",
        answer="原文、词义、思想情感和考点共同组成这首诗的知识内容。",
        aliases=[],
    )

    decision = evaluate_learn(request, KnowledgeState())

    assert decision.entity["entityType"] == "poem"
    assert decision.claim["fieldPath"] == "knowledgeContent"
