"""GEO 跟踪核心逻辑单测：指标计算、域名解析、信源缺口、故障降级。"""
from collections import Counter

import httpx

from aigc_growth.geo.report import aggregate_overall, source_gap
from aigc_growth.geo.sampler import MockEngine, SampleResult, domain_of
from aigc_growth.geo.tracker import analyze, track_prompt_sync


class TestDomainOf:
    def test_basic(self):
        assert domain_of("https://www.zhihu.com/question/123") == "zhihu.com"

    def test_no_www(self):
        assert domain_of("https://36kr.com/p/67890") == "36kr.com"

    def test_case_fold(self):
        assert domain_of("https://WWW.Example.COM/a") == "example.com"

    def test_empty(self):
        assert domain_of("not-a-url") == ""


class TestAnalyze:
    def test_mention_head(self):
        r = analyze("品牌A是不错的选择", "品牌A", ["竞品X"])
        assert r["mentioned"] and r["position"] == "head"

    def test_mention_tail(self):
        r = analyze("可以考虑竞品X，或者最后看看品牌A", "品牌A", ["竞品X"])
        assert r["mentioned"] and r["position"] == "tail"

    def test_no_mention(self):
        r = analyze("只提到了竞品X和竞品Y", "品牌A", ["竞品X", "竞品Y"])
        assert not r["mentioned"]
        assert set(r["competitors"]) == {"竞品X", "竞品Y"}


class TestTrackPromptSync:
    def test_mock_engine_aggregation(self):
        engine = MockEngine("mock-test", brand_hit=True)
        result = track_prompt_sync(engine, "品牌A怎么样", "品牌A", ["竞品X"], n_samples=3)
        assert result.n_samples == 3
        assert result.mention_rate == 1.0        # mock 固定提及
        assert result.citation_rate == 1.0
        assert result.source_domains == Counter(
            {"zhihu.com": 3, "36kr.com": 3, "163.com": 3}
        )
        assert len(result.raw) == 3              # 原始存档

    def test_mock_engine_no_hit(self):
        engine = MockEngine("mock-test", brand_hit=False)
        result = track_prompt_sync(engine, "随便问", "品牌A", ["竞品X"], n_samples=2)
        assert result.mention_rate == 0.0
        assert result.competitors_hits["竞品X"] >= 0


class TestSearchAugmentedEngine:
    """自建联网引擎：搜索 API 检索 → LLM 综合 → 结构化 citations。"""

    @staticmethod
    def _engine(monkeypatch, answer: str):
        from aigc_growth.geo import sampler
        from aigc_growth.geo.search import SearchBackend, SearchHit

        class FakeSearch(SearchBackend):
            name = "fake"
            unit_cost_usd = 0.0014

            def search(self, query: str, top_k: int = 5):
                return [
                    SearchHit("知乎回答", "https://www.zhihu.com/question/1", "内容1"),
                    SearchHit("36氪报道", "https://36kr.com/p/2", "内容2"),
                ]

        monkeypatch.setattr(sampler.LLMClient, "chat",
                            lambda self, system, user: answer)
        return sampler.SearchAugmentedEngine(FakeSearch())

    def test_citations_from_refs(self, monkeypatch):
        engine = self._engine(monkeypatch, "品牌A表现不错[2]，详见 [1]。")
        r = engine.sample("品牌A怎么样")
        assert r.citations == [
            "https://www.zhihu.com/question/1",
            "https://36kr.com/p/2",
        ]
        assert r.engine == "search-augmented"
        assert r.cost == 0.0014

    def test_citations_fallback_to_urls(self, monkeypatch):
        engine = self._engine(monkeypatch, "参考 https://example.com/a 的说法。")
        assert engine.sample("x").citations == ["https://example.com/a"]

    def test_citations_fallback_to_hits(self, monkeypatch):
        engine = self._engine(monkeypatch, "资料不足，无法判断。")
        assert len(engine.sample("x").citations) == 2


class TestBochaSearch:
    """博查响应兼容 Bing 结构：webPages 位于响应顶层，不在 data 下。"""

    # 官方文档给出的响应结构（已裁剪）
    _PAYLOAD = {
        "_type": "SearchResponse",
        "queryContext": {"originalQuery": "q"},
        "webPages": {
            "totalEstimatedMatches": 606721,
            "value": [
                {"name": "标题1", "url": "https://a.com/1", "summary": "长摘要1"},
                {"name": "标题2", "url": "https://b.com/2", "snippet": "短摘要2"},
                {"name": "无链接项"},
            ],
        },
    }

    @staticmethod
    def _patch(monkeypatch, payload):
        from aigc_growth.geo import search as mod

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: _Resp())

    def test_parse_top_level(self, monkeypatch):
        from aigc_growth.geo.search import BochaSearch

        self._patch(monkeypatch, self._PAYLOAD)
        hits = BochaSearch("k").search("q")
        assert [h.url for h in hits] == ["https://a.com/1", "https://b.com/2"]
        assert hits[0].title == "标题1"
        assert hits[0].content == "长摘要1"
        assert hits[1].content == "短摘要2"        # summary 缺失时回退 snippet

    def test_parse_wrapped_in_data(self, monkeypatch):
        from aigc_growth.geo.search import BochaSearch

        self._patch(monkeypatch, {"data": self._PAYLOAD})
        assert len(BochaSearch("k").search("q")) == 2


class TestFaultTolerance:
    """单引擎故障必须降级，不能拖垮整轮采样。"""

    class _Boom:
        name = "boom"

        def sample(self, prompt):
            raise httpx.HTTPError("429 Too Many Requests")

    def test_failed_sample_does_not_raise(self):
        from aigc_growth.geo.tracker import _one_sample

        r = _one_sample(self._Boom(), "p", "品牌A", ["竞品X"])
        assert r["failed"] is True
        assert r["mentioned"] is False
        assert r["cost"] == 0.0
        assert "429" in r["error"]

    def test_failed_samples_excluded_from_rates(self):
        """3 次采样里 1 次失败 → 分母按成功的 2 次算，而非 3。"""
        calls = {"n": 0}

        class Flaky:
            name = "flaky"

            def sample(self, prompt):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.HTTPError("503")
                return SampleResult(answer="品牌A不错", citations=["https://a.com/1"],
                                    engine="flaky")

        r = track_prompt_sync(Flaky(), "p", "品牌A", ["竞品X"], n_samples=3)
        assert r.n_samples == 3
        assert r.failed == 1
        assert r.mention_rate == 1.0        # 2/2，失败那次不进分母
        assert len(r.raw) == 3              # 原始存档仍完整保留

    def test_all_failed_returns_zero_rates(self):
        r = track_prompt_sync(self._Boom(), "p", "品牌A", [], n_samples=2)
        assert r.failed == 2
        assert r.mention_rate == 0.0
        assert r.citation_rate == 0.0


class TestSourceGap:
    def test_gap_calc(self):
        domains = Counter({"zhihu.com": 5, "sohu.com": 3, "36kr.com": 2})
        gap = source_gap(domains, own_domains={"zhihu.com"})
        assert "zhihu.com" in gap["covered"]
        assert set(gap["gap"]) == {"sohu.com", "36kr.com"}
        assert gap["cited_domains"][0] == ("zhihu.com", 5)


class TestAggregate:
    def test_overall(self):
        e1 = MockEngine("e1", brand_hit=True)
        r1 = track_prompt_sync(e1, "p1", "品牌A", ["竞品X"], n_samples=2)
        overall = aggregate_overall([r1], "品牌A")
        assert overall["total_samples"] == 2
        assert 0.0 <= overall["sov"] <= 1.0
