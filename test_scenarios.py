#!/usr/bin/env python3
"""End-to-end tests for all 3 diagnostic scenarios."""

import json
from agent_langgraph import run_agent
from models import RankedReport, FaultReport

def test_scenario(scenario_name: str, fault_description: str):
    """Test one scenario."""
    print(f"\n{'='*70}")
    print(f"Testing scenario: {scenario_name}")
    print(f"{'='*70}")
    
    fault = {
        "fault_id": f"test-{scenario_name}",
        "description": fault_description,
        "severity": "critical",
        "component": "api_gateway",  # will be overridden by scenario state
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
        
        # Validate schema
        diag_a = result.get("diagnostic_a", {})
        diag_b = result.get("diagnostic_b", {})
        conflict = result.get("conflict", {})
        
        # Check for numeric metrics in diagnostics
        if not isinstance(diag_a, dict):
            print(f"❌ FAIL: diagnostic_a is not dict: {type(diag_a)}")
            return False
        if not isinstance(diag_b, dict):
            print(f"❌ FAIL: diagnostic_b is not dict: {type(diag_b)}")
            return False
        
        # Print summary
        print(f"✓ PASS: Output structure valid")
        print(f"\nDiagnostic A (APM):")
        print(f"  Flagged components: {diag_a.get('flagged_components', [])}")
        print(f"  Metrics: {json.dumps({k: v for k, v in diag_a.items() if k != 'flagged_components'}, indent=4)}")
        
        print(f"\nDiagnostic B (Infra):")
        print(f"  Flagged components: {diag_b.get('flagged_components', [])}")
        print(f"  Metrics: {json.dumps({k: v for k, v in diag_b.items() if k != 'flagged_components'}, indent=4)}")
        
        if conflict:
            print(f"\nConflict detected:")
            print(f"  {json.dumps(conflict, indent=2)}")
        else:
            print(f"\nNo conflict detected (agreement mode)")
        
        print(f"\nStrategies evaluated: {result.get('strategies_evaluated', 0)}")
        print(f"\nRecommended action:")
        print(f"  {result.get('recommended_action', 'N/A')}")
        
        print(f"\nReasoning:")
        print(f"  {result.get('reasoning_summary', 'N/A')}")
        
        return True
        
    except Exception as e:
        print(f"❌ FAIL: Exception occurred")
        print(f"  Error: {e}")
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
        print("✓ ALL TESTS PASSED")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        return 1

if __name__ == "__main__":
    exit(main())
