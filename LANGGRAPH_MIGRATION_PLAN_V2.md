# LangGraph Migration Plan (Revised v2)

## Overview
Rebuild the diagnostic agent from custom OpenAI loop to LangGraph with conditional branching on conflict detection. Three key changes from v1:

1. **CONDITIONAL EDGE on `has_conflict`** — two rank_repairs variants with explicit prompt differences
2. **DISTINCT `write_reasoning` node** — synthesizes narrative, not prose polish; tells the root-cause story
3. **PHASED IMPLEMENTATION** — six steps with validation gates between each

---

## 1. State Schema (TypedDict)

```python
class DiagnosticAgentState(TypedDict):
    # Input
    fault_description: str
    system_state: dict[str, dict]
    
    # Pure Python diagnostics
    diagnostic_a: dict                        # APM result
    diagnostic_b: dict                        # Infra result
    conflict: dict                            # Conflict summary (conflict_detected, conflicting_components, etc.)
    severity_ranking: list[dict]              # Pre-computed severity scores
    
    # LLM outputs (set by rank_repairs_* nodes)
    ranked_strategies: list[dict] | None      # [{rank, strategy_name, justification, trade_off_acknowledged}, ...]
    recommended_action: str | None
    
    # LLM output (set by write_reasoning node only)
    reasoning_summary: str | None             # Top-level narrative tying root cause to repair choices
```

---

## 2. Node Definitions

| Node | Input Keys | Output Keys | LLM? | Logic |
|------|------------|------------|------|-------|
| `fetch_system_state` | (none) | system_state, fault_description | No | Load scenario + fault into state |
| `compute_apm_diagnostics` | system_state, fault_description | diagnostic_a | No | Call `get_apm_diagnostics()` |
| `compute_infra_diagnostics` | system_state, fault_description | diagnostic_b | No | Call `get_infra_diagnostics()` |
| `compute_severity_ranking` | system_state | severity_ranking | No | Call `build_severity_ranking()` |
| `detect_conflict` | diagnostic_a, diagnostic_b | conflict | No | Call `build_conflict_summary()` → routes on `conflict["conflict_detected"]` |
| `rank_repairs_conflict` | all prev + conflict | ranked_strategies, recommended_action | **Yes** | LLM prompt emphasizes source tension; which to trust, why |
| `rank_repairs_agreement` | all prev + conflict | ranked_strategies, recommended_action | **Yes** | LLM prompt ranks normally (aligned sources) |
| `write_reasoning` | all prev + ranked_strategies | reasoning_summary | **Yes** | LLM synthesizes root-cause narrative (NOT prose polish of strategies) |
| `finalize_report` | all prev | (return output dict) | No | Assemble RankedReport |

---

## 3. Graph Structure & Conditional Routing

```
START
  │
  ↓
[fetch_system_state]  ← input: fault, scenario
  │
  ↓
┌─────────────────────────────────┐
│  PARALLEL:                       │
│  ├─ compute_apm_diagnostics     │
│  ├─ compute_infra_diagnostics   │
│  └─ compute_severity_ranking    │
└─────────────────────────────────┘
  │ (all three complete)
  ↓
[detect_conflict]  → evaluates conflict_detected flag
  │
  ├─ conflict_detected == True
  │        ↓
  │  [rank_repairs_conflict]
  │        │
  │        └─────────────┬─────────────┐
  │                      │             │
  ├─ conflict_detected == False         │
  │        ↓                            │
  │  [rank_repairs_agreement]           │
  │        │                            │
  │        └─────────────┬──────────────┘
  │                      │ (both converge)
  ↓
[write_reasoning]  ← narrative synthesis
  │
  ↓
[finalize_report]
  │
  ↓
END
```

**Key Points:**
- Parallel nodes (apm, infra, severity) execute simultaneously; join implicitly before detect_conflict
- `detect_conflict` is a routing node (returns string: "rank_repairs_conflict" or "rank_repairs_agreement")
- Both rank_repairs variants have identical signature; LLM prompt differs
- `write_reasoning` always runs after rank_repairs (either variant)

---

## 4. LLM Nodes: Prompts Overview

### `rank_repairs_conflict` Prompt
- Context: APM and infra disagree on which components are flagged
- Task: Explain the conflict (symptom vs. root health), decide which source to weight more
- Select top 3 strategies that address root cause, not symptoms
- For each strategy: explicit `trade_off_acknowledged` explaining trade-off vs alternatives
- Return: JSON with `ranked_strategies`, `recommended_action`

### `rank_repairs_agreement` Prompt
- Context: Both sources agree on flagged components
- Task: Rank by severity and root-cause likelihood
- Select top 3 strategies; treat as aligned signal
- For each strategy: explicit `trade_off_acknowledged`
- Return: JSON with `ranked_strategies`, `recommended_action`

### `write_reasoning` Prompt
- **Distinct purpose:** Synthesize a coherent root-cause narrative
- **Input:** diagnostics, conflict summary, ranked strategies
- **Output:** `reasoning_summary` (single string, multi-sentence narrative)
- **Content:** Tie together the "why this is the root cause" story and "why these three repairs are the best levers"
- **NOT:** Prose polish of strategy justifications (those come from rank_repairs nodes)

