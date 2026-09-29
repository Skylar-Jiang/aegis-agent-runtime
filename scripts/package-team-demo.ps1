[CmdletBinding()]
param(
  [string]$OutputPath
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
  $OutputPath = Join-Path $projectRoot "Aegis-Runtime-Base-v0.4-Conversation.zip"
}
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $outputFullPath
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("aegis-runtime-base-" + [guid]::NewGuid())
$packageRoot = Join-Path $stagingRoot "Aegis-Runtime-Base"

$excludedDirectories = @(
  ".git", ".agents", ".playwright-cli", ".pytest_cache", ".ruff_cache", ".pyright",
  ".runtime", ".venv", "node_modules", "dist", "coverage", "test-results",
  "playwright-report", "__pycache__"
)
$excludedFiles = @(
  ".env", "*.pyc", "*.pyo", "*.log", "*.db", "*.db-shm", "*.db-wal",
  "*.sqlite", "*.sqlite3", "*.tsbuildinfo", "*.zip"
)

New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

try {
  $copyArguments = @($projectRoot, $packageRoot, "/E", "/XD") + $excludedDirectories + @("/XF") + $excludedFiles + @("/NFL", "/NDL", "/NJH", "/NJS")
  & robocopy @copyArguments | Out-Null
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed with exit code $LASTEXITCODE"
  }

  if (Test-Path -LiteralPath $outputFullPath) {
    Remove-Item -LiteralPath $outputFullPath -Force
  }
  Compress-Archive -Path $packageRoot -DestinationPath $outputFullPath -Force
  Write-Host "Created $outputFullPath"
}
finally {
  $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
  $stagingFullPath = [System.IO.Path]::GetFullPath($stagingRoot)
  if ($stagingFullPath.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $stagingFullPath -Recurse -Force
  }
}
