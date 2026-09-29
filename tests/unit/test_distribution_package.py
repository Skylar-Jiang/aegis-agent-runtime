from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_base_distribution_has_a_safe_one_click_launcher() -> None:
    launcher = ROOT / "START_RUNTIME_BASE.bat"
    assert launcher.exists()
    content = launcher.read_text(encoding="utf-8")

    assert "py -3.11" in content
    assert "NODE_MAJOR" in content
    assert "NODE_MINOR" in content
    assert "uv sync --project backend --group dev --locked" in content
    assert "corepack pnpm install --frozen-lockfile" in content
    assert "set RUNTIME_MODE=live-agent" in content
    assert "LLM_API_KEY" in content


def test_legacy_launchers_delegate_to_runtime_base() -> None:
    for name in ("START_DEMO.bat", "start-dev.bat"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "START_RUNTIME_BASE.bat" in content


def test_runtime_base_has_a_deterministic_windows_validation_launcher() -> None:
    launcher = ROOT / "RUN_BASE_EXPERIMENTS.bat"
    assert launcher.exists()
    content = launcher.read_text(encoding="utf-8")

    assert "verify_runtime_base.py" in content
    assert "base-validation.json" in content
    assert "pnpm typecheck" in content
    assert "pnpm build" in content


def test_team_demo_packaging_excludes_credentials_and_runtime_residue() -> None:
    packager = ROOT / "scripts" / "package-team-demo.ps1"
    assert packager.exists()
    content = packager.read_text(encoding="utf-8")

    for excluded in (".env", ".runtime", "node_modules", ".venv", ".git"):
        assert excluded in content
