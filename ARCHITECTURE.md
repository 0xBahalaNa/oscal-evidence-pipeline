# Architecture — OSCAL Evidence Pipeline

This document defines the pipeline's stages, data contracts, library choices, and integration points with the existing portfolio audit tools. It's intentionally opinionated: the choices here are the ones an interviewer should be able to push on and the author should be able to defend.

## 1. Purpose

The pipeline solves a single problem: **the JSON shapes produced by individual audit tools are not the JSON shape an OSCAL-consuming system expects.** Without a transformation layer, every audit tool would need to be modified to emit OSCAL Assessment Results directly — which couples the tools to the OSCAL schema version and makes each tool harder to maintain.

By keeping audit tools focused on detection (their own native JSON) and centralizing OSCAL transformation in this pipeline, the system gains three properties:

1. **Audit tools stay simple.** They emit findings in whatever JSON shape makes sense for their domain.
2. **OSCAL schema upgrades happen in one place.** When the OSCAL spec moves from 1.1.x → 1.2.x, only this repo updates.
3. **The pipeline is the FedRAMP 20x integration boundary.** Continuous monitoring dashboards, KSI metric collectors, and Trestle workflows talk to *this* repo's output, not to N audit tools.

## 2. Pipeline Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                    Upstream — Source Audit Tools                    │
│  ┌───────────┐  ┌─────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ s3-audit  │  │ sg-audit│  │cloudtrail-   │  │secret-scanner│    │
│  │           │  │         │  │  audit       │  │              │    │
│  └─────┬─────┘  └────┬────┘  └──────┬───────┘  └──────┬───────┘    │
│        │             │              │                  │            │
│        └─────────────┴──────┬───────┴──────────────────┘            │
│                             │ native JSON findings                  │
└─────────────────────────────┼──────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                     OSCAL Evidence Pipeline                         │
│                                                                     │
│  ┌─────────────┐   ┌───────────────┐   ┌──────────────────────┐    │
│  │ Stage 2     │   │ Stage 3       │   │ Stage 4              │    │
│  │ Ingestion   │──▶│ Transformation│──▶│ SAR Assembly         │    │
│  │ (read JSON, │   │ (per-tool     │   │ (Trestle assemble +  │    │
│  │  detect     │   │  adapters →   │   │  schema validate)    │    │
│  │  schema)    │   │  oscal-       │   │                      │    │
│  │             │   │  pydantic     │   │                      │    │
│  │             │   │  models)      │   │                      │    │
│  └─────────────┘   └───────────────┘   └──────────┬───────────┘    │
│                                                    │                │
└────────────────────────────────────────────────────┼────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────┐
│                  Stage 5 — Output & Downstream                      │
│                                                                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐    │
│  │ assessment-results-  │  │ evidence-logger (retention)       │    │
│  │ YYYY-MM-DD.json      │  │ aws-config-compliance-monitor     │    │
│  │ (validated SAR)      │  │ compliance-report (dashboard)     │    │
│  └──────────────────────┘  └──────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
```

**Stage 1** (source audit-tool execution) is upstream and out of scope for this repo. Stages 2–5 are this repo.

## 3. Stage Breakdown

### Stage 2 — Ingestion

**Responsibility:** Read every `*.json` file in the input directory, detect which upstream tool produced it (by structural fingerprint), and route it to the matching adapter.

**Detection strategy:** Each upstream tool's JSON has a stable top-level structure. For example, `secret-scanner --output json` produces `{"scan_metadata": {...}, "findings": [...], "summary": {...}}`. Schema fingerprinting matches on the top-level key set rather than requiring upstream tools to add a `source_tool` field — keeps the upstream tools loose-coupled.

**Failure modes:**

- **Unknown schema (no adapter claims the input):** log warning, skip file, continue. The pipeline never fails the whole run on an unrecognized input.
- **Ambiguous schema (two or more adapters claim the input):** raise `MultipleAdaptersMatch` and halt the run. Catching and resuming drops every later file when the generator finalizes — silent incompleteness is the late-stage CJIS AU-6 / FedRAMP 20x audit failure we avoid by failing loud at ingest.
- **Adapter `matches()` raises:** raise `AdapterMatchError` wrapping the original exception with the offending adapter's registry key and class name. A broken adapter must not silently mask a correct one downstream — that's the same silent-ambiguity failure mode the multi-match policy guards against — and **halt the run** (same scope as the multi-match case).
- **Malformed JSON / unreadable file:** log ERROR, skip file, continue.
- **Top-level JSON not an object (list / scalar):** log WARNING, skip file, continue.
- **Missing or non-directory `input_dir`:** raise before iteration begins (`FileNotFoundError` / `NotADirectoryError`).

### Stage 3 — Transformation

**Responsibility:** Convert each finding from native JSON into an OSCAL `observation` + `finding` object, using `oscal-pydantic` models for type safety.

**Per-tool adapter pattern:** One adapter module per upstream tool (`adapters/secret_scanner.py`, `adapters/s3_audit.py`, etc.). Each adapter implements a common interface:

```python
@runtime_checkable
class Adapter(Protocol):
    def matches(self, raw: dict[str, object]) -> bool: ...
    def transform(self, raw: dict[str, object]) -> list[Observation]: ...
