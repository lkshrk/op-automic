# Implementation Plan: op-aromic GitOps CLI

## Overview

op-aromic is a Python+Typer CLI that brings Terraform/kubectl-style declarative GitOps to Broadcom Automic Workload Automation. Users author YAML manifests in Git, then `validate` → `plan` → `apply` to converge Automic state. The build is staged so each phase ships a usable, testable capability without requiring later phases.

## Verified Current State

- `src/op_aromic/cli/app.py` — Typer app with five command stubs (echo only, no logic)
- `src/op_aromic/client/auth.py` — `TokenAuth` httpx auth class. Uses Bearer token from `/authenticate`. Note: `authenticate` is called inside `auth_flow` but base_url already contains `/ae/api/v1`, so URL becomes `/ae/api/v1/authenticate` — needs verification against Automic AWA REST docs.
- `src/op_aromic/client/http.py` — `AutomicClient` with `get_object`/`create_object`/`update_object`/`delete_object`/`list_objects`. URL shape: `{url}/{client_id}/objects/{name}`. PATCH is used for update — Automic typically uses PUT for full object replacement; this needs confirmation.
- `src/op_aromic/client/errors.py` — Solid exception hierarchy (`AutomicError`, `AuthError`, `NotFoundError`, `ConflictError`, `RateLimitError`).
- `src/op_aromic/config/settings.py` — pydantic-settings with `AUTOMIC_` env prefix.
- `src/op_aromic/models/`, `src/op_aromic/engine/` — empty (just `__init__.py`).
- `tests/test_cli.py` — three smoke tests (version, help, destroy-confirm guard).
- Dependencies already include `deepdiff`, `pyyaml`, `respx` — diff and HTTP mocking are pre-wired.

## Cross-Cutting Design Decisions

### YAML schema (Kubernetes-style multi-doc)

```yaml
apiVersion: aromic.io/v1
kind: Workflow            # JOBP | Job | Schedule | Calendar | Variable | ...
metadata:
  name: DAILY.LOAD.WF     # Automic object name (uppercase, dotted is fine)
  folder: /PROD/ETL       # Automic folder path
  client: 100             # optional override; defaults to settings.client_id
  annotations:
    aromic.io/managed-by: op-aromic
    aromic.io/source: manifests/etl/daily-load.yaml
spec:
  title: "Daily ETL load"
  # type-specific fields below, validated by per-kind Pydantic models
  tasks:
    - name: STEP.EXTRACT
      ref:
        kind: Job
        name: ETL.EXTRACT
    - name: STEP.LOAD
      ref:
        kind: Job
        name: ETL.LOAD
      after: [STEP.EXTRACT]
```

Rationale:
- `apiVersion`/`kind` is the type discriminator — familiar from kubectl, allows schema evolution.
- One file may contain multiple `---`-separated docs (encourages grouping by feature, not by type).
- `metadata.folder` is canonical: name+folder is the identity tuple within a client.
- `spec.tasks[].ref` is a structured reference (not a free-form string) so the engine can build a dependency graph.

### Identity & idempotency

- Identity tuple: `(client, kind, folder, name)`.
- `name` is unique per client in Automic; `folder` is metadata that we enforce.
- A `GET /objects/{name}` is the source-of-truth probe. Existence test = 200 vs 404.
- We do not write a state file. Drift is computed by diffing live Automic JSON against rendered YAML on every `plan`. This avoids state drift and matches the GitOps philosophy.
- Optional ownership annotation (`aromic.io/managed-by`) is written into a Title or Documentation field if Automic exposes one for the kind; never required for correctness, only for `--only-managed` filtering on `destroy`.

### Drift detection (normalization)

Every diff goes through a `Normalizer` that, per kind:
1. Drops volatile fields: `LastModified`, `LastModifiedBy`, `OH_LASTMODIFIED`, `InternalId`, `OH_IDNR`, version numbers, ACL hashes.
2. Sorts list fields where order is not semantic (e.g. tags, ACLs).
3. Casts to canonical types (Automic returns strings for booleans like `"Y"`/`"N"` — coerce to `bool`).
4. Resolves references to canonical form (`{kind, name}` not raw OH_IDNR).

Diff library: `deepdiff.DeepDiff(ignore_order=True, exclude_regex_paths=...)`.

### Dependency ordering for apply

