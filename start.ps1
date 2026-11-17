param([int]$Port = 8765)
# Keep this launcher ASCII-only for Windows PowerShell 5.1 code-page compatibility.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$bundledPython = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
if (Test-Path $localPython) { $python = $localPython }
elseif (Test-Path $bundledPython) { $python = $bundledPython }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = 'python' }
else { throw 'Python 3.11+ was not found. Install Python and run: python -m pip install -r requirements-agent.txt.' }
Write-Host "Content Compass v0.3: http://127.0.0.1:$Port (Keep this window open; Ctrl+C to stop.)"
& $python -m stock_agent.server --port $Port
exit $LASTEXITCODE