```

`dict[str, object]` (not `dict[str, Any]`) is deliberate: it forces adapter authors to narrow with `isinstance` before indexing into `raw`, so upstream-schema drift surfaces as a clear type-check error rather than as silently-wrong observations. `@runtime_checkable` enables `isinstance(obj, Adapter)` for registry-time sanity checks — the runtime check is shallow (name-existence only), so signature-level enforcement falls to mypy at static-check time.

This Protocol-based design keeps adding a new upstream tool to a single file under `adapters/`. It also makes the pipeline testable per-adapter without spinning up Trestle.

**Mapping rules** (per OSCAL Assessment Results spec):
- One source-tool *run* becomes one OSCAL `result` entry.
- Each finding within a run becomes one OSCAL `observation`.
- Each FAIL/WARN observation also produces an OSCAL `finding` with `target.status.state` of `other-than-satisfied`.
- PASS observations are still recorded as observations (positive evidence) but don't produce a `finding`.
- Control IDs from the upstream tool's `control_ids` array become OSCAL `props` and `related-observations` on the corresponding finding.

### Stage 4 — SAR Assembly

**Responsibility:** Combine all transformed observations + findings into a single OSCAL Assessment Results document, validate against the OSCAL schema, and emit JSON.

**Why Trestle here:** Trestle's `trestle assemble` workflow already handles SAR document assembly with proper `metadata` blocks, `back-matter` resources, and schema validation. Re-implementing assembly with oscal-pydantic alone is possible but reinvents Trestle's wheel.

**Metadata block** (per OSCAL spec):
- `title` — set by pipeline (`"Evidence Pipeline Run YYYY-MM-DD"`)
- `last-modified` — pipeline run timestamp (ISO 8601)
- `version` — pipeline version
- `oscal-version` — current pinned OSCAL version (1.1.x at time of writing)
- `parties[]` — at minimum, the assessment-conducting party (the operator running the pipeline)

### Stage 5 — Output

**Responsibility:** Write the validated SAR JSON to a timestamped filename under `evidence/`. Optionally archive to S3 with Object Lock for CJIS AU-6 retention (v1.1+).

**Filename convention:** `evidence/assessment-results-YYYY-MM-DD-HHMMSS.json`. Timestamp prevents overwrite (AU-9 Protection of Audit Information). Aligns with the `evidence-logger` convention so a future unified runner can place both side-by-side.

## 4. OSCAL Data Model — Why SAR First

OSCAL defines seven models. They form a lifecycle, not an inheritance hierarchy:

```
Catalog        Profile          Component         SSP        SAP        SAR        POA&M
(NIST publishes)(FedRAMP        Definition       (system    (assessor  (this      (open
                publishes)      (per-tool /       authors)   authors)   pipeline)  findings)
                                per-service)
   │              │                  │              │           │          │           │
   ▼              ▼                  ▼              ▼           ▼          ▼           ▼
  controls     selection         what tool X     full sys    plan to    actual      remediation
  exist        + tailoring       implements      docs        check      findings    backlog
