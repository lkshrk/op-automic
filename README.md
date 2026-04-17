# op-aromic

GitOps CLI for Broadcom Automic Workload Automation.

Declare Automic objects as YAML in Git, then `validate` → `plan` → `apply` to
converge live state — the Terraform model, applied to AE.

## Install

```bash
git clone <repo-url> op-aromic
cd op-aromic
uv sync
uv run aromic --help
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## Quick start

### 1. Configure credentials

```bash
cp .env.example .env
# edit .env:
#   AUTOMIC_URL=https://automic.example.com/ae/api/v1
#   AUTOMIC_CLIENT_ID=100
#   AUTOMIC_USER=USER          (or CLIENT/USER or CLIENT/USER/DEPT)
#   AUTOMIC_PASSWORD=secret
```

Verify auth works:

```bash
uv run aromic auth check
```

### 2. Write a manifest

```yaml
# manifests/etl/daily-load.yaml
apiVersion: aromic.io/v1
kind: Workflow
metadata:
  name: DAILY.LOAD.WF
  folder: /PROD/ETL
spec:
  tasks:
    - name: STEP.EXTRACT
      ref: { kind: Job, name: ETL.EXTRACT }
    - name: STEP.LOAD
      ref: { kind: Job, name: ETL.LOAD }
      after: [STEP.EXTRACT]
```

More complete examples live under `examples/` — one per supported kind.

### 3. Validate, plan, apply

```bash
uv run aromic validate manifests/           # schema + dependency check
uv run aromic plan manifests/               # show diff vs live AE
uv run aromic apply manifests/              # apply (interactive prompt)
uv run aromic apply manifests/ --auto-approve --dry-run   # no writes
```

### 4. Export existing Automic objects to YAML

```bash
uv run aromic export -o ./manifests --layout by-folder
```

## Supported kinds

| Kind | Automic type | Status |
|------|--------------|--------|
| Workflow | JOBP | v21 nested shape |
| Job | JOBS | v21 nested shape |
| Variable | VARA | v21 nested shape |
| Calendar | CALE | best-effort (needs live verification) |
| Schedule | JSCH | best-effort (needs live verification) |

## Configuration precedence

Highest priority wins:

1. CLI flags (`--log-level`, `--max-workers`, …)
2. Environment variables (`AUTOMIC_*`)
3. `.env` file
4. `aromic.yaml` in the working directory
5. Built-in defaults

Key settings:

```yaml
# aromic.yaml
auto_create_folders: true
retry_base_delay_ms: 500
retry_max_backoff_s: 30
retry_statuses: [429, 503]
update_method: POST       # POST (v21 default) or PUT (legacy)
auth_method: basic        # basic (v21) or bearer (24.2+, stub only)
```

## Commands

```
aromic validate <path>           schema + dependency check
aromic plan <path>               diff against live AE (read-only)
aromic apply <path>              two-pass upsert (creates then wires refs)
aromic export -o <dir>           pull AE objects → YAML
aromic destroy <path> --confirm  reverse-dependency delete (v21: records only)
aromic auth check                probe credentials against configured URL
```

Global flags: `--log-level`, `--log-format`, `--output` (text|json), `--max-workers`.

Exit codes:
- `0` — success
- `1` — fatal (bad manifests, auth, transport)
- `2` — partial (some items failed or were refused)

## Limitations

- **DELETE not supported in AE REST v21**: `destroy` records
  `NotSupportedDelete` entries rather than calling the API. Use the
  Repository API or the Java CLI for real deletes until a future AE
  version exposes DELETE.
- **Bearer auth (Automic 24.2+)** is stubbed; current code uses HTTP
  Basic per swagger v21.
- **Calendar / Schedule shapes** are implemented best-effort from
  swagger; real fixtures couldn't be captured without a live instance.
  See `docs/ISSUES.md` for the full list of live-verification items.

## Development

```bash
uv sync
uv run pytest                   # 416 tests, 88% coverage
uv run ruff check src tests
uv run mypy --strict src
```

Project layout:

```
src/op_aromic/
├── cli/           Typer app, output formatting, prompts, progress
├── client/        httpx + Basic auth + v21 envelope unwrap
├── config/        pydantic-settings (env + .env + aromic.yaml + CLI)
├── engine/        loader, validator, differ, planner, applier, destroyer, exporter
├── models/        per-kind Pydantic manifest models
└── observability/ structlog with secret redaction
```

See `docs/IMPLEMENTATION_PLAN.md` for the original roadmap and
`docs/ISSUES.md` for every known open question / assumption.
