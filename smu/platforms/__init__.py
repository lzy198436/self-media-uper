from .base import PlatformAdapter


def get_platform(name: str) -> PlatformAdapter:
    if name == "bilibili":
        from .bilibili import BilibiliAdapter
        return BilibiliAdapter()
    raise KeyError(f"未支持的平台：{name}（已支持：bilibili；微博/抖音/小红书/视频号在路线图上）")
