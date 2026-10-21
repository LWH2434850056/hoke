"""全局配置：pydantic-settings 读取 .env，key 留空时自动降级为 mock。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 全局开关 ----
    mock_external: bool = True

    # ---- 基建 ----
    database_url: str = "postgresql+psycopg://aigc:aigc@localhost:5432/aigc"
    redis_url: str = "redis://localhost:6379/0"

    # ---- LLM 分层路由 ----
    llm_strong_api_key: str = ""
    llm_strong_base_url: str = "https://api.deepseek.com/v1"
    llm_strong_model: str = "deepseek-chat"
    llm_lite_api_key: str = ""
    llm_lite_base_url: str = "https://api.deepseek.com/v1"
    llm_lite_model: str = "deepseek-chat"

    # ---- GEO 跟踪 ----
    perplexity_api_key: str = ""
    geo_models: str = "search-augmented,perplexity-sonar,doubao-web,kimi-web,deepseek-web"
    geo_samples_per_prompt: int = 3
    geo_concurrency: int = 5

    # ---- GEO 搜索通道（替代 Perplexity 的联网检索 + 结构化引用）----
    search_provider: str = ""     # "" 按 key 自动挑选 | zhipu | tavily | bocha
    search_top_k: int = 5         # 每次检索取前 N 条作为证据
    zhipu_api_key: str = ""
    zhipu_search_engine: str = "search_std"   # search_std 0.01元/次 | search_pro 0.03元/次
    tavily_api_key: str = ""
    bocha_api_key: str = ""

    # ---- 数字人 ----
    avatar_provider: str = "mock"  # mock | silicon | sadtalker | aliyun
    avatar_voice_key: str = "female_warm"       # 音色（与 TTS 预设共用）
    silicon_api_key: str = ""
    silicon_base_url: str = "https://api.siliconintelligent.com"

    # 本地 SadTalker：零成本无限量，需 GPU + 单独部署模型
    sadtalker_dir: str = ""                     # SadTalker 仓库目录
    sadtalker_python: str = ""                  # 解释器，留空用当前 venv
    sadtalker_extra_args: str = "--still --preprocess full --enhancer gfpgan"

    # 阿里云百炼 万相 wan2.2-s2v：新用户 100 秒免费，单次音频需 <20 秒
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"
    wan_resolution: str = "480P"                # 480P 0.5元/秒 | 720P 0.9元/秒
    # 万相要求图片/音频公网可达。把 output/ 暴露到公网（隧道/OSS）后填这里，
    # 本地文件会自动映射成 {MEDIA_PUBLIC_BASE}/videos/xxx；留空则必须直接传公网 URL
    media_public_base: str = ""

    # 讯飞 数字人视频大模型：免费包 300 秒 / 7 天 / 并发 1
    # 主题驱动（服务端自动扩写文案+配音），不使用本地文案与形象
    xfyun_appid: str = ""
    xfyun_api_key: str = ""
    xfyun_api_secret: str = ""
    xfyun_base_url: str = "https://vms.cn-huadong-1.xf-yun.com"
    xfyun_word_count: int = 0                   # 0=按文案长度自动；范围 50-300

    # ---- 抖音 ----
    douyin_client_key: str = ""
    douyin_client_secret: str = ""
    douyin_business_token: str = ""

    # ---- 工程参数 ----
    llm_token_budget_daily: int = 5_000_000
    trace_enabled: bool = True

    @property
    def geo_model_list(self) -> list[str]:
        return [m.strip() for m in self.geo_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_mock() -> bool:
    """mock 判定：显式开关打开，或关键 key 缺失时自动降级。"""
    s = get_settings()
    return s.mock_external
