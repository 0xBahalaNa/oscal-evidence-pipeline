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
