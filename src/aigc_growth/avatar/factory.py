"""Provider 工厂：按配置装配，换厂商只改这里。"""
from __future__ import annotations

import logging

from aigc_growth.avatar.provider import AvatarProvider
from aigc_growth.config import get_settings

logger = logging.getLogger(__name__)


def get_provider() -> AvatarProvider:
    """按 AVATAR_PROVIDER 装配：aliyun | sadtalker | xfyun | silicon，未就绪则降级 mock。

    装配阶段不抛异常——真正的失败留到 submit 阶段暴露，好让上层的
    「失败自动降级 TTS 混剪」兜住，而不是整条发布链路断掉。
    """
    s = get_settings()
    if not s.mock_external:
        name = s.avatar_provider

        if name == "aliyun" and s.dashscope_api_key:
            from aigc_growth.avatar.aliyun import AliyunWanProvider

            return AliyunWanProvider(s.dashscope_api_key, s.dashscope_base_url,
                                     s.wan_resolution, s.avatar_voice_key)

        if name == "sadtalker":
            from aigc_growth.avatar.sadtalker import SadTalkerProvider

            if not s.sadtalker_dir:
                logger.warning("AVATAR_PROVIDER=sadtalker 但 SADTALKER_DIR 未配置")
            return SadTalkerProvider(s.sadtalker_dir, s.sadtalker_python,
                                     s.sadtalker_extra_args, s.avatar_voice_key)

        if name == "xfyun" and s.xfyun_appid and s.xfyun_api_key and s.xfyun_api_secret:
            from aigc_growth.avatar.xfyun import XfyunAvatarProvider

            return XfyunAvatarProvider(s.xfyun_appid, s.xfyun_api_key,
                                       s.xfyun_api_secret, s.xfyun_base_url,
                                       s.xfyun_word_count)

        if name == "silicon" and s.silicon_api_key:
            from aigc_growth.avatar.silicon import SiliconIntelligenceProvider

            return SiliconIntelligenceProvider(s.silicon_api_key, s.silicon_base_url)

        if name != "mock":
            logger.warning("AVATAR_PROVIDER=%s 未就绪（缺 key 或配置），降级 mock", name)

    from aigc_growth.avatar.mock import InstantMockProvider

    return InstantMockProvider()
