"""GEO 跟踪演示脚本。

用法：
    python scripts/run_geo_track.py              # mock 模式
    MOCK_EXTERNAL=false python scripts/run_geo_track.py   # 真实 key
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_growth.config import get_settings  # noqa: E402
from aigc_growth.geo import (  # noqa: E402
    build_engines,
    render_markdown,
    run_track,
)
from aigc_growth.geo.prompts import render_seeds  # noqa: E402

BRAND = "示例品牌A"
COMPETITORS = ["竞品X", "竞品Y", "竞品Z"]


def main() -> None:
    s = get_settings()
    mode = "MOCK" if s.mock_external else "REAL"
    print(f"[GEO 跟踪] 模式={mode} 采样={s.geo_samples_per_prompt}次/prompt")

    prompts = render_seeds(BRAND, COMPETITORS)
    prompt_texts = [p.prompt for p in prompts[:6]]  # 演示取前 6 条
    print(f"[GEO 跟踪] Prompt 库 {len(prompts)} 条，本次跑 {len(prompt_texts)} 条")

    engines = build_engines()
    print(f"[GEO 跟踪] 引擎矩阵: {[e.name for e in engines]}")

    results = asyncio.run(
        run_track(
            engines,
            prompt_texts,
            BRAND,
            COMPETITORS,
            n_samples=s.geo_samples_per_prompt,
            concurrency=s.geo_concurrency,
        )
    )

    report = render_markdown(results, BRAND)
    out_dir = Path(__file__).resolve().parents[1] / "output"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "geo_report.md"
    out_file.write_text(report, encoding="utf-8")

    print(f"[GEO 跟踪] 完成 {len(results)} 组采样统计，报告已写入 {out_file}")
    print("\n===== 报告预览（前 40 行）=====")
    print("\n".join(report.splitlines()[:40]))


if __name__ == "__main__":
    main()
