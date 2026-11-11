# Remediation and verification (Phases 6–7; RQ6)

**Remediation** (`pipeline/remediation/catalogue.py`): each bottleneck class maps to one fix,
the reviewed target-API variant that implements it, and the metric that must improve.

| Class | Fix | Variant | Must improve |
|---|---|---|---|
| DB_BOTTLENECK | add the missing index | `noindex-fixed` | database time and share ↓ |
| N_PLUS_ONE | one JOIN / batched query | `nplus1-fixed` | queries per request, repeated query ↓ |
| LARGE_PAYLOAD | pagination + field selection | `payload-fixed` | response size ↓ |
| CACHE_OPPORTUNITY | stable cache key + TTL | `cache-fixed` | hit ratio ↑, queries ↓ |
| CPU_BOTTLENECK | remove the expensive per-request computation | `cpu-fixed` | app time, event-loop delay ↓ |

`INSUFFICIENT_EVIDENCE` → no fix ("collect more evidence"). A fix is applied by switching the
operation to its variant with the same parameters. The system never edits code.

**Verification** (`pipeline/verification/stats.py`). Runs are independent, so groups are
compared unpaired on log server-side run p50 (protocol amendment of 11 Nov 2026):
- **Improved:** one-sided Welch t-test, Holm-corrected across pairs, α 0.05. The exact
  Mann-Whitney test is reported as a check. With 5 vs 3 runs its smallest possible p is
  1/56 ≈ 0.018, too coarse for a Holm correction over 10 pairs.
- **Back to healthy:** TOST. The 90 % CI of fixed/healthy must lie within ±10 %.
- **Mechanism:** the targeted metric moved the expected way.

```sh
python -m evaluation.verification --campaign <main-id>      # all fixed variants of the campaign + recommendation accuracy
python -m campaign.loop --operation users.list --variant nplus1:20 --baseline-campaign <main-id>   # closed-loop demo
```

The closed loop (`experiments/campaign/loop.py`) injects a fault the system is not told
about, then runs: detect → diagnose → recommend → apply the fix → re-run → verify. Its report
is in `data/results/loops/<loop-id>/`.

**Pilot (development check):**
- The recommended fix matches the injected fault in 20/20 runs.
- Both fixes improved significantly: reports cache ×0.010, p(Holm) 0.004; users.list N+1
  ×0.20, p 0.019.
- Neither was shown equivalent to healthy: with 2 runs per group the 90 % CIs were too wide.
