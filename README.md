# AFE Copilot

> An open-source AI system that drafts AFEs in 5 minutes, tracks the approval pipeline, and analyzes post-execution variance — built for operators running multi-rig workover programs.

Built by a Staff Production Engineer (ex-OXY, ex-Shell) who has written hundreds of AFEs by hand.

[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen)](https://afe-copilot.streamlit.app)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)

**Try it now → [afe-copilot.streamlit.app](https://afe-copilot.streamlit.app)**

---

## The problem

An operator running 11 workover rigs generates ~200 AFEs per year. Each AFE consumes 1–3 hours of engineer time on writing, routing, and follow-up — that's 400–600 hours per year spent on paperwork that doesn't make a single barrel of oil. AFEs that should be approved in a week sit for a month waiting on missing line items, unclear cost justification, or a reviewer asking the same question for the fifth time.

This system attacks all three bottlenecks: drafting speed, drafting quality, and pipeline visibility.

## What it does

**1. AFE Drafter agent**
Input: well diagnosis + intervention choice (e.g., "ESP swap + acid stim on well ED-001H").
Output: a polished .docx AFE with:
- Scope of work and technical justification
- Line-item cost breakdown (equipment, vendors, manpower) benchmarked against historical cost DB
- Pre-job and post-job economics (NPV @ 10%, payout, $/BOE)
- Risk register with mitigation actions
- Approval signature block routed for the operator's typical chain

**2. AFE Pipeline Tracker**
Live dashboard showing every in-flight AFE with status (draft → engineering review → finance → approved → executed), aging in days, and predicted next-bottleneck (e.g., "this AFE will sit with finance for 8 days based on historical patterns — escalate early").

**3. Variance Analyzer**
Post-execution: ingest the actual rig invoices and field tickets. Computes actual-vs-AFE variance by category. Flags systematic over-runs by vendor, rig, or intervention type so the next AFE includes a more realistic estimate.

**See a real sample:** [`examples/sample_afe_acid_stimulation.md`](examples/sample_afe_acid_stimulation.md) — a complete agent-generated AFE for a synthetic Delaware Basin acid stimulation, including scope, technical justification, cost breakdown with vendor benchmarks, NPV/payout, and a 7-line risk register (5 standard + 2 well-specific).

## Quick start

```bash
git clone https://github.com/<your-user>/afe-copilot
cd afe-copilot
pip install -e ".[docs]"
cp .env.example .env  # add ANTHROPIC_API_KEY

# Draft an AFE from a well-diagnosis JSON
python -m src.drafter --input examples/well_diagnosis_001.json --out drafts/

# Open the pipeline tracker
streamlit run demo/app.py
```

## Architecture

```
                 ┌─────────────────────────┐
                 │  Well diagnosis (JSON)  │
                 └────────────┬────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────┐
   │                  AFE Drafter agent                    │
   │  Claude reasons over:                                │
   │   • intervention scope (tools: scope_generator)      │
   │   • cost estimate (tools: cost_lookup, benchmark)    │
   │   • economics (tools: npv_payout)                    │
   │   • risk register (tools: risk_lookup)               │
   └──────────────────────────────┬───────────────────────┘
                                  ▼
                       ┌──────────────────┐
                       │  Polished .docx  │
                       └────────┬─────────┘
                                ▼
                  ┌─────────────────────────┐
                  │  Pipeline Tracker (DB)  │
                  └─────────────────────────┘
```

## Roadmap

- [x] v0.1 — AFE Drafter agent producing valid .docx
- [x] v0.2 — Cost DB with historical benchmarks (synthetic)
- [ ] v0.3 — Pipeline Tracker with status state machine + Streamlit dashboard
- [ ] v0.4 — Variance Analyzer (actual-vs-AFE) with rig/vendor breakdown
- [ ] v0.5 — Integration hook for Project 1 (well review → AFE draft is one chain)
- [ ] v0.6 — Routing prediction model: which AFEs will bottleneck where, by historical pattern

## Why this matters for the AI-engineering hiring conversation

This isn't a toy. It addresses a problem hiring managers have explicitly flagged as a gap they can't fill — production engineers who can scale beyond what a single human can write in a week. Combined with the Production Engineer Copilot, it's a complete end-to-end workflow: well diagnosis → intervention selection → AFE draft → approval tracking → variance analysis.

## License

MIT.

## Contact

Eric Diaz II — [LinkedIn](https://www.linkedin.com/in/eric-a-diaz2) — diaz.a.eric1@gmail.com

Available for senior AI engineering roles and consulting engagements with multi-rig E&P operators.