Two-phase apply:
1. **Pass 1 — upsert without refs**: create or update every object with reference fields stripped or pointing to placeholders. This guarantees every name exists in Automic before anything tries to reference it.
2. **Pass 2 — wire refs**: PATCH/PUT each object that has outbound references, now that all targets exist.

Within pass 1 we still topologically sort by `kind` precedence (Calendars → Variables → Jobs → Schedules → Workflows) because some kinds genuinely cannot be created without their refs. Within a kind we use the user-provided file order as a tiebreaker for deterministic output.

`destroy` runs the reverse: Workflows first, then Schedules, Jobs, Variables, Calendars.

### State location

Stateless. No `.aromic.tfstate`. The `plan` is `desired (YAML) - actual (Automic GET) = changeset`. This is the GitOps invariant.

The only local artifact is an optional `--out plan.json` file from `plan` that `apply` can consume to guarantee plan/apply parity (Terraform-style).

### Export round-trip guarantee

`export` produces YAML that, when fed back through `validate` and `plan`, yields zero changes. We enforce this with a property test that runs `export → plan` against a fixture mock and asserts an empty changeset.

---

## Phase 1 — Foundation: Models, YAML loader, validate (no network)

**Goal**: `aromic validate ./manifests` parses YAML, validates against per-kind Pydantic schemas, reports errors with file/line context, exits 0/1 appropriately. No network. Ships a usable linter.

### Files to create

- `src/op_aromic/models/base.py`
  - `Manifest` base Pydantic model with `apiVersion`, `kind`, `metadata`, `spec`.
  - `Metadata` model: `name`, `folder`, `client`, `annotations`.
  - `ObjectRef` model: `kind`, `name`, optional `folder`.
  - `KIND_REGISTRY` dict mapping kind string to spec model class.
- `src/op_aromic/models/workflow.py` — `WorkflowSpec` (JOBP). `tasks: list[WorkflowTask]`, each with `name`, `ref: ObjectRef`, `after: list[str]`.
- `src/op_aromic/models/job.py` — `JobSpec` (JOBS). `host`, `login`, `script` (with subtypes for OS/SQL/etc).
- `src/op_aromic/models/schedule.py` — `ScheduleSpec` (JSCH).
- `src/op_aromic/models/calendar.py` — `CalendarSpec` (CALE).
- `src/op_aromic/models/variable.py` — `VariableSpec` (VARA).
- `src/op_aromic/models/__init__.py` — re-export, populate `KIND_REGISTRY`.
- `src/op_aromic/engine/loader.py`
  - `load_manifests(path: Path) -> list[LoadedManifest]` walks `*.yaml`/`*.yml`, supports multi-doc.
  - `LoadedManifest` frozen dataclass: `source_path`, `doc_index`, `manifest: Manifest`.
  - Raises `ManifestError` with file:line context (use `yaml.SafeLoader` with line tracking, or `ruamel.yaml` if needed).
- `src/op_aromic/engine/validator.py`
  - `validate_manifests(loaded) -> ValidationReport` — checks for: duplicate identity tuples, references resolving to declared objects, naming convention (Automic: uppercase A-Z, 0-9, `.`, `_`, `#`, `$`, `@`, max 200 chars), folder path syntax.
- `src/op_aromic/engine/errors.py` — `ManifestError`, `ValidationError`, `ReferenceError`.

### Files to modify

- `src/op_aromic/cli/app.py` — wire `validate` to call loader+validator, format report via `rich`. Keep stubs for the other commands.

### Tests (TDD)

- `tests/models/test_workflow.py` — happy path + each invalid field
- `tests/models/test_job.py`, `test_schedule.py`, `test_calendar.py`, `test_variable.py`
- `tests/engine/test_loader.py` — multi-doc, missing kind, malformed YAML, file:line in error
- `tests/engine/test_validator.py` — duplicate identity, dangling reference, invalid name chars, name too long
- `tests/cli/test_validate.py` — exit codes (0 clean, 1 errors, 2 warnings + `--strict`)
- `tests/fixtures/manifests/valid/`, `tests/fixtures/manifests/invalid/` — golden files

### Risks / open questions

