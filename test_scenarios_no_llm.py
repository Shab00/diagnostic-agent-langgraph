#!/usr/bin/env python3
"""End-to-end tests for all 3 diagnostic scenarios (no LLM required - uses fallback)."""

import json
import sys
from agent_langgraph import run_agent

def test_scenario(scenario_name: str, fault_description: str):
    """Test one scenario."""
    print(f"\n{'='*70}")
    print(f"Testing scenario: {scenario_name}")
    print(f"{'='*70}")
    
    fault = {
        "fault_id": f"test-{scenario_name}",
        "description": fault_description,
        "severity": "critical",
        "component": "api_gateway",
        "reported_at": "2024-01-01T00:00:00Z",
    }
    
    try:
        result = run_agent(fault, scenario_name)
        
        # Validate output structure
        required_keys = {
            "diagnostic_a", "diagnostic_b", "conflict",
            "strategies_evaluated", "recommended_action", "reasoning_summary"
        }
        missing = required_keys - set(result.keys())
        if missing:
            print(f"❌ FAIL: Missing keys in output: {missing}")
            return False
        
        # Validate types
        if not isinstance(result.get("diagnostic_a"), dict):
            print(f"❌ FAIL: diagnostic_a is not dict: {type(result.get('diagnostic_a'))}")
            return False
        if not isinstance(result.get("diagnostic_b"), dict):
            print(f"❌ FAIL: diagnostic_b is not dict: {type(result.get('diagnostic_b'))}")
            return False
        
        # Print summary
        print(f"✓ PASS: Output structure valid")
        
        diag_a = result.get("diagnostic_a", {})
        diag_b = result.get("diagnostic_b", {})
        
        print(f"\nDiagnostic A (APM):")
        print(f"  Flagged: {len(diag_a.get('flagged_components', []))} components")
        metrics_a = {k: v for k, v in diag_a.items() if k not in ['flagged_components', 'timestamp']}
        if metrics_a:
            print(f"  Metrics: {json.dumps(metrics_a, indent=4)}")
        
        print(f"\nDiagnostic B (Infra):")
        print(f"  Flagged: {len(diag_b.get('flagged_components', []))} components")
        metrics_b = {k: v for k, v in diag_b.items() if k not in ['flagged_components', 'timestamp']}
        if metrics_b:
            print(f"  Metrics: {json.dumps(metrics_b, indent=4)}")
        
        if result.get("conflict"):
            print(f"\n✓ Conflict detected (going through conflict resolution)")
        else:
            print(f"\n✓ No conflict (agreement mode used)")
        
        print(f"\nStrategies evaluated: {result.get('strategies_evaluated', 0)}")
        print(f"\nRecommended action: {result.get('recommended_action', 'N/A')[:60]}...")
        print(f"\nReasoning: {result.get('reasoning_summary', 'N/A')[:60]}...")
        
        return True
        
    except Exception as e:
        print(f"❌ FAIL: Exception occurred")
        print(f"  Error type: {type(e).__name__}")
        print(f"  Error: {str(e)[:200]}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all scenario tests."""
    scenarios = [
        ("cache_collapse", "Cache server is down, causing high latency and errors"),
        ("db_degradation", "Database experiencing resource exhaustion, slow queries"),
        ("queue_backup", "Message queue backed up, causing worker queue times"),
    ]
    
    results = {}
    for scenario_name, description in scenarios:
        results[scenario_name] = test_scenario(scenario_name, description)
    
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for scenario_name, passed in results.items():
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{status}: {scenario_name}")
    
    all_passed = all(results.values())
    print(f"\n{'='*70}")
    if all_passed:
        print("✓ ALL TESTS PASSED (fallback LLM used)")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
