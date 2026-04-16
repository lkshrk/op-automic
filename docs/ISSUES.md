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
- **Phase**: 1 (surfaced)
- **Severity**: medium
- **Context**: `mypy --strict src/` reports 4 errors in `src/op_aromic/client/auth.py` and `src/op_aromic/client/http.py` (`name-defined` for `httpx.Auth.EventHook`, 3x `no-any-return`). These existed in the scaffold before Phase 1 work and sit in code explicitly out of scope for Phase 1.
- **Default chosen**: left as-is; Phase 2 owns the client rewrite.
- **Resolution needed**: fix when Phase 2 touches `client/http.py` and `client/auth.py`.

### Metadata length/character rules moved to validator
- **Phase**: 1
- **Severity**: low
- **Context**: The plan calls for name rules (200 char max, `^[A-Z0-9._#$@]+$`) in the validator. Pydantic `Metadata` previously enforced `max_length=200`, which short-circuited the validator for over-length names. Moved the length cap out of the envelope so the validator is the single source of truth and can report *all* offenders with file:line context.
- **Default chosen**: envelope only enforces non-empty; engine/validator.py owns Automic-specific rules.
- **Resolution needed**: none (design decision).

