# op-aromic

GitOps CLI for Broadcom Automic Workload Automation.

## Usage

```bash
aromic validate .          # Validate YAML manifests
aromic plan .              # Show what would change (read-only)
aromic apply .             # Apply changes to Automic
aromic export -o ./out     # Export objects from Automic to YAML
aromic destroy . --confirm # Remove managed objects
```

## Configuration

Set via environment variables or `.env` file:

```bash
AUTOMIC_URL=http://automic:8080/ae/api/v1
AUTOMIC_CLIENT_ID=100
AUTOMIC_USER=USER/DEPT
AUTOMIC_DEPARTMENT=DEPT
AUTOMIC_PASSWORD=secret
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src/
uv run mypy src/
```
