# case_03_port_collision

Port is probed, released, then bound -- a TOCTOU window.

**Root cause class:** `resource_leak_port_collision`
**Difficulty:** medium
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

Two services scan the same fixed range concurrently. Each closes its probe before binding, so both can see the same port free and race to claim it.

## The real fix

`hold_the_resource_between_check_and_use` -- Do not probe and release. Bind the real socket to port 0 and keep it, reading the assigned port from `getsockname()`.

Expected to touch: `app/net.py`

## The tempting wrong fix

Retry the bind on EADDRINUSE, or widen the scan range so collisions get rarer without becoming impossible.

## Baseline flake rate

**38.8%** (194/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:13:34Z.
