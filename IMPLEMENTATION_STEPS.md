# LangGraph Implementation: Step-by-Step Plan

## Execution Model
Execute ONE step at a time. Verify step output before proceeding to next. After each step completes, display results and ask for confirmation before moving forward.

---

## Step 2a: State Schema & Node Stubs

**Goal:** Define state structure and create all 8 node functions with proper signatures and empty bodies.

**Deliverables:**
1. `DiagnosticAgentState` TypedDict with all keys
2. Eight node function stubs:
   - `fetch_system_state(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `compute_apm_diagnostics(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `compute_infra_diagnostics(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `compute_severity_ranking(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `detect_conflict(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `rank_repairs_conflict(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `rank_repairs_agreement(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `write_reasoning(state: DiagnosticAgentState) → DiagnosticAgentState`
   - `finalize_report(state: DiagnosticAgentState) → dict`

**Validation Checklist:**
- [ ] TypedDict compiles (all keys with correct types)
- [ ] All 8 functions defined with matching signatures
- [ ] agent.py imports without errors
- [ ] Node stubs have placeholder `pass` or `return state` bodies

**Approval Gate:** Confirm step 2a output before proceeding.

---

## Step 2b: Five Pure Python Nodes

**Goal:** Implement the five deterministic diagnostic nodes that reuse existing pure Python logic.

**Nodes:**
1. `fetch_system_state` — load scenario by name into state
2. `compute_apm_diagnostics` — call existing `get_apm_diagnostics()`
3. `compute_infra_diagnostics` — call existing `get_infra_diagnostics()`
4. `compute_severity_ranking` — call existing `build_severity_ranking()`
5. `detect_conflict` — call existing `build_conflict_summary()` (PURE PYTHON, acts as routing node via graph.add_conditional_edges())

**Implementation Detail:**
- Reuse functions from current agent.py as-is (byte-identical)
- Each node returns updated state dict with new keys set
- No external calls, no randomness, no LLM in any of these nodes
- detect_conflict is routing node but NOT an LLM node; it purely computes conflict summary and produces routing signal
- Routing signal (has_conflict) is handled by graph.add_conditional_edges(), not by detect_conflict itself

**Validation Checklist:**
- [ ] fetch_system_state loads system_state from simulator.get_scenario()
- [ ] compute_apm_diagnostics sets state["diagnostic_a"]
- [ ] compute_infra_diagnostics sets state["diagnostic_b"]
- [ ] compute_severity_ranking sets state["severity_ranking"]
- [ ] detect_conflict sets state["conflict"] (no LLM call)
- [ ] All thresholds (APM_LATENCY_THRESHOLDS, APM_ERROR_RATE_THRESHOLD) unchanged
- [ ] Numeric values match old agent.py (confidence, severity scores, flags)

---

## Step 2c: LLM Nodes & Prompts

**Goal:** Implement LLM-based nodes with full prompt templates and error handling.

**Nodes:**
1. `detect_conflict` — pure Python but acts as routing node; call existing `build_conflict_summary()`
2. `rank_repairs_conflict` — LLM; conflict-focused prompt
3. `rank_repairs_agreement` — LLM; agreement-focused prompt
4. `write_reasoning` — LLM; narrative synthesis

**Implementation Detail:**

**detect_conflict:**
- Call existing `build_conflict_summary()`
- Return state with conflict key set
- No routing logic here; routing happens in graph.add_conditional_edges()

**rank_repairs_conflict & rank_repairs_agreement:**
- Create OpenAI client: `OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url="http://localhost:20128/v1")`
- Build prompt (see LANGGRAPH_MIGRATION_PLAN_V2.md § 4 for content)
- Call client.chat.completions.create(model="free_first", messages=[...], response_format={"type": "json_object"})
- Parse JSON response to extract ranked_strategies, recommended_action
- On error: fallback to `_fallback_ranking()` logic
- Return state with ranked_strategies and recommended_action set

**write_reasoning:**
- Input: full state including ranked_strategies
- Prompt: "Synthesize a coherent root-cause narrative explaining why [root] is the issue and why these three repairs are the best choices"
- Parse response to extract reasoning_summary string
- On error: fallback to generic summary: "Root cause could not be determined; repair strategies selected based on pre-computed severity metrics and source agreement."
- Return state with reasoning_summary set

**Validation Checklist:**
- [ ] OpenAI client initialized with http://localhost:20128/v1 and OPENAI_API_KEY from environment
- [ ] Prompts template rendered with actual diagnostics and catalog data
- [ ] JSON response parsing handles missing keys gracefully
- [ ] Fallback logic produces valid output on LLM error
- [ ] rank_repairs_conflict prompt emphasizes source disagreement
- [ ] rank_repairs_agreement prompt treats sources as aligned
- [ ] write_reasoning produces a narrative (multi-sentence string, not bullet points)
- [ ] Test on cache_collapse (no conflict) → uses rank_repairs_agreement
- [ ] Test on db_degradation (conflict) → uses rank_repairs_conflict

**Approval Gate:** Test LLM calls on two scenarios (conflict + agreement); confirm prompts and fallback logic work before proceeding.

---

## Step 2d: Graph Wiring

**Goal:** Build the LangGraph StateGraph with all nodes and edges, including parallel execution and conditional routing.

**Components:**
1. Create StateGraph(DiagnosticAgentState)
2. Add all 8 nodes
3. Add edges:
   - Linear: START → fetch_system_state
   - Parallel: fetch → {apm, infra, severity}
   - Implicit join: parallel outputs → detect_conflict
   - Conditional: detect_conflict → rank_repairs_conflict OR rank_repairs_agreement
   - Linear: rank_repairs_* → write_reasoning → finalize_report → END
4. Compile: graph = builder.compile()

**Implementation Detail:**
- graph.add_node("fetch_system_state", fetch_system_state)
- ... (same for all 8 nodes)
- graph.add_edge(START, "fetch_system_state")
- graph.add_edge("fetch_system_state", "compute_apm_diagnostics")
- graph.add_edge("fetch_system_state", "compute_infra_diagnostics")
- graph.add_edge("fetch_system_state", "compute_severity_ranking")
- graph.add_edge("compute_apm_diagnostics", "detect_conflict")
- graph.add_edge("compute_infra_diagnostics", "detect_conflict")
- graph.add_edge("compute_severity_ranking", "detect_conflict")
- graph.add_conditional_edges("detect_conflict", _route_on_conflict, {"conflict": "rank_repairs_conflict", "agreement": "rank_repairs_agreement"})
- graph.add_edge("rank_repairs_conflict", "write_reasoning")
- graph.add_edge("rank_repairs_agreement", "write_reasoning")
- graph.add_edge("write_reasoning", "finalize_report")
- graph.add_edge("finalize_report", END)

**_route_on_conflict function:**
- Input: state
- Logic: return "conflict" if state["conflict"]["conflict_detected"] else "agreement"
- Output: string ("conflict" or "agreement")

**Validation Checklist:**
- [ ] StateGraph imports from langgraph.graph
- [ ] All 8 nodes added to graph
- [ ] Parallel edges correctly set up (all three nodes added after fetch)
- [ ] detect_conflict has conditional_edges (returns "conflict" or "agreement")
- [ ] Both rank_repairs variants route to write_reasoning
- [ ] write_reasoning routes to finalize_report
- [ ] finalize_report routes to END
- [ ] graph.compile() succeeds without errors
- [ ] graph.get_graph().draw_ascii() shows correct structure

**Approval Gate:** Show graph structure (ASCII or Mermaid); confirm topology is correct before proceeding.

---

## Step 2e: End-to-End Test (cache_collapse)

**Goal:** Run the complete graph on one scenario and verify output shape/content.

**Test Scenario:** cache_collapse (no conflict expected)

**Test Flow:**
1. Invoke graph: `result = graph.invoke({"fault_description": "...", "system_state": {...}, ...})`
2. Verify all keys in result:
   - diagnostic_a, diagnostic_b, conflict
   - severity_ranking, ranked_strategies, recommended_action
   - reasoning_summary
3. Verify numeric values:
   - Confidence scores in diagnostic_a and diagnostic_b
   - Severity scores in severity_ranking
   - Ranked strategies contain strategy_name from REPAIR_STRATEGY_CATALOG
4. Verify LLM outputs:
   - reasoning_summary is a non-empty string
   - ranked_strategies list has 3 entries (or fewer if fallback)
5. Compare to old agent.run_agent() output:
   - Same diagnostic content
   - Same conflict structure
   - Same ranked strategies (if LLM produces same selection)

**Validation Checklist:**
- [ ] graph.invoke() completes without exception
- [ ] Result is dict with all required keys
- [ ] diagnostic_a and diagnostic_b have correct structure (source, component_states, summary, confidence, flagged_components)
- [ ] conflict has correct structure (conflict_detected, conflicting_components, explanations)
- [ ] ranked_strategies is list of dicts with rank, strategy_name, justification, trade_off_acknowledged
- [ ] recommended_action is non-empty string
- [ ] reasoning_summary is non-empty string (narrative, not bullet-point list)
- [ ] confidence scores are floats between 0 and 1
- [ ] severity_ranking contains all components from system_state
- [ ] No conflict detected in cache_collapse → rank_repairs_agreement was used

**Approval Gate:** Display full result dict from cache_collapse invocation; confirm output format and values before proceeding.

---

## Step 2f: Test All Three Scenarios

**Goal:** Run graph on all three test scenarios and verify routing logic, no regressions.

**Test Scenarios:**
1. `cache_collapse` — expect no conflict (rank_repairs_agreement used)
2. `db_degradation` — expect conflict (rank_repairs_conflict used)
3. `queue_backup` — verify at least one scenario uses rank_repairs_conflict

**Test Flow:**
For each scenario:
1. Invoke graph with scenario metrics
2. Verify conflict_detected flag matches expectation
3. Verify correct rank_repairs variant was used (inspect node execution trace or check routing logic)
4. Verify output shape and no NaN/None in critical fields
5. Verify RankedReport Pydantic model validates the result
6. Compare numeric metrics to old agent (thresholds, flags, confidence)

**Validation Checklist:**
- [ ] cache_collapse: conflict_detected == False, uses rank_repairs_agreement
- [ ] db_degradation: conflict_detected == True, uses rank_repairs_conflict (or at least shows conflict)
- [ ] queue_backup: output valid, routing correct
- [ ] All three scenarios produce valid RankedReport objects
- [ ] No KeyError or missing state keys
- [ ] All numeric thresholds identical to old agent.py
- [ ] Confidence scores logical (more degraded components → lower confidence)
- [ ] Severity ranking components ordered by deviation magnitude

**Approval Gate:** Show results for all three scenarios; confirm routing logic correct and no regressions before committing code.

---

## Commit & Finalize

After step 2f approval:
1. Update requirements.txt with langgraph, langchain-core, langchain-openai
2. Clean up old functions/dead code from agent.py (if any)
3. Verify FastAPI routes still work (run main.py, call /fault endpoint)
4. Git commit with message: "feat: migrate agent to LangGraph with conditional branching on conflict detection"
5. Mark task complete

---

## Summary: Wait for Approval Between Steps

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2a (State & Stubs)                                         │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Approval received
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2b (Pure Python Nodes)                                     │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Approval received
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2c (LLM Nodes & Prompts)                                   │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Approval received
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2d (Graph Wiring)                                          │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Approval received
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2e (E2E Test: cache_collapse)                              │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Approval received
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2f (Test All Scenarios)                                    │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Done → WAIT FOR APPROVAL                                      │
└─────────────────────────────────────────────────────────────────┘
         ↓ Final approval
┌─────────────────────────────────────────────────────────────────┐
│ COMMIT & FINALIZE                                               │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Complete                                                       │
└─────────────────────────────────────────────────────────────────┘
```
