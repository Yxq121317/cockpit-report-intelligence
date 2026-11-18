# 一键安装后端与前端依赖（Windows PowerShell）。
# 用法：.\scripts\setup.ps1            # 安装依赖，已存在 .env 时不覆盖
#       .\scripts\setup.ps1 -Force     # 强制重新安装 Python 依赖
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw '未找到 python，请先安装 Python 3.12+ 并加入 PATH。'
}

$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Step '创建虚拟环境 .venv'
    python -m venv .venv
}

# 部分工具创建的 venv 不带 pip，这里补齐。
& $VenvPython -c "import pip" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step '安装 pip'
    & $VenvPython -m ensurepip --upgrade
}

if (-not (Test-Path (Join-Path $Root '.env'))) {
    if (Test-Path (Join-Path $Root '.env.example')) {
        Write-Step '从 .env.example 创建 .env（占位配置，可离线运行）'
        Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
    } else {
        Write-Host '提示：未找到 .env，程序会使用 config.py 中的默认值并以 mock 模式运行。' -ForegroundColor Yellow
    }
} else {
    Write-Host '检测到 .env，保留现有配置。' -ForegroundColor DarkGray
}

Write-Step '安装后端依赖'
$PipArgs = @('-m', 'pip', 'install', '-r', (Join-Path $Root 'requirements.txt'))
if ($Force) { $PipArgs += '--upgrade' }
& $VenvPython @PipArgs

Write-Step '安装前端依赖'
Push-Location (Join-Path $Root 'frontend')
try {
    npm install
} finally {
    Pop-Location
}

Write-Step '完成'
Write-Host '下一步：.\scripts\seed.ps1  然后  .\scripts\dev.ps1' -ForegroundColor Green
