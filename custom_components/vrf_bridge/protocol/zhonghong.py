"""中弘（Zhonghong）VRF 协议实现。

帧格式（从站 -> 主站）::

    [地址] [功能码] [功能值] [数量] [记录 x N] [校验和]

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

FUNC_CTRL_SWITCH = 0x31
FUNC_CTRL_TEMPERATURE = 0x32
FUNC_CTRL_MODE = 0x33
FUNC_CTRL_FAN = 0x34
FUNC_QUERY = 0x50

QUERY_SINGLE = 0x01
QUERY_ONLINE = 0x02
QUERY_MULTI = 0x0F
QUERY_ALL = 0xFF

SWITCH_OFF = 0x00
SWITCH_ON = 0x01

MODE_TO_WIRE = {
    VrfMode.COOL: 0x01,
    VrfMode.HEAT: 0x08,
    VrfMode.FAN_ONLY: 0x04,
    VrfMode.DRY: 0x02,
}
MODE_FROM_WIRE = {value: key for key, value in MODE_TO_WIRE.items()}

FAN_TO_WIRE = {
    VrfFanMode.LOW: 0x04,
    VrfFanMode.MEDIUM: 0x02,
    VrfFanMode.HIGH: 0x01,
}
FAN_FROM_WIRE = {value: key for key, value in FAN_TO_WIRE.items()}

# 单次响应允许携带的最大内机数量。
MAX_UNITS_PER_RESPONSE = 64
# 在线列表记录长度 / 状态记录长度。
ONLINE_RECORD_LENGTH = 3
STATE_RECORD_LENGTH = 10
# 控制确认帧长度。
CONTROL_ACK_LENGTH = 6


def additive_checksum(data: bytes) -> int:
    """返回字节和的低 8 位。"""

    return sum(data) & 0xFF


class ZhonghongProtocol(VrfProtocol):
    """中弘系列中央空调网关协议。"""

    key: ClassVar[str] = "zhonghong"
    label: ClassVar[str] = "中弘"

    # -- 发送方向 ---------------------------------------------------------

    def build_discover(self) -> bytes:
        """构造在线内机查询报文。"""

        payload = bytes((self.slave_addr, FUNC_QUERY, QUERY_ONLINE, 0xFF, 0xFF, 0xFF))
        return payload + bytes((additive_checksum(payload),))

    def build_query(self, key: VrfUnitKey) -> bytes:
        """构造单台内机状态查询报文。"""

        payload = bytes(
            (
                self.slave_addr,
                FUNC_QUERY,
                QUERY_SINGLE,
                0x01,
                key.outdoor,
                key.indoor,
            )
        )
        return payload + bytes((additive_checksum(payload),))

    def build_set_mode(
        self, key: VrfUnitKey, mode: VrfMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造模式控制报文；开机需要先设模式再开机。"""

        del state
        if mode is VrfMode.OFF:
            return [self._control(FUNC_CTRL_SWITCH, SWITCH_OFF, key)]

        mode_frame = self._control(FUNC_CTRL_MODE, MODE_TO_WIRE[mode], key)
        on_frame = self._control(FUNC_CTRL_SWITCH, SWITCH_ON, key)
        return [mode_frame, on_frame]

    def build_set_temperature(
        self, key: VrfUnitKey, temperature: float, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造目标温度控制报文。"""

        del state
        value = max(16, min(30, int(round(temperature))))
        return [self._control(FUNC_CTRL_TEMPERATURE, value, key)]

    def build_set_fan(
        self, key: VrfUnitKey, fan_mode: VrfFanMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造风速控制报文；协议无自动风速，自动映射为低速。"""

        del state
        value = FAN_TO_WIRE.get(fan_mode, FAN_TO_WIRE[VrfFanMode.LOW])
        return [self._control(FUNC_CTRL_FAN, value, key)]

    def _control(self, function: int, value: int, key: VrfUnitKey) -> bytes:
        """构造一条 7 字节控制报文。"""

        payload = bytes(
            (
                self.slave_addr,
                function,
                value & 0xFF,
                0x01,
                key.outdoor,
                key.indoor,
            )
        )
        return payload + bytes((additive_checksum(payload),))

    # -- 接收方向 ---------------------------------------------------------

    def feed(self, data: bytes) -> VrfReport:
        """解析收到的字节流。"""

        self._append(data, MAX_BUFFER_SIZE)
        units: list[VrfUnitKey] = []
        updates: list[VrfUnitUpdate] = []

        while len(self._buffer) >= 6:
            length = self._frame_length(self._buffer)
            if length == 0:
                del self._buffer[0]
                continue
            if len(self._buffer) < length:
                break
            frame = bytes(self._buffer[:length])
            if additive_checksum(frame[:-1]) != frame[-1]:
                del self._buffer[0]
                continue
            del self._buffer[:length]
            if frame[1] != FUNC_QUERY:
                # 控制确认帧不携带状态。
                continue
            parsed_units, parsed_updates = self._parse_query(frame)
            units.extend(parsed_units)
            updates.extend(parsed_updates)

        return VrfReport(units=tuple(units), updates=tuple(updates))

    def _frame_length(self, data: bytes | bytearray) -> int:
        """计算缓冲区首帧的长度；0 表示首字节非法。"""

        if len(data) < 6 or data[0] != self.slave_addr:
            return 0
        function = data[1]
        if FUNC_CTRL_SWITCH <= function <= FUNC_CTRL_FAN:
            return CONTROL_ACK_LENGTH
        if function != FUNC_QUERY:
            return 0

        func_value = data[2]
        count = data[3]
        if count > MAX_UNITS_PER_RESPONSE:
            return 0
        if func_value == QUERY_ONLINE:
            record_length = ONLINE_RECORD_LENGTH
        elif func_value == QUERY_SINGLE:
            if count > 1:
                return 0
            record_length = STATE_RECORD_LENGTH
        elif func_value in (QUERY_MULTI, QUERY_ALL):
            record_length = STATE_RECORD_LENGTH
        else:
            return 0
        return 4 + count * record_length + 1

    def _parse_query(
        self, frame: bytes
    ) -> tuple[list[VrfUnitKey], list[VrfUnitUpdate]]:
        """解析查询响应帧。"""

        func_value = frame[2]
        count = frame[3]
        if func_value == QUERY_ONLINE:
            units: list[VrfUnitKey] = []
            for index in range(count):
                base = 4 + index * ONLINE_RECORD_LENGTH
                outdoor = frame[base]
                indoor = frame[base + 1]
                online = frame[base + 2]
                if online == 1:
                    units.append(VrfUnitKey(outdoor, indoor))
            return units, []

        updates: list[VrfUnitUpdate] = []
        for index in range(count):
            base = 4 + index * STATE_RECORD_LENGTH
            outdoor = frame[base]
            indoor = frame[base + 1]
            power = frame[base + 2] != SWITCH_OFF
            target = frame[base + 3]
            mode = MODE_FROM_WIRE.get(frame[base + 4])
            fan_mode = FAN_FROM_WIRE.get(frame[base + 5])
            current = frame[base + 6]
            updates.append(
                VrfUnitUpdate(
                    key=VrfUnitKey(outdoor, indoor),
                    power=power,
                    mode=mode if power else VrfMode.OFF,
                    fan_mode=fan_mode,
                    target_temperature=float(target),
                    current_temperature=float(current) if current else None,
                )
            )
        return [], updates
