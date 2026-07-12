$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$required = (Get-Content -Raw (Join-Path $root ".nvmrc")).Trim()

if (-not (Get-Command nvm -ErrorAction SilentlyContinue)) {
    Write-Error "nvm-windows is required. Install it, then run this script again."
    exit 1
}

$escapedVersion = [regex]::Escape($required)
$installed = (& nvm list 2>&1 | Out-String)
$listExitCode = $LASTEXITCODE
if ($listExitCode -ne 0) {
    Write-Error "nvm list failed with exit code ${listExitCode}: $installed"
    exit $listExitCode
}
if ($installed -notmatch "(?m)^\s*\*?\s*$escapedVersion(?:\s|$)") {
    Write-Error "Node.js $required is not installed. Run: nvm install $required"
    exit 1
}

& nvm use $required
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$actual = (& node --version).Trim().TrimStart("v")
if ($actual -ne $required) {
    Write-Error "Node.js version mismatch. Required: $required; Current: $actual"
    exit 1
}

Write-Host "Using Node.js v$actual"