---

## 5. LLM Configuration (All Nodes)
- **Endpoint:** `http://localhost:20128/v1` (OpenAI-compatible)
- **Model:** `"free_first"`
- **Auth:** `os.environ.get("OPENAI_API_KEY")` — no hardcoding
- **Fallback:** If LLM unavailable, use `_fallback_ranking()` logic for rank_repairs; use generic summary for reasoning

---

## 6. File Changes

### `agent.py` (rewrite)
- **Keep byte-identical:**
  - `REPAIR_STRATEGY_CATALOG`
  - `APM_LATENCY_THRESHOLDS`, `APM_ERROR_RATE_THRESHOLD`
  - `get_apm_diagnostics()`, `get_infra_diagnostics()`, `build_conflict_summary()`, `build_severity_ranking()`
  
- **Remove:** `get_client()`, `MODEL`, `TOOLS`, `evaluate_and_rank_strategies()`, old `run_agent()`

- **Add:**
  - `DiagnosticAgentState` TypedDict
  - 8 node functions (fetch, 3x pure, 2x rank_repairs variants, write_reasoning, finalize)
  - `_route_on_conflict()` function (returns string for conditional edge)
  - OpenAI client init (http://localhost:20128/v1, OPENAI_API_KEY)
  - Graph builder (StateGraph, add all nodes + edges, add_conditional_edges)
  - New `run_agent()` wrapping `graph.invoke()`

### `requirements.txt` (append)
```
langgraph>=0.1.0
langchain-core>=0.1.0
langchain-openai>=0.1.0
```

### Unchanged
- `models.py`, `routes/`, `main.py`, `simulator.py`, `store.py`, `security.py`

---

## 7. Implementation Steps (Six Phases)

### **Step 2a: State Schema & Node Stubs**
Define `DiagnosticAgentState` TypedDict and stub all 8 node functions (empty bodies, correct signatures).
- Verify: Type hints correct, state keys match across all nodes
- Output: Skeleton agent.py with structure in place

### **Step 2b: Four Pure Python Nodes**
Implement: fetch_system_state, compute_apm_diagnostics, compute_infra_diagnostics, compute_severity_ranking
- Reuse existing functions from current agent.py
- Verify: Each node reads correct state keys, writes correct output keys
- Test: Single scenario (cache_collapse)

### **Step 2c: LLM Nodes & Prompts**
Implement: rank_repairs_conflict, rank_repairs_agreement, write_reasoning, fallback logic
- Write prompt templates
- Implement OpenAI client init (http://localhost:20128/v1, OPENAI_API_KEY env var)
- Implement fallback JSON parsing for rank_repairs
- Verify: Prompts produce valid JSON responses

### **Step 2d: Graph Wiring**
Build StateGraph:
- Add all 8 nodes
- Add edges: linear start→fetch→parallel→join→detect_conflict
- Add parallel execution for apm/infra/severity
- Add conditional edge after detect_conflict (routing on has_conflict flag)
- Both rank_repairs variants converge into write_reasoning
- write_reasoning → finalize_report
- Compile graph

### **Step 2e: End-to-End Test (cache_collapse)**
Run graph.invoke() on cache_collapse scenario:
- Verify all nodes execute
- Verify state flows correctly
- Verify output shape matches old agent.py
- Verify FastAPI /fault route still works

### **Step 2f: Test All Three Scenarios**
Run all scenarios (cache_collapse, db_degradation, queue_backup):
- Verify different conflict states route correctly (conflict vs. agreement)
- Verify LLM outputs consistent and valid
- Verify RankedReport model validates
- Verify no regressions in numeric thresholds

---

## 8. Validation Criteria

After each step, verify before proceeding:

**Step 2a:** 
- State schema compiles with TypedDict
- All 8 node functions have correct parameter/return types
- agent.py imports no errors

**Step 2b:**
- Each pure Python node outputs correct keys
- No state mutations (TypedDict semantics)
- Numeric thresholds unchanged

**Step 2c:**
- LLM prompts are well-formed
- Mock/test LLM call succeeds (or fallback engages)
- JSON response parsed to dict with required keys

**Step 2d:**
- Graph compiles without errors
- graph.invoke() runs to completion
- All nodes called in correct order

**Step 2e:**
- graph.invoke() output == old agent.run_agent() output (shape + content)
- FastAPI /fault route returns 200 with valid RankedReport
- No exceptions raised

**Step 2f:**
- All three scenarios return valid RankedReport
- Conflict detection triggers correct rank_repairs variant
- No numeric regression (thresholds, confidence scores)

---

## 9. Backward Compatibility

- ✅ `run_agent(fault: dict, scenario: str) → dict` signature unchanged
- ✅ Output dict keys match old agent (diagnostic_a, diagnostic_b, conflict, strategies_evaluated, recommended_action, reasoning_summary)
- ✅ FastAPI routes require zero changes
- ✅ All constants (catalogs, thresholds) byte-identical
- ✅ Three scenarios unchanged
