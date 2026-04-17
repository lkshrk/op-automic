# Real Automic REST API Fixtures

Source: [AE REST API v1 Swagger](https://docs.automic.com/documentation/webhelp/english/ALL/components/DOCU/21/REST%20API/Automation.Engine/swagger.yaml)

Verbatim examples from the official Broadcom Automic swagger.yaml (version 21). Use for normalizer/serializer round-trip tests against known-good shapes.

## Available fixtures

| File | Kind | Swagger example ref |
|---|---|---|
| `workflow_JOBP.json` | JOBP (standard workflow) | `standard_workflowOut` |
| `unix_job_JOBS.json` | JOBS (Unix job) | `unix_jobOut` |
| `static_variable_VARA.json` | VARA (static variable) | `static_variableOut` |

Missing (swagger did not include examples): JSCH (schedule), CALE (calendar), JOBP file-transfer.

## Response envelope shape

```json
{
  "total": 1,
  "data": {
    "<kind_lowercase>": {
      "metadata": {"version": "21.0.0"},
      "general_attributes": { "name", "type", "queue", ... },
      "<kind>_attributes": { ... },
      ...kind-specific sections
    }
  },
  "path": "FOLDER/SUBFOLDER",
  "client": 100,
  "hasmore": false
}
```

Kind → data key (lowercase):
- Workflow → `jobp`
- Job → `jobs`
- File transfer → `jobf`
- Script → `scri`
- Variable → `vara`
- Schedule → `jsch` (unverified)
- Calendar → `cale` (unverified)

## Import request body shape

`POST /{client_id}/objects?overwrite_existing_objects=true`

```json
{
  "path": "FOLDER/SUBFOLDER",
  "data": {
    "<kind_lowercase>": { ...same shape as export... }
  }
}
```

## Implications for op-aromic

The synthetic fixtures under `tests/fixtures/automic/*.json` (non-`real/`) use a simplified flat shape that **does not match the real response envelope**. Phase 2 normalizer/serializer were written against synthetic shapes and will need a rewrite — see `docs/ISSUES.md` entry "Response envelope shape divergence".