```

The pipeline produces **SAR** (Assessment Results) first because:

1. **It's the most automatable.** SAR is the only model whose content is *derived* from operational data. The other six are mostly authored by humans (or generated from spreadsheets, painfully).
2. **It's the FedRAMP 20x KSI entry point.** KSI metrics are computed from SAR observations over time. Without machine-readable SARs, KSIs don't exist.
3. **It demonstrates the highest portfolio leverage.** Every other audit tool the portfolio ships becomes more valuable when its output is OSCAL-consumable. SAR is the format that makes that true.

POA&M (v1.1) is the natural follow-on: every FAIL `finding` in a SAR is a candidate POA&M item. Component Definition (v1.2) is the more ambitious follow-on: each portfolio audit tool's capability becomes a Component Definition, which a downstream SSP author can `import-component` from.

## 5. Toolchain Rationale — Trestle + oscal-pydantic

**Three options were considered. Both was chosen. Here's why.**

### Trestle alone

IBM Compliance Trestle is the most mature OSCAL toolchain. It's used by IBM, Red Hat, and other FedRAMP CSPs for production OSCAL workflows. It ships CLI commands (`trestle assemble`, `trestle split`, `trestle import`) that handle the document-assembly choreography correctly.

Trade-off: Trestle's models are heavyweight and the transformation extension points are less ergonomic for "take this arbitrary JSON and produce an OSCAL observation." You end up shelling out to Trestle from custom Python, which loses type safety.

### oscal-pydantic alone

`oscal-pydantic` is a pure Python typed model library generated from the OSCAL JSON Schema. Every OSCAL object is a Pydantic model with type hints and validation. Transformation code looks like ordinary Python: `Observation(uuid=..., title=..., ...)`.

Trade-off: No assembly choreography. No CLI. No `trestle split` workflow for managing large SSPs by file. Re-implementing those is significant scope.

### Both (chosen)

- **oscal-pydantic** owns Stage 3 (transformation): per-tool adapters build typed OSCAL objects directly. Tests are ordinary `pytest` tests of pure Python functions.
- **Trestle** owns Stage 4 (assembly + validation): once the typed objects exist, Trestle assembles them into a valid SAR document and runs OSCAL schema validation.

This gives the pipeline the **right tool for the right stage** and avoids reinventing either project's contributions. Interview-defensible: "We use Trestle for what Trestle is best at — workflow orchestration and schema validation — and oscal-pydantic for what it's best at — typed, testable transformation code."

## 6. Upstream Integration Map

Each upstream tool's expected JSON shape and the OSCAL observation it produces:

| Source Tool | Input Shape (top-level keys) | OSCAL Observation `type` | Notes |
|-------------|-------------------------------|---------------------------|-------|
| `secret-scanner` | `scan_metadata`, `findings`, `summary` | `finding` | Already emits structured JSON via `--output json`. No changes needed. |
| `s3-audit` | (currently plaintext — needs `--json` flag) | `control-objective` | Upstream issue required: add JSON output. Tracked in `s3-audit#future-enhancements`. |
| `sg-audit` | (currently plaintext) | `control-objective` | Same — needs `--json` flag upstream. |
| `cloudtrail-audit` | (currently plaintext SUMMARY block) | `monitoring` | Same — needs JSON export (already in its Future Enhancements). |
| `evidence-logger` | structured text file | `assessment-activity` | Wraps an existing artifact rather than producing a new one. Different ingest path. |

