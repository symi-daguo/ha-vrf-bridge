"""硕创（Sochuang）VRF 协议实现。

帧格式（固定 12 字节）::

    [地址] [00] [0C] [功能码] [数据 x6] [CRC1] [CRC2]

CRC 为 Modbus CRC16（初值 0xFFFF，多项式 0xA001），线上低字节在前。
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

FRAME_LENGTH = 12
LENGTH_MARKER = 0x0C

FUNC_QUERY_COUNT = 0x01
FUNC_QUERY_STATUS = 0x02
FUNC_CTRL_SWITCH = 0x04
FUNC_CTRL_MODE = 0x05
FUNC_CTRL_TEMPERATURE = 0x06
FUNC_CTRL_FAN = 0x07

SWITCH_OFF = 0x00
SWITCH_ON = 0x01

MODE_TO_WIRE = {
    VrfMode.COOL: 0x00,
    VrfMode.HEAT: 0x01,
    VrfMode.FAN_ONLY: 0x02,
    VrfMode.DRY: 0x03,
}
MODE_FROM_WIRE = {value: key for key, value in MODE_TO_WIRE.items()}

FAN_TO_WIRE = {
    VrfFanMode.LOW: 0x00,
    VrfFanMode.MEDIUM: 0x01,
    VrfFanMode.HIGH: 0x02,
    VrfFanMode.AUTO: 0x03,
}
FAN_FROM_WIRE = {value: key for key, value in FAN_TO_WIRE.items()}

# 单个网关最多 32 台内机。
MAX_UNITS = 32


def modbus_crc16(data: bytes) -> int:
    """计算 Modbus CRC16。"""

    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


class SochuangProtocol(VrfProtocol):
    """硕创系列中央空调网关协议。"""

    key: ClassVar[str] = "sochuang"
    label: ClassVar[str] = "硕创"

    # -- 发送方向 ---------------------------------------------------------

    def build_discover(self) -> bytes:
        """构造内机数量查询报文。"""

        payload = bytes(
            (self.slave_addr, 0x00, LENGTH_MARKER, FUNC_QUERY_COUNT, *([0] * 6))
        )
        return payload + modbus_crc16(payload).to_bytes(2, "little")

    def build_query(self, key: VrfUnitKey) -> bytes:
        """构造单台内机状态查询报文。"""

        payload = bytes(
            (
                self.slave_addr,
                0x00,
                LENGTH_MARKER,
                FUNC_QUERY_STATUS,
                key.indoor,
                *([0] * 5),
            )
        )
        return payload + modbus_crc16(payload).to_bytes(2, "little")

    def build_set_mode(
        self, key: VrfUnitKey, mode: VrfMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造模式控制报文；开机需要先设模式再开机。"""

        del state
        if mode is VrfMode.OFF:
            return [self._control(FUNC_CTRL_SWITCH, SWITCH_OFF, key)]
        return [
            self._control(FUNC_CTRL_MODE, MODE_TO_WIRE[mode], key),
            self._control(FUNC_CTRL_SWITCH, SWITCH_ON, key),
        ]

    def build_set_temperature(
        self, key: VrfUnitKey, temperature: float, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造目标温度控制报文。"""

        del state
        value = max(0, min(0xFF, int(round(temperature))))
        return [self._control(FUNC_CTRL_TEMPERATURE, value, key)]

    def build_set_fan(
        self, key: VrfUnitKey, fan_mode: VrfFanMode, state: VrfUnitState | None
    ) -> list[bytes]:
        """构造风速控制报文。"""

        del state
        value = FAN_TO_WIRE.get(fan_mode, FAN_TO_WIRE[VrfFanMode.AUTO])
        return [self._control(FUNC_CTRL_FAN, value, key)]

    def _control(self, function: int, value: int, key: VrfUnitKey) -> bytes:
        """构造一条 12 字节控制报文。"""

        payload = bytes(
            (
                self.slave_addr,
                0x00,
                LENGTH_MARKER,
                function,
                key.indoor,
                value & 0xFF,
                *([0] * 4),
            )
        )
        return payload + modbus_crc16(payload).to_bytes(2, "little")

    # -- 接收方向 ---------------------------------------------------------

    def feed(self, data: bytes) -> VrfReport:
        """解析收到的字节流。"""

        self._append(data, MAX_BUFFER_SIZE)
        units: list[VrfUnitKey] = []
        updates: list[VrfUnitUpdate] = []

        while len(self._buffer) >= FRAME_LENGTH:
            frame = bytes(self._buffer[:FRAME_LENGTH])
            if frame[1] != 0x00 or frame[2] != LENGTH_MARKER:
                del self._buffer[0]
                continue
            if modbus_crc16(frame[:10]) != int.from_bytes(frame[10:12], "little"):
                del self._buffer[0]
                continue
            if frame[0] != self.slave_addr:
                del self._buffer[:FRAME_LENGTH]
                continue
            del self._buffer[:FRAME_LENGTH]

            function = frame[3]
            if function == FUNC_QUERY_COUNT:
                count = min(frame[4], MAX_UNITS)
                units.extend(VrfUnitKey(0, index + 1) for index in range(count))
            elif function == FUNC_QUERY_STATUS:
                update = self._parse_state(frame)
                if update is not None:
                    updates.append(update)

        return VrfReport(units=tuple(units), updates=tuple(updates))

    @staticmethod
    def _parse_state(frame: bytes) -> VrfUnitUpdate | None:
        """解析单台内机状态帧。"""

        indoor = frame[4]
        if indoor < 1 or indoor > MAX_UNITS:
            return None
        power = frame[5] != SWITCH_OFF
        mode = MODE_FROM_WIRE.get(frame[6])
        fan_mode = FAN_FROM_WIRE.get(frame[8])
        current = frame[9]
        return VrfUnitUpdate(
            key=VrfUnitKey(0, indoor),
            power=power,
            mode=mode if power else VrfMode.OFF,
            fan_mode=fan_mode,
            target_temperature=float(frame[7]),
            current_temperature=float(current) if current else None,
        )
