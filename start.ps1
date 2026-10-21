# ============================================================
# AI 数字获客系统 · 一键启动脚本
#
# 用法（PowerShell，在项目根目录执行）：
#   .\start.ps1            # 基础模式：基建(PG/Redis) + API 服务
#   .\start.ps1 -Full      # 完整模式：基础模式 + 两个 Celery worker
#   .\start.ps1 -Demo      # 演示模式：跑 GEO 报告 + 视频生成（不启动服务）
#   .\start.ps1 -Stop      # 停止所有容器
# ============================================================
param(
    [switch]$Full,
    [switch]$Demo,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$venvScripts = Join-Path $root ".venv\Scripts"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------- 停止模式 ----------
if ($Stop) {
    Write-Step "停止容器"
    docker compose -f "$root\docker-compose.yml" down
    Write-Host "已停止。uvicorn/celery 窗口请直接关闭。" -ForegroundColor Green
    exit 0
}

# ---------- 前置检查 ----------
Write-Step "检查环境"
if (-not (Test-Path $venvPy)) {
    Write-Host "未找到虚拟环境，正在创建..." -ForegroundColor Yellow
    python -m venv "$root\.venv"
    & "$venvScripts\pip.exe" install -q -e "$root[dev]"
}

# .env 不存在则从模板复制（默认 mock 配置，开箱即用）
if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "已从 .env.example 生成 .env（当前为 MOCK 模式，无需 API key）" -ForegroundColor Yellow
}

$env:PYTHONPATH = Join-Path $root "src"

# ---------- 演示模式 ----------
if ($Demo) {
    Write-Step "运行 GEO 跟踪演示"
    & $venvPy "$root\scripts\run_geo_track.py"
    Write-Step "运行视频生成演示（首次会下载 whisper 模型）"
    $env:HF_ENDPOINT = "https://hf-mirror.com"
    $env:HF_HUB_DISABLE_XET = "1"
    & $venvPy "$root\scripts\make_demo_video.py"
    Write-Host "`n完成！产物在 output\ 目录" -ForegroundColor Green
    exit 0
}

# ---------- 启动基础设施 ----------
Write-Step "启动 PostgreSQL + Redis"
$dockerOk = $false
try {
    docker compose -f "$root\docker-compose.yml" up -d | Out-Null
    $dockerOk = $true
    Write-Host "容器已启动（PG: localhost:5432, Redis: localhost:6379）" -ForegroundColor Green
} catch {
    Write-Host "Docker 不可用或未启动，跳过容器（API 仍可启动，涉及 DB 的接口会报错）" -ForegroundColor Yellow
}

if ($dockerOk) {
    Write-Step "等待数据库就绪"
    $retry = 0
    while ($retry -lt 30) {
        $ready = docker exec (docker ps -qf "name=postgres") pg_isready -U aigc 2>$null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2; $retry++
    }
    Write-Host "数据库就绪" -ForegroundColor Green
}

# ---------- 启动 Celery（-Full 模式） ----------
if ($Full) {
    Write-Step "启动 Celery workers（新窗口）"
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$root'; `$env:PYTHONPATH='$root\src'; .\.venv\Scripts\celery -A aigc_growth.celery_app.celery_app worker -Q io_queue -c 5 --pool=solo -n io@%h"
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$root'; `$env:PYTHONPATH='$root\src'; .\.venv\Scripts\celery -A aigc_growth.celery_app.celery_app worker -Q cpu_queue -c 2 --pool=solo -n cpu@%h"
    Write-Host "io_queue / cpu_queue worker 已在新窗口启动" -ForegroundColor Green
}

# ---------- 启动 API ----------
Write-Step "启动 API 服务"

# 清理残留实例，避免端口冲突与「改了 .env 不生效」的假象：
# uvicorn --reload 会派生子进程，子进程继承监听套接字句柄，
# 只按端口 PID 杀不够——子进程存活时端口依然被占。故按命令行整体清。
$stale = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like '*aigc_growth*' }
if ($stale) {
    $stale | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "已清理残留服务进程 (PID $($_.ProcessId))" -ForegroundColor Yellow
    }
    # 再清掉孤立占用 8000 的进程（含继承了句柄的子进程）
    netstat -ano | Select-String ":8000\s+.*LISTENING" | ForEach-Object {
        $procId = ($_ -split '\s+')[-1]
        Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}
Write-Host @"

  API 文档:  http://127.0.0.1:8000/docs
  健康检查:  http://127.0.0.1:8000/healthz
  当前模式:  $(if ((Get-Content "$root\.env" | Select-String "MOCK_EXTERNAL=true") ) { "MOCK（无需 key）" } else { "真实服务" })

  按 Ctrl+C 停止服务
"@ -ForegroundColor Green

& "$venvScripts\uvicorn.exe" aigc_growth.api.main:app --reload --host 127.0.0.1 --port 8000
