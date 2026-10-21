# AI 数字获客系统（AIGC 内容生产与分发全链路）

选题 → 文案 → 视频生成 → 多平台发布 → 数据回流反哺 → GEO 可见度跟踪

> 规格文档见 [数字获客系统-项目全案.md](./数字获客系统-项目全案.md)，本仓库是其代码实现。

## 快速开始

**一键启动（Windows PowerShell）：**

```powershell
.\start.ps1          # 基建 + API（默认 mock，无需 key）
.\start.ps1 -Full    # 再加两个 Celery worker
.\start.ps1 -Demo    # 跑 GEO 报告 + 视频生成演示
.\start.ps1 -Stop    # 停止容器
```

**手动分步：**

```bash
# 1) 基础设施（PG+pgvector / Redis）
docker compose up -d

# 2) 安装依赖
pip install -e ".[dev]"
copy .env.example .env        # 默认 MOCK_EXTERNAL=true，无 key 可跑

# 3) 跑测试
pytest

# 4) GEO 跟踪演示（产出 output/geo_report.md）
python scripts/run_geo_track.py

# 5) 视频合成演示（产出真实 mp4；首次跑会下载 whisper tiny 模型）
python scripts/make_demo_video.py
# 国内网络需设置镜像（PowerShell）：
#   $env:HF_ENDPOINT="https://hf-mirror.com"; $env:HF_HUB_DISABLE_XET="1"

# 6) API 服务
uvicorn aigc_growth.api.main:app --reload
# Swagger: http://127.0.0.1:8000/docs

# 7) Celery worker（双队列）
celery -A aigc_growth.celery_app.celery_app worker -Q io_queue -c 10
celery -A aigc_growth.celery_app.celery_app worker -Q cpu_queue -c 3
```

## 切换真实服务

编辑 `.env`：`MOCK_EXTERNAL=false` 并填入对应 key（LLM / Perplexity / 硅基智能 / 抖音开放平台）。key 留空的模块自动继续走 mock。

## 目录结构

```
src/aigc_growth/
├── config.py / db.py / celery_app.py     # 配置 / 会话 / 双队列(采集IO vs 渲染CPU)
├── models/                                # content_tasks, video_assets, geo_*, video_snapshots, comments
├── geo/                                   # ⭐ GEO 跟踪：多引擎采样/多次采样统计/信源缺口
├── content/                               # LangGraph 工作流 + 结构化输出 + LLM-as-judge + GEO规则
├── avatar/                                # 数字人抽象层(Protocol) / 硅基智能 / mock
├── video/                                 # edge-tts / faster-whisper 字幕 / FFmpeg 合成
├── collector/                             # 抖音官方API / 衰减式采样 / 评论打标脱敏
├── publisher/                             # API 主通道 + Playwright 兜底
├── tasks/                                 # Celery 任务（幂等/降级/分级重试）
└── api/                                   # FastAPI
scripts/   run_geo_track.py, make_demo_video.py
tests/     geo 指标 / 状态机 / 幂等 / 合规闸门
```

## 核心设计速查

| 设计点 | 位置 | 说明 |
|--------|------|------|
| 全链路状态机 | `content/state_machine.py` | 断点续跑、失败分级(retry/alert/dlq) |
| 幂等防重复计费 | `tasks/avatar_task.py` | content_id 去重，数字人按次计费 |
| 退避轮询 | `tasks/avatar_task.py` | 指数退避 ≤60s，总超时 900s |
| 降级不静默 | `tasks/avatar_task.py::_fallback` | 数字人失败→TTS混剪+告警 |
| 多次采样统计 | `geo/tracker.py` | 3-5 次采样取统计值，原始回答存档 |
| 信源缺口 | `geo/report.py::source_gap` | AI 引用域名 vs 自有域名 → 投放清单 |
| 衰减式采样 | `collector/scheduler.py` | 1h→6h→T+1→T+3→T+7→T+30→周归档 |
| 快照幂等 upsert | `collector/upsert.py` | (platform,item_id,stat_date) 唯一键 |
| 评论转线索 | `collector/comments.py` | 规则+LLM 打标，高意向推 CRM，PII 脱敏 |
| 双通道发布 | `publisher/douyin.py` | API 主通道 + Playwright 兜底 + 截图留证 |
| GEO 写作规则 | `content/geo_rules.py` | Prompt 固化 + 广告法词库闸门 |
| 模型分层路由 | `content/llm.py` | strong 重决策 / lite 批量生产 |
