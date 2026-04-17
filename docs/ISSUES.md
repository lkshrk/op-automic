# Open Questions & Problems

Scratchpad populated during autonomous implementation. Each entry:

- **Phase**: 1-5
- **Severity**: blocker / high / medium / low
- **Default chosen**: what was assumed to continue
- **Resolution needed**: what must happen before production use

---

## Pre-existing (from IMPLEMENTATION_PLAN.md)

### Auth endpoint URL shape
- **Phase**: 2
- **Severity**: high
- **Context**: `TokenAuth._authenticate` posts to `f"{base_url}/authenticate"` where `base_url` already contains `/ae/api/v1`. Produces `/ae/api/v1/authenticate`. AWA docs suggest `/{client}/login` or similar.
- **Default chosen**: keep current code; add `aromic auth check` debug subcommand in Phase 5.
- **Resolution needed**: verify against live Automic AWA REST endpoint.

### PATCH vs PUT for updates
- **Phase**: 2/3
- **Severity**: high
- **Context**: `update_object` uses PATCH. Automic typically requires full-body PUT.
- **Default chosen**: assume PUT with full merged payload; switch in Phase 3 applier.
- **Resolution needed**: live verification.
- **2026-04-17 (Phase 2, commit 996349e)**: Flipped `update_object` to use `_UPDATE_METHOD = "PUT"` in `src/op_aromic/client/http.py`. Constant is module-level so a future flip back to PATCH is a one-liner.

### Automic JSON field shape
- **Phase**: 2
- **Severity**: high
- **Context**: no live system; normalizer rules speculated from Broadcom docs.
- **Default chosen**: model common subset; `spec.raw` escape hatch passes unmodelled fields.
- **Resolution needed**: `--record` mode against real instance, capture fixtures.

### Folder auto-creation
- **Phase**: 3
- **Severity**: medium
- **Context**: unclear whether Automic auto-creates folder paths or requires FOLD objects first.
- **Default chosen**: assume auto-create; add FOLD kind if discovered otherwise.
- **Resolution needed**: live verification.

### Pagination contract
- **Phase**: 2
- **Severity**: medium
- **Context**: `list_objects` assumes `data` key, `max_rows`/`start_row` params.
- **Default chosen**: keep current shape; add `_paginate` helper.
- **Resolution needed**: verify against AWA REST docs.

---

## Surfaced during implementation

### Pre-existing mypy errors in client/*
- **Phase**: 1 (surfaced) — **resolved in Phase 2 (2026-04-17)**
- **Severity**: medium
- **Context**: `mypy --strict src/` reports 4 errors in `src/op_aromic/client/auth.py` and `src/op_aromic/client/http.py` (`name-defined` for `httpx.Auth.EventHook`, 3x `no-any-return`). These existed in the scaffold before Phase 1 work and sit in code explicitly out of scope for Phase 1.
- **Default chosen**: left as-is; Phase 2 owns the client rewrite.
- **Resolution**: `EventHook` replaced with `Generator[httpx.Request, httpx.Response, None]`; `no-any-return` resolved by `cast(dict[str, Any], response.json())`.

### Metadata length/character rules moved to validator
- **Phase**: 1
- **Severity**: low
- **Context**: The plan calls for name rules (200 char max, `^[A-Z0-9._#$@]+$`) in the validator. Pydantic `Metadata` previously enforced `max_length=200`, which short-circuited the validator for over-length names. Moved the length cap out of the envelope so the validator is the single source of truth and can report *all* offenders with file:line context.
- **Default chosen**: envelope only enforces non-empty; engine/validator.py owns Automic-specific rules.
- **Resolution needed**: none (design decision).

### Kind to Automic type mapping in client/api.py
- **Phase**: 2
- **Severity**: medium
- **Context**: `list_objects_typed` needs an Automic type string for the `type` query parameter. Mapping `{Workflow→JOBP, Job→JOBS, Schedule→JSCH, Calendar→CALE, Variable→VARA}` is taken from Broadcom AE REST docs and is duplicated in `engine/serializer.py::_KIND_TO_TYPE`. Not verified against a live list endpoint.
- **Default chosen**: keep the map centralised in `client/api.py::_KIND_TO_AUTOMIC_TYPE`; the duplicate in serializer intentionally stays so serializer never imports client.
- **Resolution needed**: confirm against `/ae/api/v1/{client}/objects?type=...` on a live instance.

### Concurrency marker capture lives outside ObjectDiff
- **Phase**: 3
- **Severity**: medium
- **Context**: `engine/normalizer.py::_COMMON_IGNORED` strips `LastModified` as
  a volatile field, so `ObjectDiff.actual` has no optimistic-concurrency
  marker baked in. Phase 2 is frozen so the planner cannot attach one to the
  diff directly.
