# Member 3 V2 rollback benchmark: phases A and B

This package adds the first formal deterministic rollback fixtures:

- M3-C01: File selective rollback; one committed file is preserved.
- M3-C02: Memory selective rollback; previous TRUSTED version is restored.
- M3-C07: Download quarantine rollback; independent download is preserved.

Only `ADAPTIVE_RUNTIME` is implemented in phases A/B. The runner fails closed for
`BASELINE` and `FULL_GUARD` until the group freezes their rollback semantics.

Formal outputs:

- `experiments/v2/results/raw/rollback_benchmark.jsonl`
- `experiments/v2/results/raw/rollback_benchmark.csv`

The runner writes JSONL first and then writes the same ExperimentResult fields to CSV.
It uses deterministic local storage only. The Download fixture does not make a network
connection.
