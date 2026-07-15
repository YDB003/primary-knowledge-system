# Unified Public System Design

## Goal

Publish one GitHub repository, `YDB003/primary-knowledge-system`, containing
both the PKS engine and the reviewed mathematics, Chinese, and English public
knowledge baseline. After the unified repository passes all release checks,
delete the other three GitHub repositories while retaining their local copies.

## Repository Layout

The engine remains at the repository root. Public knowledge also lives at the
root because the existing public synchronization protocol already expects
`manifest.json` and `subjects/` there.

```text
primary-knowledge-system/
  src/
  tests/
  scripts/
  docs/
  manifest.json
  subjects/
    math/
    chinese/
    english/
  LICENSE
  LICENSE-DATA
  LICENSES/
```

The root layout avoids a new `--data-path` option and keeps clones, validation,
RAG ingestion, and `pks public-sync` compatible with the current contract.

## Data And License Boundaries

- Engine source remains Apache License 2.0 under `LICENSE`.
- Original public knowledge data uses CC BY 4.0 under `LICENSE-DATA`.
- Records marked `CC-BY-SA-4.0` retain the full license text under `LICENSES/`.
- `NOTICE` explains which files are code, original data, share-alike data, and
  third-party references.
- Student work, photos, mistakes, mastery state, family observations, textbook
  scans, exercises, answers, and illustrations remain excluded.

## Documentation And Contribution Flow

The root README becomes the single entry point for installation, data counts,
repository layout, validation, and reviewed synchronization. Public sync uses:

```powershell
pks public-sync --vault .\local-vault `
  --repository-id cn-primary-knowledge-base `
  --url https://github.com/YDB003/primary-knowledge-system.git
```

The stable logical ID is retained so existing local state can migrate from the
former public-data URL without creating duplicate entities. The engine permits
only this explicit old-to-new URL move and rejects arbitrary rebindings.

Contribution guidance covers code and knowledge changes. `CODEOWNERS` assigns
the repository owner to `subjects/`, `manifest.json`, and data-license files.
All future public changes continue through protected Pull Requests.

## CI And Release

The existing Python 3.11, 3.12, and 3.13 matrix continues to run the full test
suite, release scanner, and package build. Every matrix job also validates the
embedded public data, rebuilds deterministic bundles, and requires a clean Git
diff. A regression test verifies that the checked-in baseline contains exactly
1,541 entities and 1,192 relations across all three subjects.

The unified repository is released as `v0.3.0`. The release includes the Python
wheel and GitHub source archives. Branch protection remains enabled after the
migration.

## Migration And Deletion Order

1. Copy the reviewed `manifest.json`, `subjects/`, data licenses, and provenance
   documents into an isolated migration branch.
2. Update tests, CI, README, contribution guidance, notices, and sync examples.
3. Run local tests, release scan, package build, deterministic data rebuild, and
   a no-diff check.
4. Push a Pull Request and wait for every GitHub Actions job to pass.
5. Merge the migration administratively because the repository owner cannot
   approve a Pull Request authored by the same GitHub identity.
6. Fresh-clone `primary-knowledge-system`, install it, validate the embedded
   data, and run public synchronization against the unified GitHub URL.
7. Publish `v0.3.0`.
8. Delete these GitHub repositories only after all prior checks pass:
   `cn-primary-knowledge-base`, `cn-primary-math-taxonomy`, and
   `travel-plan-skill`.
9. Verify the GitHub account contains only `primary-knowledge-system`.

Local copies of deleted GitHub repositories are not deleted. They provide a
recovery source if GitHub deletion must later be reversed.

## Failure Handling

- Any local or GitHub CI failure stops the migration before repository deletion.
- Any mismatch after deterministic data rebuild stops the migration.
- Failure to acquire GitHub repository-deletion permission leaves old
  repositories intact and reports the external authorization blocker.
- Public synchronization without a configured local model must import zero
  records and retain candidates as pending local review.
