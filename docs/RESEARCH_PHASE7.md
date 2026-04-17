# Phase 7 Research Findings: Nested v21 Shapes

Research conducted 2026-04-17. Sources cited below each section.

---

## 1. Calendar (CALE) Shape

**Verdict: Unverifiable from public sources. Best-effort from swagger patterns.**

The Broadcom Automic AE REST v21 swagger.yaml (`docs.automic.com/documentation/webhelp/english/ALL/components/DOCU/21/REST%20API/Automation.Engine/swagger.yaml`) references `InImportObjectBody` for POST /objects but the component schema was truncated in the available excerpt. CALE-specific JSON examples do not appear in any public swagger example set.

From swagger pattern analysis and the confirmed real shapes for JOBP/JOBS/VARA, the most likely structure is:

```json
{
  "metadata": {"version": "21.0.0"},
  "general_attributes": {
    "name": "WORK.DAYS",
    "type": "CALE",
    "minimum_ae_version": "11.2"
  },
  "calendar_definitions": [
    {
      "keyword": "WEEKDAY",
      "type": "WEEKDAY",
      "entries": ["2025-01-06", "2025-01-07"]
    }
  ]
}
```

**Confidence: LOW. Needs live verification.**
Logged to ISSUES.md: "CALE nested shape unverified — needs live capture".

Sources:
- https://docs.automic.com/documentation/webhelp/english/ALL/components/DOCU/21/REST%20API/Automation.Engine/swagger.yaml
- https://community.broadcom.com/discussion/ae-rest-api-v21-documentation
- https://gist.github.com/muracz/97a41978a44871da49e63e5c4fe4b6b4

---

## 2. Schedule (JSCH) Shape

**Verdict: Unverifiable from public sources. Best-effort from swagger patterns.**

No public JSCH example found in the v21 swagger or community samples. The REST API community gist (muracz) documents v21 API changes (POST /objects now requires `application/json`, added `client` field to response) but does not include JSCH schema.

From pattern analysis (consistent with JOBP structure), the most likely shape is:

```json
{
  "metadata": {"version": "21.0.0"},
  "general_attributes": {
    "name": "ETL.NIGHTLY",
    "type": "JSCH",
    "minimum_ae_version": "11.2"
  },
  "schedule_attributes": {
    "calendar": "WORK.DAYS"
  },
  "schedule_definitions": [
    {
      "object_name": "ETL.DAILY",
      "object_type": "JOBP",
      "start_time": "020000",
      "calendar_keyword": "WEEKDAY"
    }
  ]
}
```

**Confidence: LOW. Needs live verification.**
Logged to ISSUES.md: "JSCH nested shape unverified — needs live capture".

Sources:
- https://community.broadcom.com/discussion/ae-rest-api-v21-documentation
- https://gist.github.com/muracz/97a41978a44871da49e63e5c4fe4b6b4

---

## 3. VARA column_count Semantics

**Verdict: Verified from real fixture + Automic documentation.**

The real fixture `tests/fixtures/automic/real/static_variable_VARA.json` confirms:
- `variable_definitions.column_count: "5"` for STATIC type
- `static_values` rows have exactly `value1` through `value5` keys
- Each row also has `key`, `client`, and `validity_range`

Automic documentation (docs.automic.com AWA guides) states that Static VARA objects have one key column plus a fixed set of value columns. The scripting interface references column numbers as optional positional args. The observed fixture caps at 5 columns for STATIC type.

**Conclusion:**
- STATIC VARA: column_count is an integer (1–5) stored as string, defaults to "5". `value1` through `value{column_count}` keys exist per row. Values beyond column_count are omitted.
- DYNAMIC/SQL/EXEC VARA: column_count is determined by the data source query. Not fixed at 5.
- op-aromic models `entries` as `key + List[str]` of up to `column_count` values.

Sources:
- `tests/fixtures/automic/real/static_variable_VARA.json` (confirmed verbatim swagger example)
- https://docs.automic.com/documentation/webhelp/english/ASO/11.2/AE/11.2/All%20Guides/Content/ucacta.htm
- https://docs.automic.com/documentation/webhelp/english/AWA/11.2/AE/11.2/All%20Guides/Content/ucaafg.htm

---

## 4. Workflow Task Reference Resolution

**Verdict: Verified from Automic naming conventions documentation.**

Automic naming conventions documentation (v21.0.4, v24.4) state:
> "In the Automation Engine, authorizations can only be controlled via object names and object types; they cannot be controlled via user-defined folder structures."

This confirms that **object names are globally unique per client** — the folder tree is a UI/organisational layer, not a namespace. Two objects of the same type cannot share a name within a client regardless of folder placement.

Therefore `workflow_definitions[].object_name` is a client-scoped global reference. No folder qualifier is needed. op-aromic's `ObjectRef` model (name + kind) is correct. The `folder` field on `ObjectRef` is optional and unused for resolution.

Sources:
- https://docs.automic.com/documentation/WEBHELP/English/all/components/DOCU/21.0.4/Automic%20Automation%20Guides/Content/AWA/Objects/BestPractices/BP_ConsistentNamingConventionsObjects.htm
- https://docs.automic.com/documentation/WEBHELP/English/all/components/DOCU/24.4.0/Automic%20Automation%20Guides/Content/AWA/Objects/BestPractices/BP_ConsistentNamingConventionsObjects.htm

---

## 5. POST /objects Request Body Shape

**Verdict: Partially verified. Body mirrors GET response envelope.**

The swagger v21 `POST /{client_id}/objects?overwrite_existing_objects=true` uses `InImportObjectBody` schema. The gist documents that v21 added `application/json` content-type requirement and a 200 OK response for successful import.

From the fixture README (confirmed from real captures):
```json
{
  "path": "FOLDER/SUBFOLDER",
  "data": {
    "<kind_lowercase>": { ...same nested shape as GET response inner object... }
  }
}
```

The POST body is **the same envelope structure as the GET response** (`path` + `data` dict keyed by kind lowercase), not a flat payload. The inner object under `data.<key>` contains `metadata`, `general_attributes`, and kind-specific attribute blocks.

**Implication for op-aromic serializer:** `manifest_to_automic_payload` must produce:
```json
{
  "path": "<folder>",
  "data": {
    "<kind_lower>": {
      "metadata": {"version": "21.0.0"},
      "general_attributes": {"name": "...", "type": "...", ...},
      "<kind>_attributes": {...},
      ...kind-specific arrays...
    }
  }
}
```

This is a breaking change from the current flat PascalCase shape. **Serializer rewrite is out of Phase 7 scope** (minimal blast radius rule) — logged to ISSUES.md for Phase 8.

Sources:
- `tests/fixtures/automic/real/README.md` (confirms import body shape)
- https://gist.github.com/muracz/97a41978a44871da49e63e5c4fe4b6b4
- https://docs.automic.com/documentation/webhelp/english/ALL/components/DOCU/21/REST%20API/Automation.Engine/swagger.yaml
