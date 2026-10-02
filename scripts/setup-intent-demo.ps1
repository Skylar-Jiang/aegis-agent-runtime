param([switch]$SkipBrowser)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $repoRoot
try {
    $venvPython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $basePython = uv python find 3.11
        if ($LASTEXITCODE -ne 0) {
            uv python install 3.11
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 installation failed' }
            $basePython = uv python find 3.11
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 lookup failed' }
        }
        # Python's native venv avoids uv's PE trampoline creation on Windows.
        & $basePython -m venv backend/.venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
    }
    uv sync --project backend --group dev --frozen --python 3.11
    if ($LASTEXITCODE -ne 0) {
        # Locked pip fallback for Windows uv PE launcher failures. No lock changes.
        if (-not (Test-Path -LiteralPath $venvPython)) { throw 'uv did not create Python 3.11 environment' }
        & $venvPython -m ensurepip
        if ($LASTEXITCODE -ne 0) { throw 'ensurepip failed' }
        New-Item -ItemType Directory -Path '.runtime' -Force | Out-Null
        uv export --project backend --group dev --frozen --no-emit-project --output-file .runtime/intent-locked.txt | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependency export failed' }
        & $venvPython -m pip install --require-hashes -r .runtime/intent-locked.txt
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed' }
        & $venvPython -m pip install --no-deps -e backend
        if ($LASTEXITCODE -ne 0) { throw 'Editable backend installation failed' }
    }
    corepack pnpm --dir frontend install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    if (-not $SkipBrowser) {
        corepack pnpm --dir frontend exec playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed' }
    }
} finally { Pop-Location }