**The pipeline is built tolerant of this state.** Adapters for `secret-scanner` ship in v1.0; adapters for the other tools land as each upstream adds its `--output json` (or equivalent) flag. The pipeline is the *forcing function* for normalizing upstream output across the portfolio.

## 7. FedRAMP 20x KSI Alignment

FedRAMP 20x organizes machine-readable evidence around **Key Security Indicators (KSIs)** — 64 KSIs across 11 thematic areas. Each KSI is a measurable property of the system, evaluated continuously rather than annually.

This pipeline supports KSI computation by ensuring **every SAR observation carries**:

- A timestamp (`collected` field on the observation)
- A unique observation UUID (stable across runs for the same finding)
- Mapped control IDs in `props`
- A clear pass/fail/warn state via the linked `finding.target.status.state`

A downstream KSI metric collector (in `aws-config-compliance-monitor` or `compliance-report`) reads two SARs from different dates, diffs the observation sets, and produces KSI values such as:

- *"Number of S3 buckets without encryption at rest" (KSI cryptographic protection)*
- *"Number of root account usages in last 30 days" (KSI privileged access)*
- *"Number of overly-permissive IAM policy statements" (KSI least privilege)*

The pipeline doesn't compute KSIs itself — that's downstream — but it produces the input format the KSI collectors need.

## 8. Phase Roadmap

| Version | Scope | Sprint Target |
|---------|-------|---------------|
| **v1.0** | SAR generation from `secret-scanner` (working end-to-end), adapter stubs for the four other source tools, validated SAR JSON output, CLI entry point, sample evidence committed under `examples/` | sprint-month-4 (June 2026) |
| **v1.1** | POA&M generation from FAIL findings, S3 archival with Object Lock for CJIS AU-6 retention | sprint-month-5 (July 2026) |
| **v1.2** | Component Definition generation per source audit tool — each tool becomes an OSCAL Component the downstream SSP can `import-component` | sprint-month-6 (Aug 2026) |
| **v1.3** | AI evidence module — emit AI-specific evidence (model lineage, training data audit logs, bias testing results) as OSCAL observations / components; bridges this pipeline to the AI portfolio layer (Project 10 AI Risk Assessment, Project 12 AI Controls Mappings) | sprint-month-7 (Sep 2026), paired with the AI portfolio sprint |
| **v2.0** | SSP skeleton generation from a Profile + Component Definition set, KSI metric extraction from cross-run SAR diffs | sprint-month-8+ |

## 9. Non-Goals (Explicit)

- **Not an SSP authoring tool.** Trestle already does this well. We produce SAR, not SSP.
- **Not a compliance dashboard.** That's `compliance-report`. We produce the evidence the dashboard reads.
- **Not a continuous monitoring agent.** That's `aws-config-compliance-monitor`. We transform the evidence the agent produces.
- **Not an enforcement tool.** Audit tools detect; this pipeline transforms. Preventive guardrails belong in `aws-compliance-as-code` (CloudFormation / SCPs).
- **Not an evidence retention store.** Filesystem write to `evidence/` is sufficient for v1.0. S3 Object Lock retention is a v1.1 enhancement, not a Phase 1 deliverable.

## 10. Decision Log

Decisions worth re-examining if requirements change:

| Decision | Rationale | Re-examine If |
|----------|-----------|---------------|
| Trestle + oscal-pydantic (not either alone) | Right tool per stage; production-realism | OSCAL spec changes break oscal-pydantic generation; Trestle starts supporting first-class custom transformation |
| Schema fingerprinting (no `source_tool` field required upstream) | Keeps upstream tools loose-coupled | Upstream tools naturally start adding `source_tool` for their own reasons (CI integration, log routing) |
| SAR-only for v1.0 (not POA&M or Component Definition) | Ship narrow, prove the pipeline, layer ambition | v1.0 ships fast and v1.1 / v1.2 work is blocked on integration feedback |
| Timestamped filename for output (not append to single file) | AU-9 protection of audit info; matches `evidence-logger` convention | Move to event-stream output (Kafka / Kinesis) becomes necessary for scale |
| Filesystem output (no S3 archival in v1.0) | Phase 1 doesn't need durable retention to prove the transform | CJIS auditor requests evidence of 1-year retention before v1.1 lands |

