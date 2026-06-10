"""平台适配器接口。新平台（微博/抖音/小红书/视频号）实现本接口后在 __init__.py 注册。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..materials import Material


class PlatformAdapter(ABC):
    name: str = ""

    @abstractmethod
    def login(self) -> None:
        """交互式登录（须在真终端运行）。"""

    @abstractmethod
    def is_logged_in(self) -> bool: ...

    @abstractmethod
    def publish(self, material: Material, state: dict, opts) -> dict:
        """发布一个素材。成功返回记录 dict（至少含 url/id 类字段），失败抛异常。"""

    def sync(self, materials: list[Material], state: dict) -> list[tuple[str, str]]:
        """从平台拉取已发布内容并与素材匹配，返回 [(素材名, 平台id)]。可选实现。"""
        raise NotImplementedError(f"{self.name} 暂不支持 sync")
