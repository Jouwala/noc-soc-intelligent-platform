<#
  sync_from_main.ps1 - thin wrapper around the transactional Node sync engine.
  Source repo is READ-ONLY; the portable copy is the only write target.
  The real logic lives in sync-engine.mjs + sync-policy.mjs (zero npm deps,
  runs on the copy's bundled runtime\node\node.exe).

  Legacy switches -NoBackup / -NoDist / -SkipLocalFix are accepted but ignored:
  the new engine ALWAYS backs up before writing, NEVER syncs build artifacts
  (dist is rebuilt locally), and ALWAYS re-derives the two monitor script paths.

  Usage:
    powershell -NoProfile -File ops\deploy\sync_from_main.ps1 -DryRun
    powershell -NoProfile -File ops\deploy\sync_from_main.ps1 -AcceptSource
#>
[CmdletBinding()]
param(
    [string]$MainRepo = 'D:\01_RPA',
    [switch]$DryRun,
    [switch]$AcceptSource,
    [switch]$Quiet,
    [switch]$NoPause,
    [switch]$NoBackup,
    [switch]$NoDist,
    [switch]$SkipLocalFix
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$Node = Join-Path $Root 'runtime\node\node.exe'
$Engine = Join-Path $PSScriptRoot 'sync-engine.mjs'

if (-not (Test-Path -LiteralPath $Node)) { Write-Host "[ABORT] missing bundled node: $Node" -ForegroundColor Red; exit 2 }
if (-not (Test-Path -LiteralPath $Engine)) { Write-Host "[ABORT] missing sync engine: $Engine" -ForegroundColor Red; exit 2 }
if ($NoBackup -or $NoDist -or $SkipLocalFix) {
    Write-Host '[WARN] -NoBackup/-NoDist/-SkipLocalFix are no longer supported; the engine always backs up, never syncs dist, and always fixes monitor paths.' -ForegroundColor Yellow
}

$nodeArgs = @($Engine, '--source', $MainRepo, '--target', $Root)
if ($DryRun) { $nodeArgs += '--dry-run' }
if ($AcceptSource) { $nodeArgs += '--accept-source' }
if ($Quiet) { $nodeArgs += '--quiet' }

& $Node @nodeArgs
$rc = $LASTEXITCODE
exit $rc
