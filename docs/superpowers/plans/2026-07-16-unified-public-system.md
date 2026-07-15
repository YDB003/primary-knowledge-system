# Unified Public System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the reviewed three-subject public baseline into `primary-knowledge-system`, publish `v0.3.0`, and delete every other GitHub repository owned by `YDB003`.

**Architecture:** Keep the Python engine and public data at one repository root. The existing validator and synchronizer continue to consume `manifest.json` and `subjects/` without a new path option. Code and data retain separate license files while sharing one protected Pull Request and CI workflow.

**Tech Stack:** Python 3.11-3.13, pytest, Git, GitHub Actions, GitHub CLI, deterministic JSON bundles.

## Global Constraints

- Keep `YDB003/primary-knowledge-system` as the only GitHub repository.
- Preserve local copies of deleted GitHub repositories.
- Delete old GitHub repositories only after fresh-clone and synchronization verification.
- Public sync still requires deterministic rules plus a configured local model.
- Publish the unified repository as `v0.3.0`.

---

### Task 1: Embed The Reviewed Baseline

**Files:**
- Create: `tests/test_embedded_baseline.py`
- Create: `manifest.json`
- Create: `subjects/math/**`
- Create: `subjects/chinese/**`
- Create: `subjects/english/**`

**Interfaces:**
- Consumes: `pks.public_data.validate_public_repository(root, check_dist=True)`.
- Produces: a repository-root public data source compatible with `pks public-sync`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from pks.public_data import validate_public_repository


ROOT = Path(__file__).resolve().parents[1]


def test_embedded_public_baseline_is_complete() -> None:
    findings = validate_public_repository(ROOT, check_dist=True)
    assert findings == []
    assert (ROOT / "manifest.json").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_embedded_baseline.py`
Expected: FAIL because `manifest.json` and `subjects/` are absent.

- [ ] **Step 3: Copy the reviewed data sources and generated bundles**

Copy `manifest.json` and `subjects/` from the locally verified
`cn-primary-knowledge-base-public` checkout without its `.git` directory.

- [ ] **Step 4: Complete count assertions and run the test**

Assert exactly 1,541 entities, 1,192 relations, and subject counts from the
manifest. Run `python -m pytest -q tests/test_embedded_baseline.py` and expect
PASS.

- [ ] **Step 5: Commit**

```bash
git add manifest.json subjects tests/test_embedded_baseline.py
git commit -m "feat: embed reviewed three-subject baseline"
```

### Task 2: Unify Licensing And Documentation

**Files:**
- Create: `LICENSE-DATA`
- Create: `LICENSES/CC-BY-SA-4.0.txt`
- Create: `docs/data-contract.md`
- Create: `docs/provenance.md`
- Modify: `README.md`
- Modify: `NOTICE`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/public-sync.md`
- Create: `.github/CODEOWNERS`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the embedded `manifest.json` and subject tree from Task 1.
- Produces: one public entry point and explicit code/data rights boundaries.

- [ ] **Step 1: Copy data license and provenance files**

Copy the verified CC BY 4.0 license as `LICENSE-DATA`, the CC BY-SA 4.0 text,
and the data contract/provenance documents.

- [ ] **Step 2: Rewrite public entry points**

Document the combined code and data layout, counts, validation commands, and
the self-repository synchronization URL. Remove claims that data is separate.

- [ ] **Step 3: Add ownership and bump package version**

Assign `subjects/`, `manifest.json`, and data license files to `@YDB003`; bump
the package from `0.2.0` to `0.3.0`.

- [ ] **Step 4: Run release scanning and reference checks**

Run `python scripts/release_check.py --root .` and verify the old public data
repository URL no longer appears in active docs.

- [ ] **Step 5: Commit**

```bash
git add README.md NOTICE CONTRIBUTING.md docs LICENSE-DATA LICENSES .github/CODEOWNERS pyproject.toml
git commit -m "docs: unify public system and data boundaries"
```

### Task 3: Make CI Validate Code And Data Together

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `pks public-validate`, `pks public-build`, pytest, and package build.
- Produces: one required CI matrix for the monorepo.

- [ ] **Step 1: Add embedded data checks to CI**

Add validation, deterministic rebuild, and `git diff --exit-code` after tests.

- [ ] **Step 2: Run the exact CI sequence locally**

```powershell
python -m pytest -q
python -m pks public-validate --root . --check-dist
python -m pks public-build --root .
git diff --exit-code
python scripts/release_check.py --root .
python -m build
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: validate unified code and knowledge repository"
```

### Task 4: Publish And Verify The Unified Repository

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: the migration branch and GitHub protected `main`.
- Produces: merged `main`, green Actions, and release `v0.3.0`.

- [ ] **Step 1: Push the branch and open a Pull Request**
- [ ] **Step 2: Wait for every required GitHub Actions job**
- [ ] **Step 3: Administratively squash-merge the owner-authored migration PR**
- [ ] **Step 4: Fresh-clone the merged repository and rerun tests and data checks**
- [ ] **Step 5: Run `pks public-sync` against the unified GitHub URL with no model and assert zero imported records**
- [ ] **Step 6: Publish `v0.3.0` with the built wheel**

### Task 5: Delete Superseded GitHub Repositories

**Files:**
- No local files are deleted.

**Interfaces:**
- Consumes: verified unified release `v0.3.0`.
- Produces: a GitHub account containing only `primary-knowledge-system`.

- [ ] **Step 1: Verify the unified repository and release are public and healthy**
- [ ] **Step 2: Delete `cn-primary-knowledge-base`**
- [ ] **Step 3: Delete `cn-primary-math-taxonomy`**
- [ ] **Step 4: Delete `travel-plan-skill`**
- [ ] **Step 5: List the account repositories and assert only `primary-knowledge-system` remains**