## 11. Validation Layers & Schema-Pinning Policy

Schema validation is the gate that lets the pipeline claim its evidence is *machine-consumable* — an assessor, eMASS, or a KSI collector can ingest a SAR without a round-trip to fix it. This section is the audit defense for that gate: what each validation layer actually checks, where the published-schema gate runs and why, and the honest limits of the regex shim that makes it run at all. It is deliberately written to be defensible under push-back — it does not oversell what the gate enforces.

### 11.1 The Three-Layer Validation Model

Validation happens in three layers. Each is a **superset** of the one before it — a SAR that passes Layer 3 has also passed Layers 1 and 2 — and each catches a class of error the prior layer *relaxes*.

| Layer | Mechanism | Runs where | Catches | Relaxes (gap the next layer closes) |
|-------|-----------|------------|---------|--------------------------------------|
| **1 — Typed import** | `oscal-pydantic` model construction | Build time (Stage 3) | Type/shape drift at construction — wrong field types, missing required model fields | `oscal-pydantic==2023.3.21` (frozen on pydantic v1) **comments out** several constraints — TZ-aware datetime regex, several token-shape regexes. It relaxes rules the NIST schema enforces. |
| **2 — Structural / model-level** | Trestle `AllValidator` chain (Catalog / Duplicates / Refs / Links / RuleParameters), via `assembler._validate_via_trestle_models` | Import/runtime (Stage 4), import-time-cheap | Duplicate UUIDs across observations/findings/parties, broken intra-document `href` refs, shape violations `oscal-pydantic` still enforces | Does **not** validate against the published NIST OSCAL JSON Schema — no required/enum/format/token-regex enforcement from the canonical spec. |
| **3 — Published NIST JSON Schema** | `jsonschema` via `oscal_pipeline.schemas.validate` | **CI / pytest + CLI emit boundary** — deliberately *not* in the assembler import path | Required-property, enum, type, and approximate token-regex constraints the canonical schema enforces and Layers 1–2 relax | (Format caveat — see §11.2.) |

**Why Layer 3 stays out of the assembler import path.** Keeping the published-schema validator out of anything `import oscal_pipeline` pulls in keeps the library **import-time-cheap**: importing `oscal_pipeline` to build a SAR does not load `jsonschema`, compile the schema, or pay validation cost. Layer 3 still runs at **emit time** — `oscal-pipeline run` calls `validate_against_vendored_schema` after assemble and before write (fail-closed) — and in **CI** via `tests/test_schema_validation.py` inside the existing `.github/workflows/test.yaml` step (`pytest --cov-fail-under=80`). The reusable module in `oscal_pipeline/schemas/validate.py` replaced the earlier smell where production comments pointed at a test file as the gate. The assembler does not import it; the CLI and tests do.

The SAR the gate validates is **byte-deterministic**: built from the `secret_scanner_mixed.json` fixture, transformed by `SecretScannerAdapter`, assembled with a pinned `RunMetadata` (`run_timestamp=datetime(2026, 6, 5, 12, 0, 0, tzinfo=utc)`). Determinism is the property that makes cross-run SAR diffing (CM-3) work; validating a fixed artifact also means a gate failure is a real regression, never fixture noise.

### 11.2 The ECMA → Python Regex Shim (and its Fidelity Caveat)

OSCAL is published as JSON Schema Draft-7, whose `pattern` keyword is specified against **ECMA-262** regex. OSCAL's `token` datatype uses ECMA Unicode-property classes — `\p{L}`, `\p{N}` — that **Python's `re` cannot compile**: a stock `Draft7Validator` raises `re.PatternError: bad escape \p` the moment it hits a token-patterned field. Without a shim there is no Python-side schema gate at all.

