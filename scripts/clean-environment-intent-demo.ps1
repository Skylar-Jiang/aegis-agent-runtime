param([int]$BackendPort = 8046, [int]$FrontendPort = 5176)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cleanRoot = Join-Path $repoRoot ('.runtime/member4-clean-' + [guid]::NewGuid().ToString('N'))
# A fresh checkout of the index includes the candidate staged changes before commit.
# Nothing is deleted or moved; dependencies, .env and runtime state are not copied.
New-Item -ItemType Directory -Path $cleanRoot -Force | Out-Null
$resolvedCleanRoot = (Resolve-Path -LiteralPath $cleanRoot).Path
$expectedParent = (Resolve-Path -LiteralPath (Join-Path $repoRoot '.runtime')).Path
if (-not $resolvedCleanRoot.StartsWith($expectedParent + [IO.Path]::DirectorySeparatorChar)) {
    throw 'Clean checkout must be inside this workspace .runtime directory'
}
Push-Location -LiteralPath $repoRoot
try {
    $checkoutPrefix = '--prefix=' + $resolvedCleanRoot.Replace('\', '/') + '/'
    git checkout-index --all $checkoutPrefix
    if ($LASTEXITCODE -ne 0) { throw 'Index checkout failed; stage the candidate first' }
    $sourceCommit = git rev-parse HEAD
    $sourceDiff = git diff --cached --binary
    [IO.File]::WriteAllText((Join-Path $resolvedCleanRoot 'candidate-source.txt'),
        "HEAD: $sourceCommit`nStaged diff:`n" + ($sourceDiff -join "`n"))
} finally { Pop-Location }
Push-Location -LiteralPath $resolvedCleanRoot
try {
    & (Join-Path $resolvedCleanRoot 'scripts/setup-intent-demo.ps1')
    if (-not (Test-Path -LiteralPath 'backend/.venv/Scripts/python.exe')) { throw 'Python environment missing' }
    & backend/.venv/Scripts/python.exe -m pytest tests/integration/test_intent_demo_api.py tests/unit/test_intent_demo_execution.py tests/contract/test_intent_demo_contracts.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Clean backend regression failed' }
    corepack pnpm --dir frontend typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Clean frontend typecheck failed' }
    corepack pnpm --dir frontend exec vitest run
    if ($LASTEXITCODE -ne 0) { throw 'Clean frontend tests failed' }
    corepack pnpm --dir frontend build
    if ($LASTEXITCODE -ne 0) { throw 'Clean frontend build failed' }
    & backend/.venv/Scripts/python.exe scripts/intent_demo.py e2e --backend-port $BackendPort --frontend-port $FrontendPort --output .runtime/member4-clean-evidence
    if ($LASTEXITCODE -ne 0) { throw 'Clean browser regression failed' }
    [IO.File]::WriteAllText((Join-Path $resolvedCleanRoot 'clean-result.json'),
        '{"status":"PASS","backend_tests":22,"frontend_tests":84,"browser_tests":7}')
    Write-Output "Clean environment PASS: $resolvedCleanRoot"
} finally { Pop-Location }
