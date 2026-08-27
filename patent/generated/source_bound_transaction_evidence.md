# Source-Bound Causal Transaction: Controlled Ablation Evidence

## Scope

This report isolates the source-bound causal transaction from the complete Jira Enhancer B3 system. It is controlled fault-injection evidence, not a claim about external-customer production outcomes.

- Random seed: `20260726`
- Trials per scenario: `2000`
- Logical transactions per method: `18000`
- Total method-level observations: `72000`
- Paired design: every method receives the same transaction payload and fault schedule.

## Compared Arrangements

1. **A0 Ordinary approval + log:** checks approval/workflow eligibility and records the result.
2. **A1 Source-hash conditional write:** A0 plus a content-hash precondition.
3. **A2 Source hash + atomic idempotency:** A1 plus an atomic operation key, representing the strongest control baseline.
4. **A3 Source-bound causal transaction:** binds source hash and revision, approved draft version/hash, policy snapshot, target set, reviewer decision, and idempotent operation.

## Aggregate Results

| Method | Stale mutation | Duplicate mutation | Any unsafe mutation | Eligible write success | Correct outcome |
|---|---:|---:|---:|---:|---:|
| A0_ordinary_approval_log | 66.67% | 33.33% | 88.89% | 33.33% | 11.11% |
| A1_source_hash_conditional | 44.44% | 22.22% | 66.67% | 33.33% | 33.33% |
| A2_source_hash_atomic_idempotency | 44.44% | 0.00% | 44.44% | 100.00% | 55.56% |
| A3_source_bound_causal_transaction | 0.00% | 0.00% | 0.00% | 100.00% | 100.00% |

## Residual Technical Effect

Against A2, the strongest baseline, A3 reduced stale mutations from 44.44% to 0.00% without reducing valid-control write success. The A2 failures occurred in: source_aba, draft_drift, policy_drift, target_drift.

The residual gain comes from cross-artifact causal binding rather than source hashing alone. A content hash cannot detect an ABA revision, and source-bound conditional writes do not detect a regenerated draft, changed policy, or changed target when the Jira source text itself is unchanged.

## Paired Inference

| Baseline | Metric | Risk reduction | 95% paired CI | McNemar discordance | log10(p) |
|---|---|---:|---:|---:|---:|
| A0_ordinary_approval_log | stale_mutation | 0.6667 | [0.6598, 0.6736] | 12000 / 0 | -3612.06 |
| A0_ordinary_approval_log | duplicate_mutation | 0.3333 | [0.3264, 0.3402] | 6000 / 0 | -1805.88 |
| A0_ordinary_approval_log | unsafe_mutation | 0.8889 | [0.8843, 0.8935] | 16000 / 0 | -4816.18 |
| A1_source_hash_conditional | stale_mutation | 0.4444 | [0.4372, 0.4517] | 8000 / 0 | -2407.94 |
| A1_source_hash_conditional | duplicate_mutation | 0.2222 | [0.2161, 0.2283] | 4000 / 0 | -1203.82 |
| A1_source_hash_conditional | unsafe_mutation | 0.6667 | [0.6598, 0.6736] | 12000 / 0 | -3612.06 |
| A2_source_hash_atomic_idempotency | stale_mutation | 0.4444 | [0.4372, 0.4517] | 8000 / 0 | -2407.94 |
| A2_source_hash_atomic_idempotency | duplicate_mutation | 0.0000 | [0.0000, 0.0000] | 0 / 0 | 0.00 |
| A2_source_hash_atomic_idempotency | unsafe_mutation | 0.4444 | [0.4372, 0.4517] | 8000 / 0 | -2407.94 |

## Interpretation Boundary

The result demonstrates the mechanism under declared, repeatable fault schedules and supports a technical-effect argument. It does not establish a universal production effect or, by itself, a legal conclusion of non-obviousness. The strongest next evidence would repeat these fault classes through an instrumented Jira staging connector and record remote request identifiers, revisions, and mutation counts.
