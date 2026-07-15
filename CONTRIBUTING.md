# Contributing

Code and public knowledge contributions are accepted through Pull Requests.
Direct changes to protected `main` are not part of the contribution workflow.

## Code Changes

1. Add or update a failing test before changing behavior.
2. Implement the smallest change that makes the test pass.
3. Run the complete local checks below.
4. Explain behavioral and data-contract changes in the Pull Request.

## Knowledge Changes

1. Search stable IDs, titles, and aliases before creating a new entity.
2. Keep each entity independently understandable, teachable, and testable.
3. Use original explanatory language and include evidence identifiers.
4. Record subject, type, grade range, license class, and source references.
5. Give every relation two valid endpoint IDs, a typed relation, and a reason.
6. Edit source JSON under `entities/` or `relations/`, then rebuild generated
   bundles. Do not edit `dist/knowledge.json` or `manifest.json` by hand.

Textbooks may verify occurrence and location, but no single edition decides
whether canonical knowledge exists. Do not copy textbook prose, lessons,
examples, exercises, answers, illustrations, scans, teacher guides, or
restorable continuous excerpts.

Never submit student names, schools, contact details, homework photos,
handwriting, mistakes, mastery state, private Vault content, API keys, or local
absolute paths. A model's confidence is not evidence.

## Local Checks

```powershell
python -m pip install -e . pytest build
python -m pytest -q
python -m pks public-validate --root . --check-dist
python -m pks public-build --root .
git diff --exit-code
python scripts\release_check.py --root .
python -m build
```

Merging into GitHub does not admit a change into a user's formal local
knowledge base. Local synchronization still requires deterministic rules and
model review; unsuccessful records remain pending or quarantined.
