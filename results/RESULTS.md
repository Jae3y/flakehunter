# Results

Generated 2026-08-29T19:04:49Z.

Primary metric: **residual flake rate** — failures per 500 runs after the fix,
target zero. Cost is in tokens, not dollars (`DECISIONS.md` D-007).

## Results table

| Case | Root cause | Corpus flake | Baseline after fix | Agent after fix | Cause? B/A | Agent status | Tokens B/A |
|---|---|---|---|---|---|---|---|
| 01 race condition | `race_condition` | 33.40% | 0.00% (unsound) | - | Y / - | EXCLUDED (runtime cost, see DECISIONS D-012) | 2,489 / 0 |
| 02 test order dependency | `test_order_dependency` | 32.80% | 0.00% | - | Y / - | ERROR (quota) | 27,370 / 0 |
| 03 port collision | `resource_leak_port_collision` | 38.80% | 0.00% | - | Y / - | ERROR (quota) | 5,685 / 0 |
| 04 clock dependence | `clock_dependence` | 4.00% | 0.00% | - | Y / - | ERROR (quota) | 7,195 / 0 |
| 05 hash iteration order | `hash_iteration_order` | 2.20% | 0.00% | - | Y / - | ERROR (quota) | 5,587 / 2,876 |
| 06 unseeded randomness | `unseeded_randomness` | 28.00% | 0.00% | - | Y / - | NOT RUN (quota) | 6,070 / 0 |
| 07 network timeout | `network_timeout_no_retry` | 5.80% | 0.80% | - | Y / - | ERROR (quota) | 4,399 / 3,698 |
| 08 tempfile collision | `tempfile_collision` | 5.40% | 0.00% | - | Y / - | NOT RUN (quota) | 4,102 / 0 |
| 09 float tolerance | `float_tolerance` | 12.00% | 0.00% | - | Y / - | ERROR (quota) | 5,791 / 0 |
| 10 async ordering | `async_ordering` | 27.00% | 0.00% | - | Y / - | NOT RUN (quota) | 3,266 / 0 |
| 11 cache leak | `cache_leak` | 31.60% | 0.00% | - | Y / - | NOT RUN (quota) | 4,388 / 0 |
| 12 masking trap | `publication_ordering` | 3.20% | 0.00% | 0.00% | Y / Y | PENDING | 3,113 / 7,703 |

`(unsound)` marks a verification in which runs errored rather than ran, so its
zero means nothing. Agent columns show `-` where the case was never attempted.

## Coverage

The baseline arm covers **all 12 cases**. The agent arm does not: the API runs
on the Google AI Studio free tier at **20 requests per day per model**, and a
case costs 2-6. Cases marked `quota` are checkpointed mid-loop
(`results/checkpoints/`) and resume from their established evidence rather than
restarting.

The claimed-versus-verified finding below is measured entirely from the
baseline arm, the 500-run verifications and the validator. It does not depend
on agent coverage.


### Aggregates

| Metric | Baseline | Agent |
|---|---|---|
| Cases attempted | 12/12 | 1/12 |
| Residual flake rate zero (verified) | 10/12 | 1/1 of attempted |
| Root cause identified | 12/12 | 1/1 of attempted |
| Total tokens | 79,455 | 14,277 |

Agent outcomes: **1 PENDING** approval, **0 UNRESOLVED**,
10 blocked by API quota, 1 excluded for runtime.
Patches rejected by the anti-cheat validator and re-authored: **0**.

Baseline model `gemini-3.6-flash`; agent model(s) `gemini-3.6-flash`.
Where these differ, the two arms are **not** directly comparable on those rows —
see `DECISIONS.md` D-013.


## The comparison that matters

### Claimed versus verified versus legitimate

The baseline returned a patch for **12/12** cases and
attached a confidence to every one. Two further questions then narrow it:
did the patch actually work, and is it a fix at all?

| Case | Confidence | Residual after fix | Verified? | Validator | Why not |
|---|---|---|---|---|---|
| 01 race condition | high | 0.00% *(unsound)* | **no** | **REJECTS** | patch_parses: app/counter.py line 16: invalid syntax |
| 02 test order dependency | high | 0.00% | yes | accepts | — |
| 03 port collision | high | 0.00% | yes | accepts | — |
| 04 clock dependence | high | 0.00% | yes | accepts | — |
| 05 hash iteration order | high | 0.00% | yes | accepts | — |
| 06 unseeded randomness | high | 0.00% | yes | accepts | — |
| 07 network timeout | high | 0.80% | **no** | **REJECTS** | survives_stress: 49/200 failures at 32 workers (4x oversubscription... |
| 08 tempfile collision | high | 0.00% | yes | accepts | — |
| 09 float tolerance | high | 0.00% | yes | accepts | — |
| 10 async ordering | high | 0.00% | yes | accepts | — |
| 11 cache leak | high | 0.00% | yes | accepts | — |
| 12 masking trap | high | 0.00% | yes | accepts | — |

**Claimed 12/12 → verified 10 → legitimate 10/12.**

Every patch carried the same confidence. Nothing in the model's own
output separated the ones that worked from the ones that did not —
that separation came entirely from running the tests and from the
validator, neither of which the baseline has.

**Anchor — 07 network timeout.** The baseline identified the root cause
correctly, reported `high` confidence, and
produced a patch that still fails **0.80%**
of the time at the normal worker count. Under CPU oversubscription the
validator drives that far higher: this is not an incomplete fix but a
**confirmed mask**, one that widens the timing window rather than
closing it. A single execution would have shown it green.

