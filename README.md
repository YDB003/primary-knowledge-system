# Primary Knowledge System

Primary Knowledge System (PKS) is a local, event-backed knowledge core for
Chinese primary-school learning. It provides governed JSON and CLI interfaces
for querying, learning, importing, reviewing, and materializing knowledge.

The public release contains the engine only. Student work, photos, mistakes,
mastery records, and family observations stay in the local Vault and are never
part of the public repository.

## Architecture

- Immutable events are the machine authority.
- Obsidian Markdown is the readable materialization.
- SQLite is a disposable search projection that can be rebuilt from events.
- Stable external IDs prevent duplicate imports across repository revisions.
- Public GitHub changes pass repository review and local rule-and-model review
  before they can enter the formal local knowledge graph.

## Requirements

- Python 3.11 or newer
- Git for public repository synchronization

## Install

```powershell
python -m pip install -e .
```

## Local Queries

```powershell
pks query --vault .\local-vault --input .\examples\query-request.json
pks learn --vault .\local-vault --input .\examples\learn-request.json
pks rebuild --vault .\local-vault
pks repair --vault .\local-vault
```

`learn` requires `callerId`, `requestId`, and `schemaVersion: "1.0"`.
The durable idempotency key is `(callerId, requestId)`.

## Import Existing Subject Repositories

```powershell
powershell -ExecutionPolicy Bypass -File scripts\import-three-subjects.ps1 `
  -Vault .\local-vault `
  -MathRoot ..\cn-primary-math-taxonomy `
  -ChineseRoot ..\cn-primary-chinese-taxonomy `
  -EnglishRoot ..\cn-primary-english-taxonomy
```

The script accepts Git worktrees or normal directories. It never publishes the
source repositories or the local Vault.

## Reviewed Public Synchronization

Configure any OpenAI-compatible review endpoint, then synchronize the reviewed
`main` branch of the public data repository:

```powershell
$env:PKS_REVIEW_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
$env:PKS_REVIEW_MODEL = "local-review-model"
$env:PKS_REVIEW_API_KEY = "local-only-key"
pks public-sync --vault .\local-vault `
  --repository-id cn-primary-knowledge-base `
  --url https://github.com/YDB003/cn-primary-knowledge-base.git
```

Every changed entity, relation, and deletion passes deterministic rules and the
configured model. Missing model configuration leaves changes pending. Failed
reviews are retained in quarantine and never enter formal search or Obsidian
materialization. See [docs/public-sync.md](docs/public-sync.md).

## Local Vault Layout

- `.pks/events/committed/`: immutable event ledger.
- `.pks/runtime/pks.sqlite3`: rebuildable SQLite projection.
- `.pks/repositories/`: attached repository records.
- `knowledge/<subject>/<entityId>.md`: Obsidian-readable knowledge notes.
- `PKS:CONTROLLED` is system managed; content after `PKS:FREE` is preserved.

## Development

```powershell
python -m pip install -e . pytest build
python -m pytest -q
python scripts\release_check.py --root .
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for public knowledge and code
contribution rules.

## License

The engine is licensed under Apache License 2.0. Public knowledge data is kept
in a separate repository with its own data-license and provenance boundaries.