- **Default chosen**: `engine/applier.capture_plan_markers(plan, client)`
  snapshots a marker for every update/delete immediately after plan time. The
  CLI calls it between `build_plan` and `apply`, then passes the dict via
  `apply(..., plan_markers=...)`. The applier itself does one extra GET per
  write to detect drift against that dict.
- **Resolution needed**: once the real Automic marker field is confirmed,
  consider folding it into the planner's diff so the two-step call collapses.

### 429 retry window (3 total attempts, exponential fallback)
- **Phase**: 3
- **Severity**: low
- **Context**: `client/http._send_with_retry` honours `Retry-After` up to
  three total attempts before raising `RateLimitError`. Malformed headers
  fall back to `(0.5s, 1.0s)` exponential backoff. Constants at module
  scope so ops can retune without touching call sites.
- **Default chosen**: 3 attempts; no circuit breaker; per-request (not
  global) budget.
- **Resolution needed**: once a live Automic surfaces actual rate limits
  we may want a global budget so ten concurrent items do not each retry
  three times.

### Apply halts pass on first failure, does not roll back
- **Phase**: 3
- **Severity**: medium
- **Context**: `engine/applier.apply` stops the current pass on any
  write failure, marks the rest of the pass as `Skipped`, and returns
  `ApplyResult.status == "partial"`. Automic has no cross-object
  transactions, so rollback would require custom compensating writes;
  instead we rely on idempotent re-run.
- **Default chosen**: no rollback; re-run converges remainder.
- **Resolution needed**: consider a `--rollback-on-error` flag in Phase 5
  if customers report half-applied manifests.

### Apply-driven deletes vs destroyer
- **Phase**: 3
- **Severity**: low
- **Context**: `apply` still processes `plan.deletes` when the plan was
  built with `--prune`. This overlaps with `destroy`. The two paths are
  intentionally different: `apply --prune` removes orphans inline
  alongside other changes; `destroy` is an explicit reverse-order sweep
  of every declared manifest.
- **Default chosen**: keep both; document the distinction.
- **Resolution needed**: none (by design).

### Round-trip shape: per-kind vs full-set
- **Phase**: 4
- **Severity**: low
- **Context**: The validator's reference-resolution rule requires every
  `ObjectRef` in a manifest set to resolve to another manifest in the same
  set. For non-leaf kinds (Workflow → Jobs, Schedule → Workflow) a
  naive single-kind export therefore fails `validate` not because the
  exporter is wrong but because the validator (correctly) sees dangling
  refs. The round-trip property test splits into two shapes: leaf kinds
  (Calendar, Variable, Job) round-trip per-kind; the flagship full-set
  test exports everything together so refs resolve.
- **Default chosen**: document the split in `tests/engine/test_round_trip.py`
  and keep the flagship full-set test as the Phase 4 quality bar.
- **Resolution needed**: none (design decision; matches real usage where
  `aromic export` pulls the whole adoption corpus at once).

### Layout "by-folder" leaf-directory convention
- **Phase**: 4
- **Severity**: low
- **Context**: `by-folder` layout mirrors Automic's folder tree onto the
  filesystem, writing one file per folder. The convention chosen is
  `<root>/<parent>/<leaf>/<leaf>.yaml` so each folder carries a file named
  after itself — predictable, and opens a place for a future
  `<leaf>/README.md` or per-folder index without collisions.
- **Default chosen**: put the file inside its leaf directory; tests assert
  this exact path.
- **Resolution needed**: none; can be revisited if operators push back.

### Managed-object prune detection heuristic
- **Phase**: 2
- **Severity**: medium
- **Context**: `_is_managed` checks `payload["Annotations"]["aromic.io/managed-by"] == "op-aromic"` OR the string `aromic.io/managed-by=op-aromic` in `payload["Documentation"]`. Automic has no native annotations; the real wire field is unknown.
- **Default chosen**: accept either shape; exporter / applier will write into `Documentation` so plan can detect managed orphans on `--prune`.
- **Resolution needed**: pick one canonical location once a live instance shows which fields survive round-tripping.


### `aromic auth check` is the manual verification for the auth URL
- **Phase**: 5
- **Severity**: medium
- **Context**: `_AUTH_PATH = "/authenticate"` is concatenated onto the
  configured `AUTOMIC_URL` (which already contains `/ae/api/v1`). We have
  not verified this shape against a live AWA instance — Broadcom docs
  hint at `/authenticate` but different versions may prefix or rename it.
- **Default chosen**: keep `/authenticate`; ship `aromic auth check` as
  the one-shot probe operators run against their instance to confirm the
  URL shape and credential mapping work end-to-end before depending on
  `plan`/`apply` in automation.
- **Resolution needed**: track the first failing live run; update
  `_AUTH_PATH` or the settings docs once the real shape is known.
