# Primary Knowledge System

Primary Knowledge System (PKS) is one local-first repository containing both
the knowledge engine and a reviewed public baseline for Chinese primary-school
mathematics, Chinese, and English.

The engine provides event-backed JSON and CLI interfaces for querying,
learning, importing, reviewing, synchronizing, and materializing knowledge.
The public baseline contains canonical entities and typed relations without
student records or copyrighted textbook reproductions.

## Included Knowledge

| Subject | Entities | Relations |
| --- | ---: | ---: |
| Mathematics | 374 | 317 |
| Chinese | 735 | 471 |
| English | 432 | 404 |
| Total | 1,541 | 1,192 |

Mathematics and Chinese cover grades 1-6. Formal English abilities begin in
grade 3, while letters and phonetic foundations sit below the grade-3 entry
point. Olympiad mathematics is outside this baseline.

## Repository Layout

```text
src/pks/                    engine and CLI
tests/                      engine and embedded-data tests
manifest.json               deterministic three-subject release manifest
subjects/<subject>/entities one canonical entity per JSON file
subjects/<subject>/relations one typed relation per JSON file
subjects/<subject>/dist      generated runtime bundle
```

The repository never contains student work, photos, mistakes, mastery records,
family observations, textbook scans, exercises, answers, or illustrations.

## Requirements And Install

- Python 3.11 or newer
- Git for public synchronization

```powershell
python -m pip install -e .
```

## Validate The Embedded Baseline

```powershell
pks public-validate --root . --check-dist
pks public-build --root .
git diff --exit-code
```

The build is deterministic. A non-empty Git diff means a source file, bundle,
or manifest is inconsistent.

## Local Queries

```powershell
pks query --vault .\local-vault --input .\query-request.json
pks learn --vault .\local-vault --input .\learn-request.json
pks rebuild --vault .\local-vault
pks repair --vault .\local-vault
```

`learn` requires `callerId`, `requestId`, and `schemaVersion: "1.0"`. The
durable idempotency key is `(callerId, requestId)`.

## Reviewed Public Synchronization

Configure an OpenAI-compatible local review endpoint, then synchronize the
reviewed `main` branch of this same repository:

```powershell
$env:PKS_REVIEW_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
$env:PKS_REVIEW_MODEL = "local-review-model"
$env:PKS_REVIEW_API_KEY = "local-only-key"
pks public-sync --vault .\local-vault `
  --repository-id primary-knowledge-system `
  --url https://github.com/YDB003/primary-knowledge-system.git
```

Every changed entity, relation, and deletion passes deterministic rules and
the configured model. Missing model configuration leaves changes pending.
Rejected changes stay in quarantine and never enter formal search or Obsidian
materialization. See [public synchronization](docs/public-sync.md).

## Optional Source Regeneration

Maintainers can regenerate the baseline from verified local subject sources:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\import-three-subjects.ps1 `
  -Vault .\local-vault `
  -MathRoot <local-math-source> `
  -ChineseRoot <local-chinese-source> `
  -EnglishRoot <local-english-source>
```

Historical `originRepositoryId` values remain stable identity keys even when
an older source repository is no longer published on GitHub.

## Local Vault Layout

- `.pks/events/committed/`: immutable event ledger.
- `.pks/runtime/pks.sqlite3`: rebuildable SQLite projection.
- `.pks/repositories/`: attached repository records.
- `.pks/public-sync/`: immutable reviews, pending records, and quarantine.
- `knowledge/<subject>/<entityId>.md`: Obsidian-readable materialization.
- `PKS:CONTROLLED` is system managed; content after `PKS:FREE` is preserved.

## Development

```powershell
python -m pip install -e . pytest build
python -m pytest -q
python -m pks public-validate --root . --check-dist
python -m pks public-build --root .
git diff --exit-code
python scripts\release_check.py --root .
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), the
[data contract](docs/data-contract.md), and
[provenance policy](docs/provenance.md).

## Licenses

- Engine source: Apache License 2.0, [LICENSE](LICENSE).
- Original project knowledge data: CC BY 4.0, [LICENSE-DATA](LICENSE-DATA).
- Records marked `CC-BY-SA-4.0`: CC BY-SA 4.0,
  [LICENSES/CC-BY-SA-4.0.txt](LICENSES/CC-BY-SA-4.0.txt).
- Referenced third-party standards and publications retain their own rights.

See [NOTICE](NOTICE) for the exact boundary.
