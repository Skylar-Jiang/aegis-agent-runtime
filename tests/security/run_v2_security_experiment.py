"""Backward-compatible entrypoint for the frozen V2 safety runner."""

from experiments.v2.runners.run_safety_evaluation import (
    DEFAULT_FIXTURE_PATH,
    SecurityCase,
    main,
    run_experiments,
    security_cases,
)

__all__ = [
    "DEFAULT_FIXTURE_PATH",
    "SecurityCase",
    "run_experiments",
    "security_cases",
]


if __name__ == "__main__":
    raise SystemExit(main())
