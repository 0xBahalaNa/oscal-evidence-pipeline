# OSCAL Evidence Pipeline

A Python pipeline that transforms compliance findings from existing audit tools (`s3-audit`, `sg-audit`, `cloudtrail-audit`, `secret-scanner`, `evidence-logger`) into **OSCAL Assessment Results (SAR)** JSON — the machine-readable evidence format required by FedRAMP 20x and increasingly expected by federal assessors reviewing FedRAMP High and CJIS v6.0 authorization packages.

Built on **IBM Compliance Trestle** (orchestration / CLI workflow) and **oscal-pydantic** (typed transformation of audit-tool JSON into OSCAL models).

> **Status:** Phase 1 in development. v1.0 ships SAR generation from existing portfolio audit tools. POA&M (v1.1) and Component Definitions (v1.2) follow.

## Why This Exists

The compliance audit world is moving from Word/PDF evidence to **machine-readable evidence**. FedRAMP 20x makes OSCAL the canonical format. Once your audit tools emit OSCAL Assessment Results instead of plaintext logs, an assessor — or, more importantly, a continuous-monitoring pipeline — can consume them without manual transcription.

This repo is the **transformation layer** between your operational audit tools and the OSCAL ecosystem. Without it, every audit tool produces a different JSON shape that has to be hand-mapped to an SAR entry. With it, the workflow is:

```
audit tool runs → emits structured JSON → pipeline transforms → OSCAL SAR JSON → assessor / dashboard / Trestle assemble
```

## Compliance Controls Addressed

This pipeline is a meta-tool: it doesn't satisfy access controls directly. It satisfies the **assessment, monitoring, and audit-record-generation** controls that govern *how compliance evidence is produced and preserved*.

| NIST 800-53 Rev 5 | FedRAMP High | CJIS v6.0 | How This Pipeline Validates |
|--------------------|:------------:|:---------:|-------------------|
| CA-2 Control Assessments | Yes | — | Produces the OSCAL SAR artifact that documents each assessment cycle |
| CA-7 Continuous Monitoring | Yes | — | Runs per audit-tool execution; produces a timestamped SAR for each cycle |
| AU-3 Content of Audit Records | Yes | — | Preserves timestamp, source tool, finding type, mapped control IDs in every SAR observation |
| AU-12 Audit Record Generation | Yes | — | Wraps audit-tool outputs into a generated, schema-validated record |
| SI-4 System Monitoring | Yes | — | SAR output feeds continuous monitoring dashboards and KSI metric pipelines |
| CM-3 Configuration Change Control | Yes | — | Every pipeline run produces a versioned, immutable evidence artifact (timestamped filename, deterministic content) |
| CA-2, CA-7, AU-12 | Yes | 1-year retention, weekly review | SAR JSON is the artifact retained for the CJIS AU-6 weekly review |

## How an Auditor Uses This Output

An assessor reviewing a FedRAMP High or CJIS v6.0 authorization package can consume the SAR JSON directly without manual transcription. Each SAR `observation` maps one-to-one to an NIST 800-53A assessment objective — for example, an `s3-audit` finding of "BucketX failed encryption check" becomes an OSCAL observation with `relevant-evidence` pointing to the source tool, `subjects` referencing the bucket, and `props` carrying the mapped control IDs (`sc-28`, `sc-28.1`). The assessor's adequacy determination (satisfied / other-than-satisfied) is captured as the OSCAL `finding` object.

Combined with `evidence-logger` for retention and `aws-config-compliance-monitor` for continuous detection, this completes the FedRAMP 20x evidence loop: **detect → transform → retain → review**.

## FedRAMP 20x Alignment

FedRAMP 20x (Pilot launched March 2025, High pilot FY26 Q4) restructures the program around five pillars: compliance-as-code, machine-readable evidence, continuous monitoring, API-driven evidence, and automated scanning. This pipeline targets the **machine-readable evidence** pillar directly:

- **OSCAL output, not Word/PDF**: Every SAR is a JSON document validated against the NIST OSCAL schema. No manual transcription, no version drift between the spreadsheet and the system.
- **Continuous evidence generation**: Each pipeline run emits a timestamped SAR. A FedRAMP 20x reviewer comparing two SARs from different dates can read the delta directly — a KSI metric in flight.
- **API-driven**: The pipeline is a library + CLI. It can be invoked from CI/CD, from a scheduled job, or from an evidence orchestrator (e.g., on every CloudTrail event indicating an audit-tool re-run).
- **30-day vs 90-day review window**: FedRAMP 20x machine-readable packages get a 30-day review SLA versus 90 days for traditional packages. The SAR output is the unit of input to that 30-day review.

## CJIS v6.0 Relevance

CJIS Security Policy v6.0 became the audit standard on April 1, 2026 and aligns with NIST 800-53 Rev 5 as of December 2024. The most material delta this pipeline supports is **AU-6**: agencies handling CJI must retain audit records for **1 year** and conduct **weekly review** of those records. The SAR JSON produced by this pipeline is the artifact retained for that 1-year window and the input to that weekly review — directly readable by a reviewer without going back to the raw CloudTrail / IAM policy / S3 audit output.