`_oscal_pattern_check` (in `oscal_pipeline/schemas/validate.py`) is a `pattern`-keyword replacement registered via `jsonschema.validators.extend(Draft7Validator, {"pattern": _oscal_pattern_check})`. It translates the two ECMA classes, then compiles and searches with Python `re`:

- `\p{L}` → `[^\W\d_]`
- `\p{N}` → `\d`

This is an **approximation, not a faithful port**, and the audit-honest accounting matters more than the cleverness:

- **`\p{L}` → `[^\W\d_]` is faithful.** In Python's default Unicode mode for `str`, "a word char that is neither a digit nor an underscore" is exactly the set of Unicode letters (including non-ASCII letters). No loss.
- **`\p{N}` → `\d` *narrows*.** `\p{N}` is *all* Unicode Number — `Nd` (decimal), `Nl` (letter-number, e.g. Roman numerals), and `No` (other-number, e.g. superscripts/fractions). Python `\d` in Unicode mode matches **`Nd` only**. The shim is therefore **stricter than the schema** here: it could reject a technically-valid `\p{N}` character (an `Nl`/`No`). This is harmless in practice — emitted OSCAL tokens are ASCII — and it is **documented, not silently wrong**: the gate errs toward rejection, which fails loud rather than passing bad evidence.
- **Untranslatable patterns are silently skipped.** Any pattern that still fails `re.compile` after translation is caught (`except re.error: return`) and **not checked**. This is a deliberate soft-spot: a future un-translatable schema pattern degrades to "not checked" rather than crashing the entire CI gate. It is a real fidelity trade-off and is named here so no reviewer assumes 100% pattern coverage.
- **String `format`s are not enforced; UUID *shape* still is — via `pattern`, not `format`.** The validator is built with `format_checker=jsonschema.FormatChecker()`, but format checking is opt-in on optional libraries. The schema's `format` keywords are `date-time`, `email`, `uri`, and `uri-reference` — and none fire for the SAR under test: `date-time`/`uri`/`uri-reference` require `rfc3339-validator` / `rfc3987` (**not in the lock**), and `format: email` rides only a party datatype this SAR doesn't emit. **UUID validity is *not* a `format` check at all** — the schema constrains UUIDs with an always-on `pattern` regex (`UUIDDatatype`, plain ASCII that Python `re` compiles fine), so UUID *shape* is enforced regardless of the inert `FormatChecker`. Note too that the `date-time-with-timezone` and `uri` datatypes carry their own always-on `pattern` regexes, so grossly malformed values are still rejected by `pattern`; only the RFC *format* semantics layered on top go unchecked (and `uri-reference`, being format-only, is fully unchecked).

**Net honest claim.** The Layer 3 gate enforces JSON **structure, required properties, enums, types, UUID shape (via `pattern`), and approximate token-regex**. It does **not** enforce `date-time` / `uri` **format** semantics, and its token-regex is approximate (narrowed `\p{N}`, soft-skipped untranslatables). It is a **"structural + approximate-token-regex" gate, not "full format/regex enforcement."** Closing the format gap is a matter of adding `rfc3339-validator` + `rfc3987` to the lock — tracked, not done in this PR.

### 11.3 Vendored, SHA-Pinned Schema as a CM-3 Artifact

The schema is **vendored**, not fetched at validate time:

