"""Backward-compatible entrypoint for frozen V2 safety report materials."""

from experiments.v2.runners.build_safety_materials import (  # pyright: ignore[reportMissingImports]
    build_materials,
    main,
)

__all__ = ["build_materials"]


if __name__ == "__main__":
    raise SystemExit(main())
