# DTLM v3.1 patch summary

- M=1.0 guardrail fixed: yes.
- Low-M regression on 0.1~0.3: no.
- Average rank moved from 2.50 to 2.33 (上升).
- M=1.0 cost: v3=2271460, v3.1=1181620, GDSF=1249850.
- M=1.0 TTL physical reclaims: v3=7449, v3.1=3652.
- v3.1 gain source: the patch only changed low-pressure physical deletion, so any high-M improvement comes from suppressing low-pressure expiry removals rather than changing GDSF eviction.
- tau saturation issue: still present / unchanged. v3 diagnosis showed no tau_base exists and 80.0% of functions ended at the cold-floor TTL; v3.1 did not change that logic.

## Per-M comparison
- M=0.1: v3.1 cost=57691770, v3 cost=57691770, GDSF cost=57691770, rank=4
- M=0.2: v3.1 cost=49093970, v3 cost=49082700, GDSF cost=51196200, rank=3
- M=0.3: v3.1 cost=12555790, v3 cost=12590140, GDSF cost=16284490, rank=2
- M=0.5: v3.1 cost=4097390, v3 cost=4167280, GDSF cost=7666250, rank=2
- M=0.7: v3.1 cost=2032850, v3 cost=2348780, GDSF cost=2907560, rank=1
- M=1.0: v3.1 cost=1181620, v3 cost=2271460, GDSF cost=1249850, rank=2