- **File:** `oscal_pipeline/schemas/oscal_assessment-results_schema-1.2.1.json` (149 KB), downloaded **verbatim** from NIST (`csrc.nist.gov`), `$id = "http://csrc.nist.gov/ns/oscal/1.2.1/oscal-ar-schema.json"`, JSON Schema Draft-7. **Never hand-edited.**
- **SHA256:** `4f9e277a177adbcca9527612ce450a33dc6096773fa229d413d801d196c61985`, pinned in the test as `_SCHEMA_SHA256`; `test_vendored_schema_integrity` asserts the on-disk file still digests to this value.
- **Zero remote `$ref`s.** Every `$ref` is an internal fragment (`#/definitions/...`). There is **no network fetch and no SSRF surface** at validate time — the gate is **offline-deterministic**.
- **Packaged in the wheel.** Shipped via `[tool.setuptools.package-data]` (`oscal_pipeline = ["py.typed", "schemas/*.json"]`); `oscal_pipeline/schemas/__init__.py` makes it an importable subpackage; the file is read through `importlib.resources.files("oscal_pipeline.schemas")` rather than a filesystem path, so it resolves identically from a source tree or an installed wheel.

Vendoring + SHA-pin + offline-determinism is exactly the **CM-3** posture: an immutable, version-controlled validation artifact whose bytes are auditable and whose behavior cannot drift because NIST republishes or a network is unavailable.

The schema is **version-coupled to the toolchain, not hardcoded**: `OSCAL_VERSION` is imported dynamically (`from trestle.oscal import OSCAL_VERSION`, currently `"1.2.1"`), and the test derives the schema **filename** from it (`f"oscal_assessment-results_schema-{OSCAL_VERSION}.json"`). The version the pipeline emits and the version it validates against are the same value by construction.

### 11.4 Schema-Pinning / `OSCAL_VERSION` Bump Checklist (AC#6)

When `compliance-trestle` bumps its bundled OSCAL version, `OSCAL_VERSION` moves with it and the vendored schema must follow. The bump is a two-step manual action with two tripwire tests that fail CI if it is forgotten or done wrong:

- [ ] **Re-vendor** the matching `oscal_assessment-results_schema-<new>.json` verbatim from NIST `csrc.nist.gov` into `oscal_pipeline/schemas/`.
- [ ] **Update `_SCHEMA_SHA256`** in the test to the new file's digest.

Tripwires that catch a forgotten or incorrect bump:

- **`test_schema_version_matches_oscal_version`** asserts `OSCAL_VERSION in schema["$id"]` — a stale vendored file (right filename, wrong contents) fails here.
- **Filename derivation** — the schema filename is built from `OSCAL_VERSION`, so bumping the version *without* re-vendoring raises `FileNotFoundError` in `_load_vendored_schema`.
- **`test_vendored_schema_integrity`** asserts the SHA256 — a re-vendor with a stale or wrong `_SCHEMA_SHA256` fails here.

A forgotten bump cannot pass silently: either the file is missing (`FileNotFoundError`), the `$id` mismatches, or the digest mismatches.

### 11.5 Vacuous-Pass Safeguards

A schema gate is only worth its CI minutes if it can actually go red. Two tests prove this one is not green-no-matter-what:

