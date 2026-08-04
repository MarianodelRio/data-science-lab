---
id: T-046
phase: 5
agent: pipeline-agent
depends_on: [T-013, T-014, T-015, T-016, T-017, T-018, T-019, T-020, T-021, T-022, T-023, T-024, T-029, T-030, T-031, T-032, T-033, T-034, T-035]
status: blocked
folders: ["tests/smoke/", "tests/fixtures/"]
outputs: [end-to-end smoke test running the full pipeline on a tiny dataset]
size: M
branch: ~
pr: ~
---

## End-to-end smoke test (tests/smoke/)

**Scope:** `tests/smoke/` + `tests/fixtures/`. Validates the assembled pipeline.

**Delivers:**
- A tiny dataset fixture (e.g. Iris/Titanic head as CSV) + a minimal workspace fixture
- A full-pipeline run (iteration 0 → delivery) using the cheapest configured models (Groq free tier) OR fully mocked LLMs for CI determinism
- Asserts the pipeline reaches Delivery and produces the expected workspace artifacts

**Done when:**
- [ ] the run creates `validation/fold_config.json`, an `experiments/baseline/results.json`, ≥1 `experiments/exp_*/results.json`, and `reports/final_report.md`
- [ ] the supervisor visits phases in order and skips Phase 3 on iteration 1 (asserted)
- [ ] `fold_config.json` is unchanged after the run (immutability check)
- [ ] the run resumes correctly after a simulated mid-run restart (checkpointer)
- [ ] a CI-safe variant runs fully mocked (no network) and is the one wired into CI
- [ ] `docs/pipeline.md` "Running the smoke test" section updated
