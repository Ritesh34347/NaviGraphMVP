# Evaluation Harness (Phase 8 placeholder)

This directory is a placeholder. It exists now so the eventual location is
settled, but nothing in it is implemented yet.

## What lands here, in Phase 8

- A **golden set** of 50+ representative business questions (see
  `golden_set/`), each with an expected intent classification, expected
  grounded entities/metrics, and (once the Query/Insight agents exist) an
  expected answer shape or acceptable answer range.
- An **LLM-as-judge harness** that runs the full pipeline against the golden
  set and scores each answer for correctness, groundedness (does it cite
  real schema/metric values rather than hallucinating them), and narrative
  quality, using a separate judge model call per question.
- Regression tracking across agent/prompt changes -- the harness should be
  runnable in CI once enough of the pipeline exists to produce a real
  end-to-end answer.

## Why it's empty now

This repository is in Phase 1 (infra scaffolding) plus the single real
Intent Understanding agent. An evaluation harness meaningfully testing
"does NaviGraph answer business questions well" requires the Query,
Guardrail, and Insight agents to exist first -- there is currently only one
stage of the pipeline to evaluate. See `LIMITATIONS.md` at the repo root.
