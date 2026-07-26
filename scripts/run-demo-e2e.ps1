param()

$ErrorActionPreference = 'Stop'
& "$PSScriptRoot\..\backend\.venv\Scripts\python.exe" -m pytest "$PSScriptRoot\..\tests\e2e\test_demo_scenarios.py" -q
