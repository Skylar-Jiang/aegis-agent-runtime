import subprocess
import sys


def main() -> int:
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ra_agent.main:app",
            "--reload",
            "--app-dir",
            "backend/src",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
