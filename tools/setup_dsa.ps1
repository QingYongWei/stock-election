<#
.SYNOPSIS
Explicit, opt-in installation of real upstream DSA in vendor/.venv-dsa.
.DESCRIPTION
Run from a trusted terminal only. Does not run DSA, inspect credentials, or
capture installer output for Web. Installation uses the chosen upstream's
requirements.txt (including its third-party GitHub wheel); review it first.
Creates .env by copying .env.example ONLY if .env does not already exist.
Then edit .env locally with your own model credentials; never commit or send it.
Upstream Python 3.10+ is recommended. The host Python environment is not changed.
.EXAMPLE
pwsh -File tools/setup_dsa.ps1 -Python C:\Python311\python.exe
.EXAMPLE
pwsh -File tools/setup_dsa.ps1 -Repo C:\src\daily_stock_analysis -Python python -InstallConfirmed
# -InstallConfirmed is the caller's explicit acknowledgement of network install.
# Then: python tools/run_dsa.py --stocks 600519,hk00700,AAPL --timeout 600
# Point host dsa_report_dir at <Repo>/reports (UTF-8 .md, not JSON).
.NOTES
The bridge preserves upstream .env loading. In audited c820915, dotenv uses
 override=False and bootstrap overrides protect SCHEDULE_ENABLED=false and
 RUN_IMMEDIATELY=true from file-priority configuration. Re-audit other revisions.
Run bridge only in CLI, not through a Web task runner which captures raw logs.
The bridge blanks FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_FOLDER_TOKEN and
disables bot streams in its child env as --no-notify alone does not guard
upstream's separately configured Feishu cloud-document creation.
#>
[CmdletBinding()]
param(
    [string]$Repo,
    [string]$Python = 'python',
    [switch]$InstallConfirmed
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root 'vendor'
if (-not $Repo) {
    $candidates = @(Get-ChildItem -LiteralPath $vendor -Directory -Filter 'daily_stock_analysis*' | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'main.py') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'requirements.txt') -PathType Leaf)
    })
    if ($candidates.Count -ne 1) { throw 'Specify -Repo: expected exactly one DSA checkout in vendor.' }
    $Repo = $candidates[0].FullName
}
$repoPath = (Resolve-Path -LiteralPath $Repo).Path
$requirements = Join-Path $repoPath 'requirements.txt'
$template = Join-Path $repoPath '.env.example'
$envPath = Join-Path $repoPath '.env'
if (-not (Test-Path -LiteralPath (Join-Path $repoPath 'main.py') -PathType Leaf) -or
    -not (Test-Path -LiteralPath $requirements -PathType Leaf) -or
    -not (Test-Path -LiteralPath $template -PathType Leaf)) {
    throw '-Repo must contain real upstream main.py, requirements.txt and .env.example.'
}
$pythonCommand = Get-Command -Name $Python -CommandType Application -ErrorAction Stop
$basePython = $pythonCommand.Source
$venv = Join-Path $vendor '.venv-dsa'
$venvPython = Join-Path $venv 'Scripts/python.exe'
Write-Host "DSA source: $repoPath"
Write-Host "Independent environment: $venv"
Write-Host 'This explicitly installs third-party dependencies over the network and copies .env.example only if .env is absent.'
if (-not $InstallConfirmed) {
    $answer = Read-Host 'Type INSTALL to confirm, or anything else to cancel'
    if ($answer -cne 'INSTALL') { throw 'Cancelled: no installation or configuration changes made.' }
}

# Inherit stdout/stderr directly; never capture potential installer secrets.
& $basePython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'DSA requires Python 3.10+; select a suitable -Python executable.' }
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $venv) {
        throw 'vendor/.venv-dsa already exists without Scripts/python.exe; inspect it manually (nothing is deleted).'
    }
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create independent DSA venv.' }
}
# Verify that the interpreter belongs to the expected independent venv.
& $venvPython -c 'import pathlib,sys; expected=pathlib.Path(sys.argv[1]).resolve(); sys.exit(0 if sys.prefix != sys.base_prefix and pathlib.Path(sys.prefix).resolve() == expected else 1)' $venv
if ($LASTEXITCODE -ne 0) { throw 'DSA interpreter is not the expected independent venv; installation refused.' }
Push-Location -LiteralPath $repoPath
try {
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'DSA dependency installation failed; setup is incomplete.' }
} finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath $envPath)) {
    # overwrite=false also protects an .env created concurrently after the check.
    [System.IO.File]::Copy($template, $envPath, $false)
    Write-Host 'Created upstream .env from its public template; edit it locally with your own model settings.'
} else {
    Write-Host 'Existing upstream .env preserved without reading its contents.'
}
Write-Host 'Setup finished. No analysis has been run. Do not commit .env or expose raw CLI logs to Web.'
Write-Host "Run: python tools/run_dsa.py --repo `"$repoPath`" --stocks 600519,hk00700,AAPL --timeout 600"
Write-Host "Set host dsa_report_dir to: $(Join-Path $repoPath 'reports')"