- **Field set per kind**: Automic exposes hundreds of attributes per object. We should not model them all. Decision: model the common subset; allow `spec.raw: dict` escape hatch for fields not in the schema (passed through verbatim).
- **Naming rules**: confirm Automic's exact allowed character set per kind — schedule names may differ from variable names.
- **Line numbers in Pydantic errors**: Pydantic v2 reports JSON paths, not source lines. Wrap with custom error formatter that maps `loc=("spec","tasks",0,"ref")` back to `path/to/file.yaml:42`.

---

## Phase 2 — Read-only client: list, get, plan

**Goal**: `aromic plan ./manifests` connects to Automic, fetches current state for every desired object, shows a coloured diff, and exits 0 (no changes) or 2 (changes pending). Read-only — never mutates. Ships a safe drift-detection tool.

### Files to create

- `src/op_aromic/client/api.py`
  - Typed wrappers around the raw `AutomicClient`: `get_object_typed(name) -> dict | None` (returns None on 404 instead of raising), `search(kind, folder)`, `object_exists(name)`.
  - This separates "raw HTTP" (`http.py`) from "API semantics" (`api.py`).
- `src/op_aromic/engine/normalizer.py`
  - `Normalizer` protocol; one implementation per kind.
  - `to_canonical(automic_json: dict) -> dict` and `to_canonical(manifest: Manifest) -> dict` — both sides go through it so diffs are apples-to-apples.
  - `IGNORED_FIELDS_BY_KIND: dict[str, set[str]]`.
- `src/op_aromic/engine/differ.py`
  - `compute_diff(desired, actual) -> ObjectDiff` using `deepdiff`.
  - `ObjectDiff`: `action: Literal["create", "update", "delete", "noop"]`, `changes: list[FieldChange]`.
- `src/op_aromic/engine/planner.py`
  - `Plan` frozen dataclass: `creates`, `updates`, `deletes`, `noops`.
  - `build_plan(loaded, client, *, prune=False) -> Plan` — `prune` enables delete detection (only objects matching the managed-by annotation).
- `src/op_aromic/engine/serializer.py`
  - `manifest_to_automic_payload(manifest) -> dict` — the inverse of `Normalizer.to_canonical(manifest)` but in Automic's native field shape (PascalCase, `Y`/`N` booleans, etc.).
- `src/op_aromic/cli/output.py`
  - `render_plan(plan)` — `rich`-based unified diff with `+`/`-`/`~` prefixes, colors per action.

### Files to modify

- `src/op_aromic/cli/app.py` — wire `plan` command. Add `--target`, `--prune`, `--out`, `--no-color` flags.
- `src/op_aromic/client/http.py` — verify and fix the auth URL bug (auth endpoint should not include `/{client_id}/objects` prefix). Add a `get_object_or_none` helper to centralize 404 handling.

### Tests (TDD)

- `tests/client/test_api.py` — uses `respx` to mock Automic; covers list pagination, 404 handling, auth retry on 401
- `tests/engine/test_normalizer.py` — round-trip for each kind: `canonical(manifest) == canonical(canonical_to_automic(manifest))`
- `tests/engine/test_differ.py` — create, update with N field changes, noop, delete (with pruning)
- `tests/engine/test_planner.py` — uses `respx` fixtures; verifies plan structure, target filter, prune behaviour
- `tests/cli/test_plan.py` — exit code 0 (no changes), 2 (changes), 1 (error); `--out plan.json` is valid JSON
- `tests/fixtures/automic/` — captured Automic JSON responses for each kind

### Risks / open questions

- **Verifying real Automic JSON shape** — without a live system, normalizer rules will be guesses. Decision: build a `--record` mode in a follow-up task that captures real responses to `tests/fixtures/automic/`. Until then, model from Broadcom REST docs (Context7) and accept that Phase 2 may need a tuning pass.
- **Pagination contract** of `/objects` — the current `list_objects` assumes `data` key and `max_rows`/`start_row` params. Confirm against AWA REST docs and add a `_paginate` helper if needed.
- **Auth endpoint URL** — `TokenAuth._authenticate` posts to `f"{self._base_url}/authenticate"` where `_base_url` already includes `/ae/api/v1`. AWA's documented endpoint is typically just `/{client}/login` or similar — needs verification before any live call.
- **PATCH vs PUT for updates** — Automic generally requires the full object body. If PATCH is wrong, `update_object` must be changed to PUT with the merged payload.

---

