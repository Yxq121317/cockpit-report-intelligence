# 初始化主库（默认 data/p1.db；配置 P1_DATABASE_URL 后为 PostgreSQL）
# 并生成 8 张表（学生、教师、班级、年级、家长、出勤、请假、课程表）的示例数据。
# 用法：.\scripts\seed.ps1
#       .\scripts\seed.ps1 -Reset     # 删表重建，会清空当前主库的全部表
param(
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    $VenvPython = 'python'
}

$SeedArgs = @('-m', 'app.seed.seed')
if ($Reset) { $SeedArgs += '--reset' }

Push-Location (Join-Path $Root 'backend')
try {
    & $VenvPython @SeedArgs
    if ($LASTEXITCODE -ne 0) { throw '种子数据初始化失败。' }
} finally {
    Pop-Location
}

Write-Host '8 张表与示例数据已就绪；无需登录，直接启动前端即可提问。' -ForegroundColor Green
