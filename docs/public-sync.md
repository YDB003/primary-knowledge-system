# Public Knowledge Synchronization

PKS treats GitHub `main` as a reviewed upstream candidate source, not as the
local machine authority. A merged public change must pass a second local review
before it can enter the local event ledger.

## Review Sequence

1. Resolve the current `main` commit.
2. Clone that exact commit into a Vault-contained immutable snapshot.
3. Validate the public manifest, bundles, hashes, IDs, licenses, and relations.
4. Compare records with the last observed public commit.
5. Apply deterministic privacy, provenance, grade, and schema rules.
6. Request a structured decision from the configured model.
7. Admit, quarantine, or retain the change for retry.
8. Import only the admitted bundle through the event-backed repository path.

Rule failure immediately quarantines the record. A missing model, timeout,
network error, or invalid model response produces `PENDING_LOCAL_REVIEW`.
There is no rules-only or model-only acceptance mode.

## Model Configuration

```powershell
$env:PKS_REVIEW_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
$env:PKS_REVIEW_MODEL = "local-review-model"
$env:PKS_REVIEW_API_KEY = "local-only-key"
$env:PKS_REVIEW_TIMEOUT_SECONDS = "30"
```

`PKS_REVIEW_ENDPOINT` and `PKS_REVIEW_MODEL` must be set together. The API key
is optional for local endpoints. The endpoint receives only the public change
and local canonical candidates; student observations are not part of this
request.

## Run

```powershell
pks public-sync --vault .\local-vault `
  --repository-id cn-primary-knowledge-base `
  --url https://github.com/YDB003/cn-primary-knowledge-base.git
```

Only `main` is accepted. Fork branches, open Pull Requests, Issues, and
unreviewed commits are never synchronization inputs.

## Local Records

- `.pks/public-sync/reviews/`: immutable local review attempts.
- `.pks/public-sync/quarantine/`: rule or model rejections.
- `.pks/public-sync/pending/`: retryable model failures or missing model setup.
- `.pks/public-sync/approved/`: deterministic admitted bundles.
- `.pks/public-sync/states/`: last observed commit and accepted record state.
- `.pks/tombstones/`: recoverable entities removed after approved deletions.

Repeating the same commit is idempotent. If the process stops after importing
an entity but before writing synchronization state, the next run reuses the
terminal review and the repository import remains idempotent.
