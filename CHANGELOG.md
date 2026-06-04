# Changelog

All notable changes to AFE Copilot are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] — 2026-06-02

- Resilience: the optional Monte-Carlo economics import is now guarded, so if a
  build/runtime hiccup makes it unavailable the rest of the app still loads (the
  section shows a notice instead of white-screening). Root cause of the v0.3.0/0.3.1
  outage was a sticky Streamlit bytecode cache serving a pre-`simulate_economics`
  `economics.pyc`; a full app Reboot clears it.

## [0.3.1] — 2026-06-02

- Republish to force a clean Streamlit Cloud rebuild (the v0.3.0 deploy served a
  stale build that failed importing `simulate_economics`). No functional change —
  the source was already correct.

## [0.3.0] — 2026-06-02

- Monte-Carlo AFE economics (P10/P50/P90 + tornado sensitivity)
- Validated one-click chain from Production Engineer Copilot (schema validation, friendly errors)
- Contingency now computed from its stated % (cost table can't drift from the math)
- docx generation decoupled from the Anthropic SDK
- Fixed payout off-by-one; variance no longer crashes on empty input; Word tables render bold (no literal **)

## [0.2.0]

- Initial public demo.
