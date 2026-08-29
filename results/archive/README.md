# Archived runs

Superseded results, kept as a record of what was attempted rather than as
evidence. They are deliberately outside the `agent_results*.json` glob that
`scripts/run_compare.py` reads, so they cannot leak into the headline table.

## `agent_results_gemini-3.5-flash-subset.json`

The agent arm attempted on `gemini-3.5-flash` while the baseline's model,
`gemini-3.6-flash`, was quota-exhausted (see `DECISIONS.md` D-013). Superseded
once billing allowed the arm to return to the baseline's model (D-015).

Kept for one reason: its `case_07` trajectory is the evidence for D-014. That
run designed `isolate_test(test_network_timeout)` against a case whose only
test is `test_status_is_fetched_from_a_healthy_service`, pytest collected
nothing, all 150 runs errored, and the loop read the resulting 0.0% flake rate
as "eliminated" — manufacturing support for the wrong root-cause class.
