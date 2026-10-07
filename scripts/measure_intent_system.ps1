param([int]$BackendPid, [int]$FrontendPid, [string]$Output)
$ErrorActionPreference = 'Stop'
$browserHelper = Start-Process -FilePath 'C:\Program Files\nodejs\node.exe' -ArgumentList 'scripts/intent_resource_browser.mjs' -WindowStyle Hidden -PassThru -RedirectStandardOutput '.runtime/intent-resource-browser.log' -RedirectStandardError '.runtime/intent-resource-browser-error.log'
$roots = @($BackendPid, $FrontendPid, $browserHelper.Id)
$samples = @()
try {
  for ($sampleIndex = 0; $sampleIndex -lt 60; $sampleIndex++) {
    $processInventory = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($rootId in $roots) { [void]$ids.Add($rootId) }
    do {
      $added = $false
      foreach ($processInfo in $processInventory) {
        if ($ids.Contains([int]$processInfo.ParentProcessId) -and $ids.Add([int]$processInfo.ProcessId)) { $added = $true }
      }
    } while ($added)
    $rows = @()
    foreach ($targetId in $ids) {
      $processObject = Get-Process -Id $targetId -ErrorAction SilentlyContinue
      if ($processObject) { $rows += @{pid=$targetId;name=$processObject.ProcessName;working_set_bytes=$processObject.WorkingSet64;private_bytes=$processObject.PrivateMemorySize64} }
    }
    $samples += @{second=$sampleIndex;phase=$(if ($sampleIndex -lt 15) {'warm_idle'} elseif ($sampleIndex -lt 45) {'eight_concurrent_health_reads'} else {'post_load'});processes=$rows;working_set_bytes=($rows | Measure-Object working_set_bytes -Sum).Sum;private_bytes=($rows | Measure-Object private_bytes -Sum).Sum}
    Start-Sleep -Seconds 1
  }
  @{scope='Owned backend, Vite, Chrome and their child processes';roots=$roots;duration_seconds=60;peak_working_set_bytes=($samples | Measure-Object working_set_bytes -Maximum).Maximum;peak_private_bytes=($samples | Measure-Object private_bytes -Maximum).Maximum;samples=$samples;official_500M_gate='pending official scope/unit confirmation';limitations=@('warm development services; no cold-start measurement','60-second sample is not a long-run soak','concurrency tests health reads, not eight simultaneous planning tasks','no external model process in scope')} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Output -Encoding utf8
} finally {
  if (-not $browserHelper.HasExited) { Wait-Process -Id $browserHelper.Id -Timeout 20 -ErrorAction SilentlyContinue }
}
