# Plan Summary: Three Key Changes

## Change 1: Conditional Edge on `has_conflict`

**OLD (v1):** All nodes executed linearly; conflict was informational only; single `rank_repairs` node.

**NEW (v2):** After `detect_conflict`, graph branches into TWO repair ranking variants:

```
detect_conflict
  ↓
  ├─ conflict_detected == True  → rank_repairs_conflict
  │   (LLM prompt emphasizes source disagreement; which to trust; why)
  │
  └─ conflict_detected == False → rank_repairs_agreement
      (LLM prompt treats sources as aligned; normal ranking)
  
  Both → write_reasoning → finalize_report
```

**Why:** Explicitly addresses source tension in prompts. Architecture reflects diagnostic reality: APM measures symptoms, infra measures root health. When they disagree, LLM needs to choose which to weight.

---

## Change 2: Distinct `write_reasoning` Node

**OLD (v1):** Single `write_justification` node polished prose for each strategy's `justification` and `trade_off_acknowledged` fields (redundant; just rewording ranking output).

**NEW (v2):** Separate `write_reasoning` node with distinct job:

- **Input:** ranked_strategies, diagnostics, conflict summary
- **Output:** reasoning_summary (single narrative string, multi-sentence)
- **Task:** Synthesize root-cause story tying hypothesis to repair strategy selection

Example output (NOT bullet points):
```
"Cache hit rate collapsed to 8% (eviction 94%) while APM flagged api_gateway 
degradation. Infrastructure correctly identifies cache as critical — this is 
the root cause, not a symptom. Restart cache to rebuild entries. If restart 
fails quickly, scale queue workers to reduce downstream load pressure."
```

**Why:** Clean separation of concerns: 
- `rank_repairs_*` produces strategy ranking (picks 3 from catalog)
- `write_reasoning` produces narrative (tells the story of root cause + repair choices)
- Two focused LLM calls instead of one trying to do both

---

## Change 3: Phased Implementation (6 Steps)

**OLD (v1):** Single plan; write all code at once; test.

**NEW (v2):** Six executable steps with approval gate after each:

| Step | Task | Approval Gate |
|------|------|--------|
| 2a | Define state schema + node function stubs | Confirm TypedDict and signatures correct |
| 2b | Implement four pure Python nodes (fetch, apm, infra, severity) | Confirm numeric logic unchanged on cache_collapse |
| 2c | Implement LLM nodes + prompts (conflict-aware ranking, narrative synthesis) | Confirm LLM calls work; fallback logic valid |
| 2d | Wire StateGraph (parallel nodes, conditional edge, convergence) | Confirm graph topology correct (draw ASCII) |
| 2e | End-to-end test on cache_collapse scenario | Confirm output shape matches old agent.py |
| 2f | Test all three scenarios (cache_collapse, db_degradation, queue_backup) | Confirm routing logic + no regressions |

**Why:** 
- Incremental validation catches issues early
- Each step is small, reviewable, testable
- "Done" is clear for each phase
- Confidence builds incrementally

---

## Revised Plan Documents

**1. LANGGRAPH_MIGRATION_PLAN_V2.md**
- Complete plan with updated architecture
- State schema (8 fields)
- Node list (8 nodes: 4 pure Python, 2 LLM rank variants, 1 LLM narrative, 1 finalize)
- Graph topology with conditional edge and parallel fan-out
- LLM config (http://localhost:20128/v1, model "free_first", OPENAI_API_KEY env var)
- Prompt overview (conflict-focused, agreement-focused, narrative-synthesis)
- File changes (agent.py rewrite, requirements.txt append)
- Byte-identical preservation matrix

**2. IMPLEMENTATION_STEPS.md**
- Six detailed steps (2a–2f)
- Each step includes deliverables, validation checklist, approval gate
- Example code structure and test scenarios
- Execution model: one step at a time, wait for confirmation

---

## Key Invariants (Preserved)

- ✅ `REPAIR_STRATEGY_CATALOG` — byte-identical
- ✅ `APM_LATENCY_THRESHOLDS`, `APM_ERROR_RATE_THRESHOLD` — byte-identical
- ✅ All pure Python logic (get_apm_diagnostics, etc.) — byte-identical
- ✅ Three scenarios (cache_collapse, db_degradation, queue_backup) — unchanged
- ✅ FastAPI request/response shape — unchanged
- ✅ run_agent(fault, scenario) → dict signature — unchanged

---

## Next Step

**AWAITING APPROVAL**

- Review LANGGRAPH_MIGRATION_PLAN_V2.md and IMPLEMENTATION_STEPS.md
- Confirm three changes make sense
- If approved, I will execute Step 2a (create state schema + stubs) and wait for confirmation

**No code written yet. This is plan review only.**
