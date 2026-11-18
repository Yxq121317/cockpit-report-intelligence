# 一键启动后端 8001 与前端 5171。
# 用法：.\scripts\dev.ps1              # 默认 mock，完全离线
#       .\scripts\dev.ps1 -Mode real  # 使用 .env 中的真实 Key
#       .\scripts\dev.ps1 -Mode auto  # 有 Key 用真实，否则自动 mock
param(
    [ValidateSet('mock', 'auto', 'real')]
    [string]$Mode = 'mock'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    $VenvPython = 'python'
}

$BackendCmd = @"
`$env:LLM_MODE = '$Mode'
Set-Location '$Root\backend'
& '$VenvPython' -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
"@

$FrontendCmd = @"
Set-Location '$Root\frontend'
npm run dev
"@

Write-Host "启动后端 http://localhost:8001（LLM_MODE=$Mode）" -ForegroundColor Cyan
Start-Process powershell -ArgumentList '-NoExit', '-Command', $BackendCmd | Out-Null

Write-Host '启动前端 http://localhost:5171' -ForegroundColor Cyan
Start-Process powershell -ArgumentList '-NoExit', '-Command', $FrontendCmd | Out-Null

Write-Host ''
Write-Host '两个窗口已打开；等待 Vite 输出后访问 http://localhost:5171 。' -ForegroundColor Green
Write-Host '无需登录，打开页面即可用中文提问。' -ForegroundColor Green