For public-safety SaaS environments (FedRAMP High + CJIS), the same SAR feeds both review tracks. Producing two separate sets of evidence is unnecessary when both frameworks now reference the same control catalog.

## OSCAL Background (Topic Primer)

OSCAL (Open Security Controls Assessment Language) is **a data format, not a framework**. NIST defines seven OSCAL models that together represent the full compliance lifecycle:

| OSCAL Model | What It Represents | Produced By |
|-------------|-------------------|-------------|
| Catalog | The control inventory itself (e.g., NIST 800-53 Rev 5) | NIST publishes; you consume |
| Profile | A selection / tailoring of a catalog (e.g., FedRAMP High baseline) | FedRAMP PMO publishes; you consume |
| Component Definition | What a specific tool, service, or component implements | You author per tool / per AWS service |
| System Security Plan (SSP) | Full system documentation | You author |
| Assessment Plan (SAP) | What the assessor will check, and how | Assessor or you (for self-assessment) |
| **Assessment Results (SAR)** | What was found during the assessment | **This pipeline** |
| Plan of Action and Milestones (POA&M) | Open findings and remediation plan | This pipeline (v1.1) |

This repo's Phase 1 produces SAR. Phase 2 adds POA&M derivation from FAIL findings. Phase 3 adds Component Definition generation from each portfolio audit tool's capability set.

See `ARCHITECTURE.md` for the full pipeline design, library rationale (Trestle + oscal-pydantic), and integration map for each upstream audit tool.

## Requirements

- Python 3.11+
- [`oscal-pydantic`](https://github.com/RS-Credentive/oscal-pydantic) — typed OSCAL models
- [`compliance-trestle`](https://github.com/IBM/compliance-trestle) — OSCAL workflow CLI + assemble/split
- Source audit tools (any subset): `s3-audit`, `sg-audit`, `cloudtrail-audit`, `secret-scanner`, `evidence-logger`

Detailed `requirements.txt` lands with the first implementation issue.

## Development Setup

The package scaffold (`oscal_pipeline/`, `tests/`, `examples/`, pinned `requirements.txt`, `pyproject.toml`) lands in Issue #1. To work on the pipeline locally:

```bash
# Clone and enter the repo
git clone https://github.com/0xBahalaNa/oscal-evidence-pipeline.git
cd oscal-evidence-pipeline

# Create an isolated virtual environment (Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate

# Install the package in editable mode with dev extras (pytest + coverage)
pip install -e ".[dev]"

# Smoke-test that the package imports and exposes a version
python -c "import oscal_pipeline; print(oscal_pipeline.__version__)"

# Run the test suite
pytest
```

The pinned dependency tree lives in `requirements.txt` — it's the **CM-3 artifact** for this repo: the exact versions used to produce any given OSCAL SAR, recorded once and version-controlled. The looser compatible-release pins in `pyproject.toml` define the contract for downstream installers; the two files together separate "what the package needs" from "what we shipped against."

## Usage

> Phase 1 MVP. CLI surface subject to change before v1.0 tag.

```bash
oscal-pipeline run \
  --input-dir ./audit-outputs/ \
  --output ./evidence/assessment-results-$(date +%Y-%m-%d).json \
  --profile fedramp-high
```

The pipeline reads each `*.json` file in `--input-dir`, identifies the source tool by schema fingerprint, transforms each finding into an OSCAL `observation` + `finding`, assembles the full SAR via Trestle, and emits a schema-validated `assessment-results.json`.

## Sample Evidence Output

See `examples/sample-assessment-results.json` (lands with the first implementation PR). The shape will follow the OSCAL Assessment Results model: `metadata` block (title, version, last-modified, parties), `import-ap` reference to the assessment plan, `results[]` array with one entry per assessment cycle, and per-result `observations[]` + `findings[]` arrays.

## Future Enhancements

- POA&M generation from FAIL findings (v1.1)
- Component Definition generation per source audit tool (v1.2)
- AI evidence module — emit AI-specific evidence (model lineage, training data audit logs, bias testing results) as OSCAL observations; connects this pipeline to the AI portfolio layer (Project 10 AI Risk Assessment, Project 12 AI Controls Mappings) (v1.3)
- SSP skeleton generation from a Profile + Component Definition set (v2.0)
- KSI metric extraction from cross-run SAR diffs (v2.0)
- S3 archival of SAR JSON with Object Lock for CJIS AU-6 1-year retention
- CI/CD integration: SAR-on-every-PR for any source audit tool

## Framework Reference

Control family mappings and AWS implementation details are documented in [nist-800-53-rev-5-to-aws-mapping](https://github.com/0xBahalaNa/nist-800-53-rev-5-to-aws-mapping).

OSCAL specifications: [pages.nist.gov/OSCAL](https://pages.nist.gov/OSCAL/)

FedRAMP 20x program documentation: [fedramp.gov](https://www.fedramp.gov)

## License

MIT
