from .base import PlatformAdapter

# sau 引擎覆盖的平台
_SAU_PLATFORMS = {"douyin", "xiaohongshu", "kuaishou"}


def get_platform(name: str) -> PlatformAdapter:
    if name == "bilibili":
        from .bilibili import BilibiliAdapter
        return BilibiliAdapter()
    if name in _SAU_PLATFORMS:
        from .sau import SauAdapter
        return SauAdapter(name)
    raise KeyError(
        f"未支持的平台：{name}（已支持：bilibili / douyin / xiaohongshu / kuaishou；"
        f"视频号/微博在路线图上）")