## Phase 3 — Mutating apply with two-pass dependency resolution

**Goal**: `aromic apply ./manifests` executes the Phase 2 plan against Automic. Confirmation prompt unless `--auto-approve`. Reports per-object outcome. Idempotent — re-running on a converged state is a no-op.

### Files to create

- `src/op_aromic/engine/dependency.py`
  - `build_graph(loaded) -> DependencyGraph` — nodes are `ObjectRef`, edges are spec-declared references.
  - `topological_order(graph) -> list[list[ObjectRef]]` — returns levels (so we can apply in parallel within a level later if needed).
  - Cycle detection raises `CyclicDependencyError`.
- `src/op_aromic/engine/applier.py`
  - `apply(plan, client, *, dry_run=False, on_progress=callback) -> ApplyResult`.
  - Implements the two-pass strategy: pass 1 upserts without refs; pass 2 fills refs. Pass 2 only touches objects whose `ref-stripped` and `full` payloads differ — single-pass for ref-less objects.
  - On any failure, halts the current pass, runs no rollback (Automic has no transactions), reports remaining work, exits non-zero.
- `src/op_aromic/engine/destroyer.py`
  - `destroy(loaded, client, *, only_managed=True) -> DestroyResult` in reverse-dependency order.
- `src/op_aromic/cli/prompts.py`
  - Confirmation helper: prints summary counts, asks for `yes` typed verbatim.

### Files to modify

- `src/op_aromic/cli/app.py` — wire `apply` and `destroy`. `apply` accepts `--plan-file plan.json` to apply a previously-saved plan (Terraform parity).
- `src/op_aromic/client/http.py` — add retry-with-backoff for 429 (`RateLimitError`) using `Retry-After` header.

### Tests (TDD)

- `tests/engine/test_dependency.py` — DAG ordering, cycle detection, forward-reference handling
- `tests/engine/test_applier.py` — uses `respx`; covers create, update, two-pass ref wiring, partial failure mid-apply, idempotency (apply twice = second is noop)
- `tests/engine/test_destroyer.py` — reverse order, `only_managed` filter, refusal to delete unmanaged objects
- `tests/cli/test_apply.py` — confirm prompt accepts/rejects, `--auto-approve` skips, plan-file round trip
- `tests/cli/test_destroy.py` — extends existing destroy test

### Risks / open questions

- **Atomicity** — Automic has no multi-object transactions. A mid-apply failure leaves a partially-converged client. Mitigation: report exact remaining work, encourage re-running `apply` (idempotent design makes this safe).
- **Folder creation** — does the Automic API auto-create the folder path or must we create FOLD objects first? If the latter, FOLD becomes implicit in the dependency graph for every object.
- **Concurrent edits** — another user editing in the GUI between `plan` and `apply`. Mitigation: detect by re-fetching before each write and aborting if the object's `LastModified` changed since the plan; `--force` to override.
- **Two-pass overhead** — for ref-less kinds (Calendars, Variables) pass 2 is wasted. Mitigation: only enqueue for pass 2 objects whose payloads differ between ref-stripped and full.

---

## Phase 4 — Export with round-trip guarantee

**Goal**: `aromic export -o ./manifests --filter Workflow` reads Automic and writes YAML that survives `validate → plan` as a no-op. Bootstraps adoption: point at an existing Automic client, get a Git-trackable representation.

### Files to create

- `src/op_aromic/engine/exporter.py`
  - `export(client, output_dir, *, kinds=None, folders=None, layout="by-folder") -> ExportResult`.
  - Uses the same `Normalizer` and inverse `serializer` from Phase 2 — guaranteeing the round-trip by construction.
  - Layouts: `by-folder` (mirror Automic folder tree as filesystem dirs), `by-kind` (`workflows/*.yaml`), `flat` (one big file per folder).
- `src/op_aromic/engine/yaml_writer.py`
  - Stable YAML output: sorted keys per Pydantic model field order, block style, no anchors, trailing newline. Stable output is what makes Git diffs readable.

### Files to modify

- `src/op_aromic/cli/app.py` — wire `export` with `--filter`, `--folder`, `--layout`, `--overwrite` flags.

### Tests (TDD)