- **Negative control on a required property.** The test deletes `metadata.oscal-version` (which **is** in the schema's `required` list) from a copy of the emitted SAR and asserts `validate_against_vendored_schema` raises `SchemaValidationError`. This proves the validator genuinely enforces `required`, rather than passing any well-formed JSON.
- **Shim-deletion → loud crash.** The emitted SAR exercises a `\p{}`-patterned token field (a control-id `name` token). A stock `Draft7Validator` with **no** shim would raise `re.PatternError` inside `iter_errors` — so deleting the shim makes the test **crash (CI red)**, not pass silently. The shim's correctness is self-guarding: remove it and the gate fails *loud*.

Both safeguards encode the same repo-wide principle the ingestion registry and severity classifier follow — **fail loud where silence would corrupt the evidence**.

### 11.6 Dependencies & Lock Completeness

The gate adds one direct dependency, pinned across the repo's three-layer dependency model:

- `pyproject.toml` — `jsonschema~=4.23` (compatible-release **contract**).
- `requirements.txt` — `jsonschema>=4.0` (direct **intent**). *Known nit:* this floor is looser than the `pyproject` `~=4.23` contract and looser than this file's own `==`/floor-plus-ceiling convention; tighten to `>=4.23` (or `==4.23.0`). Non-blocking — the lock pins it anyway.
- `requirements.lock` — full transitive freeze: `jsonschema==4.23.0`, `jsonschema-specifications==2025.9.1`, `referencing==0.37.0`, `rpds-py==2026.5.1` (`attrs==26.1.0`, `importlib_resources==7.1.0` already present).

The lock freeze keeps `pip install --no-deps -r requirements.lock` **complete** — under `--no-deps` a missing transitive becomes a runtime `ModuleNotFoundError`, not a resolver error. This is the same lock-completeness mechanic documented in the vault note *The --no-deps Lockfile Install Strategy*; see it for the full rationale.

### 11.7 Rejected Alternatives & AC Deviations

| Rejected | Why |
|----------|-----|
| **Download the schema at CI time** (fetch from NIST in the workflow) | Introduces a network dependency and SSRF surface into the merge gate, makes the gate non-deterministic across runs (NIST could republish), and breaks offline/air-gapped CI. Vendoring + SHA-pin gives a CM-3-auditable, offline-deterministic artifact instead. |
| **Run the schema gate in the assembler import path** | Loads `jsonschema` and compiles the 149 KB schema on every `import oscal_pipeline`, paying validation cost on every SAR build. Layer 3 belongs at the CLI emit boundary and in CI tests — not on the library import path (§11.1). |
| **Install dev extras (`pip install -e ".[dev]"`) in CI**, per Issue #20 AC#1 | Raises `ResolutionImpossible`: `compliance-trestle`'s metadata declares `pydantic[email]>=2`, while `oscal-pydantic==2023.3.21` pins `pydantic<2`. A normal resolve cannot satisfy both. CI installs `--no-deps -r requirements.lock` then `-e . --no-deps` instead — trestle imports `from pydantic.v1 import ...` at runtime, so a `pydantic` v1-line pin in the lock works even though the metadata says otherwise. |
| **A new `.github/workflows/ci.yml`**, per Issue #20 AC#1 | Redundant — the existing `test.yaml` pytest step already runs the full suite as the merge gate. Adding the schema gate *as a test* reuses that gate (one place, one cache, one required check) rather than standing up a parallel workflow. |

**AC deviations (transparent):**

- **AC#1** specified a new `ci.yml` installing the repo + dev extras. **Implementation deviates:** the gate runs inside the existing `test.yaml` pytest step, and install is `--no-deps -r requirements.lock` then `-e . --no-deps` (not dev extras). Reason: the `ResolutionImpossible` conflict above.
- **AC#4** (branch protection / required-check configuration) is **deferred to issue #33** (docs-only).

### 11.8 Control Mapping

| Framework | Control | How this gate validates it |
|-----------|---------|-----------------------------|
| NIST 800-53 Rev 5 | **CA-2** | A schema-conformant SAR is part of the assessment-evidence integrity contract — assessment results are well-formed before they count as evidence. |
| NIST 800-53 Rev 5 | **AU-12** | Audit records must be schema-conformant when emitted; the gate blocks a non-conformant SAR from being written at the CLI emit boundary (exit 1, no artifact) and blocks the canonical sample from merging in CI. |
| NIST 800-53 Rev 5 | **CM-3** | Vendored schema + SHA pin + `OSCAL_VERSION` pin = an immutable, version-controlled validation artifact; the deterministic in-process SAR makes the gate's verdict reproducible. |
| NIST 800-53 Rev 5 | **CM-6** | The gate is an automated verification check that runs at the CLI emit boundary on every emitted SAR and in CI on every pull request (and pushes to `main`). |
| FedRAMP 20x | KSI evidence pillar | A machine-readable SAR that passes the *published* schema is what an assessor / eMASS ingest consumes without a round-trip. |
| CJIS v6.0 | **AU-6** | The schema-valid SAR is the artifact retained for the 1-year retention + review requirement. |
