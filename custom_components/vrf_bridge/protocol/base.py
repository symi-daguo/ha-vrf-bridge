"""VRF 协议抽象层：与品牌无关的数据结构与协议接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class VrfMode(StrEnum):
    """内机运行模式（协议无关）。"""

    OFF = "off"
    COOL = "cool"
    HEAT = "heat"
    DRY = "dry"
    FAN_ONLY = "fan_only"


class VrfFanMode(StrEnum):
    """内机风速（协议无关）。"""

    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True, order=True)
class VrfUnitKey:
    """内机唯一标识。

    中弘协议使用「外机地址 + 内机地址」复合地址，其余协议只有内机地址，
    此时 ``outdoor`` 固定为 0。
    """

    outdoor: int = 0
    indoor: int = 0

    def __str__(self) -> str:
        """返回用于日志与唯一 ID 的短字符串。"""

        if self.outdoor:
            return f"{self.outdoor}.{self.indoor}"
        return str(self.indoor)


@dataclass(frozen=True, slots=True)
class VrfUnitUpdate:
    """一次内机状态更新。

    字段为 ``None`` 表示本次报文未携带该信息（例如协议未上报室温），
    调用方应保留上一次的值。
    """

    key: VrfUnitKey
    power: bool | None = None
    mode: VrfMode | None = None
    fan_mode: VrfFanMode | None = None
    target_temperature: float | None = None
    current_temperature: float | None = None


@dataclass(frozen=True, slots=True)
class VrfUnitState:
    """内机的完整状态快照。"""

    key: VrfUnitKey
    power: bool = False
    mode: VrfMode = VrfMode.OFF
    fan_mode: VrfFanMode = VrfFanMode.AUTO
    target_temperature: float | None = None
    current_temperature: float | None = None

    def merge(self, update: VrfUnitUpdate) -> VrfUnitState:
        """用一次局部更新合并出新的状态快照。"""

        power = self.power if update.power is None else update.power
        mode = update.mode if update.mode is not None else self.mode
        if not power:
            mode = VrfMode.OFF
        return VrfUnitState(
            key=self.key,
            power=power,
            mode=mode,
            fan_mode=update.fan_mode if update.fan_mode is not None else self.fan_mode,
            target_temperature=(
                self.target_temperature
                if update.target_temperature is None
                else update.target_temperature
            ),
            current_temperature=(
                self.current_temperature
                if update.current_temperature is None
                else update.current_temperature
            ),
        )


@dataclass(frozen=True, slots=True)
class VrfReport:
    """一次解析的产出：发现的内机与状态更新。"""

    units: tuple[VrfUnitKey, ...] = ()
    updates: tuple[VrfUnitUpdate, ...] = ()


class VrfProtocol(ABC):
    """一种 VRF 品牌协议的编解码实现。"""

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""

    def __init__(self, slave_addr: int) -> None:
        """保存总线从机地址。"""

        self.slave_addr = int(slave_addr) & 0xFF
        self._buffer = bytearray()

    # -- 发送方向 ---------------------------------------------------------

    @abstractmethod
    def build_discover(self) -> bytes:
        """构造「查询内机列表」报文。"""

    @abstractmethod
    def build_query(self, key: VrfUnitKey) -> bytes:
        """构造「查询单台内机状态」报文。"""

    @abstractmethod
    def build_set_mode(
        self, key: VrfUnitKey, mode: VrfMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造「设置运行模式」报文（可能需要多条）。"""

    @abstractmethod
    def build_set_temperature(
        self, key: VrfUnitKey, temperature: float, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造「设置目标温度」报文。"""

    @abstractmethod
    def build_set_fan(
        self, key: VrfUnitKey, fan_mode: VrfFanMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造「设置风速」报文。"""

    # -- 接收方向 ---------------------------------------------------------

    @abstractmethod
    def feed(self, data: bytes) -> VrfReport:
        """喂入收到的字节，返回解析出的内机与状态。"""

    def flush(self) -> None:
        """丢弃缓冲区中不完整的残留帧（总线空闲后调用）。"""

        self._buffer.clear()

    def reset(self) -> None:
        """重置解析状态（换协议或重连时调用）。"""

        self._buffer.clear()

    def _append(self, data: bytes, max_size: int) -> None:
        """追加数据并限制缓冲区大小，避免异常数据撑爆内存。"""

        self._buffer.extend(data)
        if len(self._buffer) > max_size:
            del self._buffer[: len(self._buffer) - max_size]
