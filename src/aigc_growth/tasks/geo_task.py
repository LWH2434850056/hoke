"""GEO 采样 Celery 任务（io_queue）：定时批量采样入口。"""
from __future__ import annotations

import logging

from aigc_growth.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="aigc_growth.tasks.geo_task.run_sampling")
def run_sampling(brand: str, competitors: list[str] | None = None) -> dict:
    """一轮 GEO 可见度采样（生产由 Celery beat 周期触发）。"""
    import asyncio

    from aigc_growth.config import get_settings
    from aigc_growth.db import SessionLocal
    from aigc_growth.geo import build_engines, run_track
    from aigc_growth.geo.prompts import render_seeds
    from aigc_growth.models.geo import GeoSample, GeoSampleRun

    s = get_settings()
    competitors = competitors or []
    engines = build_engines()
    prompts = render_seeds(brand, competitors)
    prompt_texts = [p.prompt for p in prompts if p.priority == "core"]  # 成本控制：核心词高频

    results = asyncio.run(
        run_track(engines, prompt_texts, brand, competitors,
                  n_samples=s.geo_samples_per_prompt, concurrency=s.geo_concurrency)
    )

    run_id_by_engine: dict[str, str] = {}
    with SessionLocal() as db:
        overall_m = sum(r.mention_rate * r.n_samples for r in results)
        overall_n = sum(r.n_samples for r in results)
        run_row = GeoSampleRun(
            brand=brand,
            competitors=competitors,
            mention_rate=overall_m / overall_n if overall_n else 0.0,
            citation_rate=0.0,
            metrics={"engines": {r.engine: r.to_dict() for r in results}},
            cost_total=sum(r.cost for r in results),
            status="done",
        )
        db.add(run_row)
        db.commit()

        from collections import defaultdict

        seq_by_engine: dict[str, int] = defaultdict(int)
        for r in results:
            run_id_by_engine[r.engine] = run_row.id
            for raw in r.raw:
                seq_by_engine[r.engine] += 1  # 同引擎多 prompt 时序号全 run 内递增，防唯一键冲突
                db.add(GeoSample(
                    run_id=run_row.id,
                    engine=r.engine,
                    sample_seq=seq_by_engine[r.engine],
                    answer=raw["answer"],            # 原始存档，可溯源
                    citations=raw.get("citations", []),
                    mentioned=raw["mentioned"],
                    position=raw.get("position"),
                    competitors_hit=raw.get("competitors", []),
                    cost=raw.get("cost"),
                ))
        db.commit()
        run_id = run_row.id

    logger.info("GEO 采样完成: run=%s mention=%.1f%%", run_id,
                (overall_m / overall_n * 100) if overall_n else 0)
    return {"run_id": run_id, "n_groups": len(results)}
