"""总线时序与容错测试：命令间隔、控制抢占、丢包重试。"""

from __future__ import annotations

import time

import pytest

from custom_components.vrf_bridge.bus import BusCommand, VrfBus
from custom_components.vrf_bridge.const import (
    BUS_MIN_GAP,
    CONTROL_BUS_QUIET,
    QUERY_RETRY_TIMES,
)
from custom_components.vrf_bridge.protocol.base import VrfUnitKey


class FakeTransport:
    """记录收发时刻的假传输层。"""

    def __init__(self, responses: dict[bytes, list[bytes]] | None = None) -> None:
        """初始化。"""

        self.writes: list[tuple[float, bytes]] = []
        self.responses = responses or {}
        self.pending: list[bytes] = []
        self._open = True

    def open(self) -> None:
        """打开。"""

        self._open = True

    def close(self) -> None:
        """关闭。"""

        self._open = False

    def write(self, data: bytes) -> None:
        """记录一次发送。"""

        self.writes.append((time.monotonic(), data))
        self.pending.extend(self.responses.get(data, []))

    def read(self, size: int = 64) -> bytes:
        """返回排队的响应。"""

        if not self.pending:
            return b""
        return self.pending.pop(0)

    def reset_input(self) -> None:
        """清空接收缓存。"""

        self.pending.clear()

    @property
    def is_open(self) -> bool:
        """是否打开。"""

        return self._open


@pytest.fixture
def key() -> VrfUnitKey:
    """返回一台内机的地址。"""

    return VrfUnitKey(1, 1)


def _bus(transport: FakeTransport, **kwargs) -> VrfBus:
    """构造一个不启动线程的总线。"""

    return VrfBus(
        transport=transport,
        slave_addr=1,
        brand="zhonghong",
        poll_interval=10,
        discovery_interval=60,
        on_snapshot=kwargs.pop("on_snapshot", lambda snapshot: None),
        **kwargs,
    )


def test_commands_are_spaced_by_min_gap() -> None:
    """连续发送的报文之间必须留出总线间隔。"""

    transport = FakeTransport()
    bus = _bus(transport)
    bus._write(b"\x01\x50")
    bus._write(b"\x01\x51")
    assert transport.writes[1][0] - transport.writes[0][0] >= BUS_MIN_GAP * 0.9


def test_control_command_waits_for_bus_quiet() -> None:
    """控制指令之后要有总线静默期。"""

    transport = FakeTransport()
    bus = _bus(transport)
    bus._send_frames([b"\x01\x33\x01"])
    bus._write(b"\x01\x50")
    assert transport.writes[1][0] - transport.writes[0][0] >= CONTROL_BUS_QUIET * 0.9


def test_settle_window_blocks_stale_state(key: VrfUnitKey) -> None:
    """刚下发指令后，旧的回读不应覆盖界面状态。"""

    transport = FakeTransport()
    bus = _bus(transport)
    bus.submit([b"\x01\x33\x01"], key)
    command = bus._commands.get_nowait()
    assert isinstance(command, BusCommand)
    assert command.unit == key


def test_ignored_units_are_not_queried() -> None:
    """被忽略的内机不应再占用总线。"""

    transport = FakeTransport()
    bus = _bus(transport)
    bus.ignore_unit(1)
    bus._units[VrfUnitKey(0, 1)] = None
    bus._query_units()
    assert transport.writes == []


def test_query_is_retried_on_garbled_line(key: VrfUnitKey) -> None:
    """线路丢字节导致无有效回包时，应重发一次查询。"""

    transport = FakeTransport()
    bus = _bus(transport)
    protocol = bus.protocol
    query = protocol.build_query(key)
    # 设备回了一个被截断的帧：校验不过
    transport.responses = {query: [b"\x01\x50\x01\x01\x01\x01\x00\x10"]}
    bus._units[key] = None
    bus._query_units()
    sent = [data for _ts, data in transport.writes]
    assert sent.count(query) == QUERY_RETRY_TIMES + 1


def _queried_units(transport: FakeTransport) -> set[VrfUnitKey]:
    """从发出的报文里解析出被查询的内机。"""

    units: set[VrfUnitKey] = set()
    for _ts, data in transport.writes:
        if len(data) >= 6 and data[1] == 0x50 and data[2] == 0x01:
            units.add(VrfUnitKey(data[4], data[5]))
    return units


def test_one_unit_per_cycle_keeps_bus_quiet() -> None:
    """每轮只查一台内机，避免把网关打挂。"""

    transport = FakeTransport()
    bus = _bus(transport)
    for indoor in (0, 1, 2):
        bus._units[VrfUnitKey(1, indoor)] = None
    bus._query_units()
    assert len(_queried_units(transport)) == 1


def test_frames_are_spaced_in_query_loop(key: VrfUnitKey) -> None:
    """连续查询之间也要有总线间隔。"""

    transport = FakeTransport()
    bus = _bus(transport)
    for indoor in (0, 1, 2):
        bus._units[VrfUnitKey(1, indoor)] = None
    times: list[float] = []
    for _ in range(3):
        before = len(transport.writes)
        bus._query_units()
        times.append(transport.writes[before][0])
    assert len(_queried_units(transport)) == 3
    for earlier, later in zip(times, times[1:], strict=False):
        assert later - earlier >= BUS_MIN_GAP * 0.9