- `tests/engine/test_exporter.py` — per-kind happy path with `respx` fixtures
- `tests/engine/test_yaml_writer.py` — stable ordering, idempotent writes (writing the same data twice produces byte-identical files)
- `tests/cli/test_export.py` — CLI flags
- **Round-trip property test** — `tests/engine/test_round_trip.py`: for each fixture in `tests/fixtures/automic/`, run `export → load → plan` and assert empty changeset. This is the quality bar.

### Risks / open questions

- **Lossy export** — fields the schema does not model would vanish. Mitigation: serialize unmodelled fields into `spec.raw` so the round-trip survives even for fields we have not formally typed.
- **Folder layout collisions** — Automic allows `/A/B` and `/A/b` (case sensitivity TBD). Filesystem may not. Mitigation: detect and either warn or use a deterministic suffix.

---

## Phase 5 — Hardening: observability, performance, ergonomics

**Goal**: production-ready. Structured logs, metrics, CI-friendly output, large-manifest performance, friendly errors.

### Files to create

- `src/op_aromic/observability/logging.py` — `structlog` config; JSON output when `--log-format json` or `CI=true`.
- `src/op_aromic/cli/output.py` (extend) — `--output json` for plan/apply/export so CI can parse.
- `src/op_aromic/engine/parallel.py` — bounded `ThreadPoolExecutor` for parallel `get_object` during `plan` build (network is the bottleneck for large manifest sets).

### Files to modify

- `src/op_aromic/client/http.py` — add request-level structured logging (method, path, status, duration); redact `Authorization` header.
- `src/op_aromic/cli/app.py` — global `--log-level`, `--log-format`, `--output` flags.

### Tests

- `tests/cli/test_json_output.py` — schema of JSON output for each command
- `tests/engine/test_parallel.py` — N concurrent gets, error propagation
- `tests/observability/test_logging.py` — secret redaction, JSON formatting
- **Integration smoke test** — `tests/integration/test_live.py` marked `@pytest.mark.integration`, gated by `AROMIC_INTEGRATION=1`, hits a real Automic. Not run in CI by default.

### Risks / open questions

- **Rate limits at scale** — exporting 10k objects may trip quotas. Mitigate with the parallel pool's bounded concurrency and Phase 3's `Retry-After` handling.
- **Memory** — keeping every object in memory for diff is fine at thousands; revisit only if 100k+ becomes a real case.

---

## Testing Strategy Summary

- **Unit**: per-module, no I/O. Models, normalizer, differ, dependency graph, YAML writer.
- **Integration (mocked)**: every command path exercised with `respx`. Realistic fixture JSON in `tests/fixtures/automic/`.
- **CLI (Typer's `CliRunner`)**: argument parsing, exit codes, prompts, output formatting.
- **Round-trip property test** (Phase 4): the cornerstone quality bar.
- **Live integration** (Phase 5): opt-in, gated by env var; not in CI.
- Coverage gate: 80% (already configured in `pyproject.toml`).

## Risks & Mitigations Summary

- **Risk**: Automic JSON shape differs from documentation.
  - Mitigation: build `--record` capture early in Phase 2 against any reachable Automic; encode real shapes in fixtures.
- **Risk**: Auth URL / endpoint shape in `auth.py` is wrong.
  - Mitigation: verify against AWA REST docs before Phase 2 lands; add an `aromic auth check` debug subcommand that just authenticates and exits.
- **Risk**: Two-pass apply produces extra writes.
  - Mitigation: skip pass 2 when ref-stripped equals full payload.
- **Risk**: Mid-apply failures leave half-converged state.
  - Mitigation: idempotent design — re-running `apply` converges remainder. Report remaining work clearly.
- **Risk**: Modelled field set diverges from Automic reality.
  - Mitigation: `spec.raw` escape hatch passes unmodelled fields through unchanged.

## Success Criteria

- [ ] Phase 1: `validate` lints YAML with file:line errors; CI usable as a pre-commit gate
- [ ] Phase 2: `plan` shows accurate diff against live Automic; exit code 2 on changes
- [ ] Phase 3: `apply` is idempotent (running twice on converged state is a no-op); two-pass resolves forward references
- [ ] Phase 4: `export → validate → plan` produces zero changes for any kind
- [ ] Phase 5: structured logs, JSON output, redacted secrets, integration suite available
- [ ] Coverage ≥ 80% measured by `pytest --cov`
- [ ] `ruff check src/` and `mypy --strict src/` clean
