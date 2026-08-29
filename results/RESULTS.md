# Results

Generated 2026-08-29T07:40:45Z.

Primary metric: **residual flake rate** — failures per 500 runs after the fix,
target zero. Cost is in tokens, not dollars (`DECISIONS.md` D-007).

## Results table

| Case | Root cause | Corpus flake | Baseline after fix | Agent after fix | Cause? B/A | Agent status | Tokens B/A |
|---|---|---|---|---|---|---|---|
| 01 race condition | `race_condition` | 33.40% | 0.00% (unsound) | - | Y / - | EXCLUDED (runtime cost, see DECISIONS D-012) | 2,489 / 0 |
| 02 test order dependency | `test_order_dependency` | 32.80% | 0.00% | - | Y / - | ERROR (quota) | 27,370 / 3,219 |
| 03 port collision | `resource_leak_port_collision` | 38.80% | 0.00% | - | Y / - | ERROR (quota) | 5,685 / 3,518 |
| 04 clock dependence | `clock_dependence` | 4.00% | 0.00% | - | Y / - | ERROR (quota) | 7,195 / 0 |
| 05 hash iteration order | `hash_iteration_order` | 2.20% | 0.00% | - | Y / - | ERROR (quota) | 5,587 / 4,496 |
| 06 unseeded randomness | `unseeded_randomness` | 28.00% | 0.00% | - | Y / - | NOT RUN (quota) | 6,070 / 0 |
| 07 network timeout | `network_timeout_no_retry` | 5.80% | 0.80% | 0.00% (patch rejected) | Y / n | UNRESOLVED (accepted by an earlier validator, REJECTED on re-valida...) | 4,399 / 26,651 |
| 08 tempfile collision | `tempfile_collision` | 5.40% | 0.00% | - | Y / - | NOT RUN (quota) | 4,102 / 0 |
| 09 float tolerance | `float_tolerance` | 12.00% | 0.00% | - | Y / - | NOT RUN (quota) | 5,791 / 0 |
| 10 async ordering | `async_ordering` | 27.00% | 0.00% | - | Y / - | NOT RUN (quota) | 3,266 / 0 |
| 11 cache leak | `cache_leak` | 31.60% | 0.00% | - | Y / - | NOT RUN (quota) | 4,388 / 0 |
| 12 masking trap | `publication_ordering` | 3.20% | 0.00% | 0.00% | Y / Y | PENDING | 3,113 / 7,703 |

`(unsound)` marks a verification in which runs errored rather than ran, so its
zero means nothing. Agent columns show `-` where the case was never attempted.


### Aggregates

| Metric | Baseline | Agent |
|---|---|---|
| Cases attempted | 12/12 | 2/12 |
| Residual flake rate zero (verified) | 10/12 | 1/2 of attempted |
| Root cause identified | 12/12 | 1/2 of attempted |
| Total tokens | 79,455 | 45,587 |

Agent outcomes: **1 PENDING** approval, **1 UNRESOLVED**,
9 blocked by API quota, 1 excluded for runtime.
Patches rejected by the anti-cheat validator and re-authored: **1**.

Baseline model `gemini-3.6-flash`; agent model(s) `gemini-3.6-flash`.
Where these differ, the two arms are **not** directly comparable on those rows —
see `DECISIONS.md` D-013.


## The comparison that matters

### Claimed versus verified

The baseline returned a patch for **12/12** cases —
it asserts a fix every time, and reports a confidence with it.
Re-running each patch 500 times shows **10** of those
12 actually reached zero failures.

| Case | Baseline confidence | Residual after its fix | Actually fixed? |
|---|---|---|---|
| 01 race condition | high | 0.00% (unsound — every run errored) | **no** |
| 02 test order dependency | high | 0.00% | yes |
| 03 port collision | high | 0.00% | yes |
| 04 clock dependence | high | 0.00% | yes |
| 05 hash iteration order | high | 0.00% | yes |
| 06 unseeded randomness | high | 0.00% | yes |
| 07 network timeout | high | 0.80% | **no** |
| 08 tempfile collision | high | 0.00% | yes |
| 09 float tolerance | high | 0.00% | yes |
| 10 async ordering | high | 0.00% | yes |
| 11 cache leak | high | 0.00% | yes |
| 12 masking trap | high | 0.00% | yes |

**2 of 12 confident fixes were not fixes.**
The baseline had no way to tell which. Every one of them was returned
with the same kind of confidence as the ones that worked, because a
system that never executes the test has nothing to distinguish them by.

**Anchor case — 07 network timeout.** The baseline identified the root cause correctly, reported `high` confidence, and produced a patch that still fails **0.80%** of the time. That is a handful of failures in 500 runs — invisible to one execution, and exactly the kind of residual flakiness that gets a test re-run rather than fixed. The agent reached the same case and declined to declare success, which is the correct answer where a false green is the failure mode.

