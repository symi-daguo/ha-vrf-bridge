"""VRF 协议实现集合与品牌注册表。"""

from __future__ import annotations

from collections.abc import Callable

from ..const import BRAND_AUTO, BRAND_DEMRY, BRAND_SOCHUANG, BRAND_ZHONGHONG
from .base import (
    VrfFanMode,
    VrfMode,
    VrfProtocol,
    VrfReport,
    VrfUnitKey,
    VrfUnitState,
    VrfUnitUpdate,
)
from .demry import DemryProtocol
from .sochuang import SochuangProtocol
from .zhonghong import ZhonghongProtocol

__all__ = [
    "DemryProtocol",
    "SochuangProtocol",
    "VrfFanMode",
    "VrfMode",
    "VrfProtocol",
    "VrfReport",
    "VrfUnitKey",
    "VrfUnitState",
    "VrfUnitUpdate",
    "ZhonghongProtocol",
    "create_protocol",
    "protocol_brands",
]

# 自动探测时的尝试顺序，与总线报文互不冲突。
_PROTOCOL_CLASSES: tuple[type[VrfProtocol], ...] = (
    ZhonghongProtocol,
    DemryProtocol,
    SochuangProtocol,
)

_BRAND_TO_CLASS: dict[str, type[VrfProtocol]] = {
    ZhonghongProtocol.key: ZhonghongProtocol,
    DemryProtocol.key: DemryProtocol,
    SochuangProtocol.key: SochuangProtocol,
}


def protocol_brands() -> tuple[str, ...]:
    """返回自动探测时的品牌顺序。"""

    return tuple(cls.key for cls in _PROTOCOL_CLASSES)


def create_protocol(brand: str, slave_addr: int) -> VrfProtocol:
    """按品牌创建协议实例；``auto`` 返回第一个候选协议。"""

    if brand == BRAND_AUTO:
        return _PROTOCOL_CLASSES[0](slave_addr)
    try:
        protocol_class = _BRAND_TO_CLASS[brand]
    except KeyError as err:
        raise ValueError(f"unknown VRF brand: {brand}") from err
    return protocol_class(slave_addr)


def protocol_factory(brand: str) -> Callable[[int], VrfProtocol]:
    """返回按品牌创建协议实例的可调用对象。"""

    def _factory(slave_addr: int) -> VrfProtocol:
        return create_protocol(brand, slave_addr)

    return _factory


# 品牌常量重导出，便于其它模块统一引用。
BRANDS: tuple[str, ...] = (
    BRAND_AUTO,
    BRAND_ZHONGHONG,
    BRAND_DEMRY,
    BRAND_SOCHUANG,
)
