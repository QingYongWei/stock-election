param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$bundledPython = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
if (Test-Path $localPython) { $python = $localPython }
elseif (Test-Path $bundledPython) { $python = $bundledPython }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = 'python' }
else { throw '未找到Python。请安装Python 3.11+，并运行 pip install -r requirements-agent.txt。' }
Write-Host "创作罗盘（暂名）v0.3 http://127.0.0.1:$Port — 保留此窗口运行，Ctrl+C停止。"
& $python -m stock_agent.server --port $Port
exit $LASTEXITCODE
