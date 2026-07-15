from __future__ import annotations

from pks.events import new_event_id
from pks.state import reduce_events


def applied_event(
    *,
    caller: str = "agent-a",
    request_id: str = "request-1",
    operation_id: str = "operation-1",
    entity_id: str = "entity-1",
    parent_revision: int = 0,
    new_revision: int = 1,
    claim_id: str = "claim-1",
    field_path: str = "answer",
    value: str = "A fraction is a part of a whole.",
) -> dict:
    response = {
        "operationId": operation_id,
        "decision": "CREATED" if parent_revision == 0 else "ENRICHED",
        "entityId": entity_id,
        "entityRevision": new_revision,
    }
    return {
        "eventId": new_event_id(),
        "eventType": "OPERATION_APPLIED",
        "policyVersion": "1.0",
        "operation": {
            "operationId": operation_id,
            "callerId": caller,
            "requestId": request_id,
            "payloadHash": f"hash-{request_id}",
            "status": "APPLIED",
            "response": response,
        },
        "entityMutation": {
            "entityId": entity_id,
            "parentRevision": parent_revision,
            "newRevision": new_revision,
            "title": "Fraction",
            "normalizedTitle": "fraction",
            "subject": "math",
            "entityType": "concept",
            "aliasesAdded": [],
            "claim": {
                "claimId": claim_id,
                "fieldPath": field_path,
                "value": value,
                "state": "ACCEPTED",
            },
            "sources": [],
            "observation": None,
            "knowledgeStatus": "ACCEPTED",
        },
    }


def test_replay_builds_entity_and_operation_indexes() -> None:
    event = applied_event()

    state = reduce_events([event])

    entity = state.entities["entity-1"]
    assert entity["revision"] == 1
    assert entity["claims"][0]["claimId"] == "claim-1"
    assert state.operations_by_id["operation-1"]["status"] == "APPLIED"
    assert state.operations_by_key[("agent-a", "request-1")]["payloadHash"] == "hash-request-1"


def test_sequential_enrichment_advances_revision() -> None:
    first = applied_event()
    second = applied_event(
        caller="agent-b",
        request_id="request-2",
        operation_id="operation-2",
        parent_revision=1,
        new_revision=2,
        claim_id="claim-2",
        field_path="commonError",
        value="The denominator is not the size of one selected part.",
    )

    state = reduce_events([first, second])

    assert state.entities["entity-1"]["revision"] == 2
    assert len(state.entities["entity-1"]["claims"]) == 2
    assert state.entity_count == 1


def test_nonconflicting_same_parent_branch_auto_merges() -> None:
    first = applied_event()
    branch = applied_event(
        caller="agent-b",
        request_id="request-2",
        operation_id="operation-2",
        parent_revision=0,
        new_revision=1,
        claim_id="claim-2",
        field_path="example",
        value="One half is a fraction.",
    )

    state = reduce_events([first, branch])

    assert state.entities["entity-1"]["revision"] == 2
    assert len(state.entities["entity-1"]["claims"]) == 2
    assert len(state.merged_branches) == 1
    assert not state.conflicts


def test_conflicting_same_parent_branch_is_preserved_as_conflict() -> None:
    first = applied_event(value="A fraction is a part of a whole.")
    branch = applied_event(
        caller="agent-b",
        request_id="request-2",
        operation_id="operation-2",
        parent_revision=0,
        new_revision=1,
        claim_id="claim-2",
        value="A fraction is always greater than one.",
    )

    state = reduce_events([first, branch])

    assert state.entities["entity-1"]["revision"] == 1
    assert state.entities["entity-1"]["claims"][0]["value"] == "A fraction is a part of a whole."
    assert state.conflicts[0]["errorCode"] == "REVISION_CONFLICT"
    assert state.conflicts[0]["incomingEventId"] == branch["eventId"]


def test_events_are_reduced_in_event_id_order() -> None:
    first = applied_event()
    second = applied_event(
        caller="agent-b",
        request_id="request-2",
        operation_id="operation-2",
        parent_revision=1,
        new_revision=2,
        claim_id="claim-2",
        field_path="example",
        value="One half is a fraction.",
    )

    state = reduce_events([second, first])

    assert state.entities["entity-1"]["revision"] == 2


def test_repository_import_rolls_back_all_mutations_when_one_conflicts() -> None:
    first = applied_event()
    second = applied_event(
        caller="agent-b",
        request_id="request-2",
        operation_id="operation-2",
        entity_id="entity-2",
        claim_id="claim-2",
    )
    valid_update = applied_event(
        parent_revision=1,
        new_revision=2,
        operation_id="unused-1",
        claim_id="claim-3",
        field_path="example",
        value="One half is a fraction.",
    )["entityMutation"]
    conflicting_update = applied_event(
        entity_id="entity-2",
        operation_id="unused-2",
        claim_id="claim-4",
        value="A fraction is always greater than one.",
    )["entityMutation"]
    import_event = {
        "eventId": new_event_id(),
        "eventType": "REPOSITORY_IMPORT_APPLIED",
        "policyVersion": "1.0",
        "operation": {
            "operationId": "repository-import-1",
            "callerId": "repository-import",
            "requestId": "repo:none->snapshot",
            "payloadHash": "repository-payload",
            "status": "APPLIED",
            "response": {"operationStatus": "APPLIED", "entityIds": ["entity-1", "entity-2"]},
            "durableCommandEnvelope": {"action": "import"},
        },
        "repositorySnapshot": {
            "repositoryId": "repo",
            "contentHash": "snapshot",
        },
        "entityMutations": [valid_update, conflicting_update],
    }

    state = reduce_events([first, second, import_event])

    assert state.entities["entity-1"]["revision"] == 1
    assert state.entities["entity-2"]["revision"] == 1
    assert state.operations_by_id["repository-import-1"]["status"] == "CONFLICT"
    assert "repo" not in state.repository_snapshots
