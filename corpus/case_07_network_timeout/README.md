# case_07_network_timeout

Single-attempt fetch against a tight timeout.

**Root cause class:** `network_timeout_no_retry`
**Difficulty:** medium
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

The service takes 4.6ms and the client waits 5ms. Ordinary scheduler jitter pushes the reply past the deadline some of the time.

## The real fix

`add_retry_with_backoff` -- Retry `fetch_status` on timeout with a short exponential backoff and a bounded attempt count.

Expected to touch: `app/client.py`

## The tempting wrong fix

Raise the timeout until failures get rare. The call still has no retry, so a slow peer fails outright in production.

## Baseline flake rate

**5.8%** (29/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:15:39Z.

## Notes

The contrast with case 12: here a retry IS the correct fix, because the root cause is the absence of one. A system that treats 'added a retry' as inherently suspect gets this wrong.
