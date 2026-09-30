# Diagnostic Repair Ranking Agent

A fault report and the agent gathers conflicting diagnostics from two independent sources, detects the contradiction, and produces a ranked repair strategy with justification.

## How It Works

1. You describe a fault (or pick a scenario: `cache_collapse`, `db_degradation`, `queue_backup`)
2. Two diagnostic sources run against the same telemetry:
   - **APM Monitor** — flags components by latency/error thresholds
   - **Infra Health** — flags components by their own resource/status
3. The agent detects when the sources disagree (symptom vs. root cause)
4. It routes through a conflict-aware branch and ranks the top 3 repair strategies
5. Returns a structured report with justification and a root-cause narrative

## Architecture

LangGraph state machine:

```
START → fetch_system_state
          ├─ compute_apm_diagnostics   ─┐
          ├─ compute_infra_diagnostics ─┼─ parallel
          └─ compute_severity_ranking  ─┘
                        ↓
                 detect_conflict
                        ↓
                ┌── conflict? ──┐
               yes              no
                ↓                ↓
        rank_repairs_      rank_repairs_
          conflict           agreement
                └───────┬───────┘
                        ↓
                 write_reasoning
                        ↓
                 finalize_report
                        ↓
                       END
```

**Key principle:** All metrics, thresholds, and conflict detection are pure Python. The LLM only selects repair strategies from a fixed catalog and writes the narrative.

## Stack

- **Python** + **LangGraph** (state machine, conditional routing)
- **FastAPI** + **Pydantic v2** (API layer)
- **Claude Haiku 4.5** via OpenAI-compatible endpoint (strategy ranking + narrative)
- **pytest** (scenario tests)

## Run Locally

```bash
pip install -r requirements.txt
cp .env.example .env   # set OPENAI_API_KEY and OPENAI_BASE_URL
uvicorn main:app --reload --port 8000
```

Test:

```bash
curl -X POST http://localhost:8000/fault \
  -H "Content-Type: application/json" \
  -d '{"description":"Cache hit rate collapsed. APM blames DB latency, infra blames cache.","severity":"high","component":"cache","scenario":"cache_collapse"}'
```

## Scenarios

| Scenario         | Sources agree?                     | Routes to |
|------------------|------------------------------------|-----------|
| `cache_collapse` | No — different components flagged  | conflict  |
| `db_degradation` | Yes — same component               | agreement |
| `queue_backup`   | Yes — same components              | agreement |

## Notes from the Rebuild

Migrated from a custom OpenAI loop to LangGraph. Four things worth remembering:

1. **Parallel nodes must return only their own keys** — returning the full state from two concurrent nodes throws `InvalidUpdateError`.
2. **LLMs don't reliably return pure JSON** — Haiku prepends prose. Parsing needs a fallback that extracts the first `{` to the last `}`.
3. **`response_format` doesn't survive OpenAI-compatible proxies** — the request looks OpenAI-compatible, but Claude doesn't honour the JSON-mode contract. Prompt engineering + robust parsing is the workaround.
4. **LangGraph silently drops state keys not in the schema** — if a node returns a key the `TypedDict` doesn't declare, it vanishes.

## Known Limitations

- Component-level conflict detection only. Cause-level disagreement (`db_degradation`) is scoped out.
- Fixed repair catalog (5 strategies, hardcoded).
- Reports stored in memory — lost on restart.

## Stack Summary

Python · LangGraph · FastAPI · Pydantic v2 · Claude Haiku 4.5

---

Built by Bashaar
