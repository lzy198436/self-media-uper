from .base import PlatformAdapter

# sau 引擎覆盖的平台（shipinhao=视频号，sau 里叫 tencent）
_SAU_PLATFORMS = {"douyin", "xiaohongshu", "kuaishou", "shipinhao"}


def get_platform(name: str, engine: str = "sau") -> PlatformAdapter:
    if name == "bilibili":
        from .bilibili import BilibiliAdapter
        return BilibiliAdapter()
    # 小红书视频可走扩展(日常浏览器，风控低)或 sau(patchright，回退用)
    if name == "xiaohongshu" and engine == "extension":
        from .xhs_video import XhsVideoAdapter
        return XhsVideoAdapter()
    if name in _SAU_PLATFORMS:
        from .sau import SauAdapter
        return SauAdapter(name)
    raise KeyError(
        f"未支持的平台：{name}（已支持：bilibili / douyin / xiaohongshu / kuaishou / shipinhao；"
        f"微博在路线图上）")
