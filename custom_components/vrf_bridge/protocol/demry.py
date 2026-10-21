"""德姆瑞（Demry）VRF 协议实现。

帧格式（固定 10 字节）::

    [地址] [内机地址 | 0xAA] [开关] [模式] [目标温度] [风速] [保留 x3] [校验和]

校验和为除最后一个字节外所有字节之和的低 8 位。
"""

from __future__ import annotations

from typing import ClassVar

from ..const import MAX_BUFFER_SIZE
from .base import (
    VrfFanMode,
    VrfMode,
    VrfProtocol,
    VrfReport,
    VrfUnitKey,
    VrfUnitState,
    VrfUnitUpdate,
)

FRAME_LENGTH = 10
DISCOVERY_MARKER = 0xAA
HOLD = 0xFF
# 单个网关最多 32 台内机（与位图长度一致）。
MAX_UNITS = 32
# 温度字段的合法区间，超出即判定为非本协议报文。
MIN_TEMPERATURE = 5
MAX_TEMPERATURE = 40

SWITCH_OFF = 0x00
SWITCH_ON = 0x01

MODE_TO_WIRE = {
    VrfMode.COOL: 0x02,
    VrfMode.HEAT: 0x01,
    VrfMode.FAN_ONLY: 0x04,
    VrfMode.DRY: 0x08,
}
MODE_FROM_WIRE = {value: key for key, value in MODE_TO_WIRE.items()}

FAN_TO_WIRE = {
    VrfFanMode.AUTO: 0x00,
    VrfFanMode.LOW: 0x01,
    VrfFanMode.MEDIUM: 0x02,
    VrfFanMode.HIGH: 0x03,
}
FAN_FROM_WIRE = {value: key for key, value in FAN_TO_WIRE.items()}


def additive_checksum(data: bytes) -> int:
    """返回字节和的低 8 位。"""

    return sum(data) & 0xFF


class DemryProtocol(VrfProtocol):
    """德姆瑞系列中央空调网关协议。"""

    key: ClassVar[str] = "demry"
    label: ClassVar[str] = "德姆瑞"

    # -- 发送方向 ---------------------------------------------------------

    def build_discover(self) -> bytes:
        """构造在线内机查询报文。"""

        payload = bytes((self.slave_addr, DISCOVERY_MARKER, *([HOLD] * 7)))
        return payload + bytes((additive_checksum(payload),))

    def build_query(self, key: VrfUnitKey) -> bytes:
        """构造单台内机状态查询报文（全部字段保持）。"""

        payload = bytes((self.slave_addr, key.indoor, *([HOLD] * 7)))
        return payload + bytes((additive_checksum(payload),))

    def build_set_mode(
        self, key: VrfUnitKey, mode: VrfMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造模式控制报文。"""

        power = mode is not VrfMode.OFF
        mode_value = MODE_TO_WIRE.get(mode, HOLD) if power else HOLD
        fan_value = self._fan_value(state)
        target_value = self._target_value(state)
        return [
            self._control(
                key,
                SWITCH_ON if power else SWITCH_OFF,
                mode_value,
                target_value,
                fan_value,
            )
        ]

    def build_set_temperature(
        self, key: VrfUnitKey, temperature: float, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造目标温度控制报文。"""

        power = state.power if state else False
        mode_value = MODE_TO_WIRE.get(state.mode, HOLD) if state and power else HOLD
        fan_value = self._fan_value(state)
        return [
            self._control(
                key,
                SWITCH_ON if power else SWITCH_OFF,
                mode_value,
                max(0, min(0xFE, int(round(temperature)))),
                fan_value,
            )
        ]

    def build_set_fan(
        self, key: VrfUnitKey, fan_mode: VrfFanMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造风速控制报文。"""

        power = state.power if state else False
        mode_value = MODE_TO_WIRE.get(state.mode, HOLD) if state and power else HOLD
        target_value = self._target_value(state)
        return [
            self._control(
                key,
                SWITCH_ON if power else SWITCH_OFF,
                mode_value,
                target_value,
                FAN_TO_WIRE.get(fan_mode, HOLD),
            )
        ]

    @staticmethod
    def _fan_value(state: VrfUnitState | None) -> int:
        """返回上一次的风速值，未知时保持。"""

        if state is None:
            return HOLD
        return FAN_TO_WIRE.get(state.fan_mode, HOLD)

    @staticmethod
    def _target_value(state: VrfUnitState | None) -> int:
        """返回上一次的目标温度，未知时保持。"""

        if state is None or state.target_temperature is None:
            return HOLD
        return max(0, min(0xFE, int(round(state.target_temperature))))

    def _control(
        self, key: VrfUnitKey, switch: int, mode: int, target: int, fan: int
    ) -> bytes:
        """构造一条 10 字节控制报文。"""

        payload = bytes(
            (
                self.slave_addr,
                key.indoor,
                switch & 0xFF,
                mode & 0xFF,
                target & 0xFF,
                fan & 0xFF,
                HOLD,
                HOLD,
                HOLD,
            )
        )
        return payload + bytes((additive_checksum(payload),))

    # -- 接收方向 ---------------------------------------------------------

    def feed(self, data: bytes) -> VrfReport:
        """解析收到的字节流。"""

        self._append(data, MAX_BUFFER_SIZE)
        units: list[VrfUnitKey] = []
        updates: list[VrfUnitUpdate] = []

        while len(self._buffer) >= FRAME_LENGTH:
            frame = bytes(self._buffer[:FRAME_LENGTH])
            if additive_checksum(frame[:-1]) != frame[-1]:
                del self._buffer[0]
                continue
            if frame[0] != self.slave_addr:
                del self._buffer[:FRAME_LENGTH]
                continue
            del self._buffer[:FRAME_LENGTH]

            if frame[1] == DISCOVERY_MARKER:
                units.extend(self._parse_discovery(frame))
                continue
            update = self._parse_state(frame)
            if update is not None:
                updates.append(update)

        return VrfReport(units=tuple(units), updates=tuple(updates))

    def _parse_discovery(self, frame: bytes) -> list[VrfUnitKey]:
        """解析位图形式的内机列表。"""

        units: list[VrfUnitKey] = []
        for index in range(4):
            bitmap = frame[5 + index]
            for bit in range(8):
                if bitmap & (1 << bit):
                    units.append(VrfUnitKey(0, (3 - index) * 8 + bit))
        return units

    @staticmethod
    def _parse_state(frame: bytes) -> VrfUnitUpdate | None:
        """解析单台内机状态帧。

        帧格式固定为 10 字节，字段取值范围有限；总线上若混入其它协议的报文，
        单靠校验和不足以判定，必须逐字段校验，否则会凭空造出内机实体。
        """

        indoor = frame[1]
        if indoor == 0 or indoor > MAX_UNITS:
            return None
        power = frame[2] == SWITCH_ON
        mode = MODE_FROM_WIRE.get(frame[3])
        fan_mode = FAN_FROM_WIRE.get(frame[5])
        if mode is None or fan_mode is None:
            return None
        target = frame[4]
        if target < MIN_TEMPERATURE or target > MAX_TEMPERATURE:
            return None
        current = frame[6]
        return VrfUnitUpdate(
            key=VrfUnitKey(0, indoor),
            power=power,
            mode=mode if power else VrfMode.OFF,
            fan_mode=fan_mode,
            target_temperature=float(target),
            current_temperature=float(current) if current else None,
        )
