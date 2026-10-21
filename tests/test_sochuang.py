"""协议层单元测试：硕创。"""

from __future__ import annotations

import pytest

from custom_components.vrf_bridge.protocol.base import (
    VrfFanMode,
    VrfMode,
    VrfUnitKey,
)
from custom_components.vrf_bridge.protocol.sochuang import SochuangProtocol

from .support.simulator.model import ClimateMode, ClimateState, FanMode
from .support.simulator.sochuang import SochuangSimulator

SLAVE = 1


@pytest.fixture
def simulator() -> SochuangSimulator:
    """返回两台内机的模拟器。"""

    return SochuangSimulator(
        -1,
        states=[
            ClimateState(
                address=1,
                power=True,
                mode=ClimateMode.DRY,
                fan_mode=FanMode.LOW,
                target_temperature=23.0,
                current_temperature=25.0,
            ),
            ClimateState(
                address=2,
                power=False,
                mode=ClimateMode.FAN_ONLY,
                fan_mode=FanMode.AUTO,
                target_temperature=28.0,
                current_temperature=29.0,
            ),
        ],
    )


def test_discover_frame_shape() -> None:
    """发现报文应为 12 字节并带 Modbus CRC。"""

    frame = SochuangProtocol(SLAVE).build_discover()
    assert len(frame) == 12
    assert frame[:5] == bytes((SLAVE, 0x00, 0x0C, 0x01, 0x00))


def test_query_frame_carries_indoor_address() -> None:
    """查询报文应携带内机地址。"""

    frame = SochuangProtocol(SLAVE).build_query(VrfUnitKey(0, 2))
    assert frame[:5] == bytes((SLAVE, 0x00, 0x0C, 0x02, 2))


def test_discover_response_parses_count(simulator: SochuangSimulator) -> None:
    """应按数量生成连续的内机地址。"""

    protocol = SochuangProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_discover())
    assert response is not None
    report = protocol.feed(response)
    assert [unit.indoor for unit in report.units] == [1, 2]


def test_state_response_parses_values(simulator: SochuangSimulator) -> None:
    """应正确解析状态字段。"""

    protocol = SochuangProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 1)))
    assert response is not None
    update = protocol.feed(response).updates[0]
    assert update.key == VrfUnitKey(0, 1)
    assert update.power is True
    assert update.mode is VrfMode.DRY
    assert update.fan_mode is VrfFanMode.LOW
    assert update.target_temperature == 23
    assert update.current_temperature == 25


def test_mode_control_sends_mode_then_power(simulator: SochuangSimulator) -> None:
    """开机应先设模式再开机。"""

    protocol = SochuangProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    frames = protocol.build_set_mode(key, VrfMode.HEAT, None)
    assert len(frames) == 2
    assert frames[0][3] == 0x05
    assert frames[1][3] == 0x04
    assert frames[1][5] == 0x01

    for frame in frames:
        simulator.handle_frame(frame)
    state = simulator.get_state(1)
    assert state is not None
    assert state.power is True
    assert state.mode is ClimateMode.HEAT


def test_control_frame_is_accepted_by_device(simulator: SochuangSimulator) -> None:
    """温度与风速控制应被设备接受。"""

    protocol = SochuangProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    for frame in protocol.build_set_temperature(key, 19, None):
        simulator.handle_frame(frame)
    for frame in protocol.build_set_fan(key, VrfFanMode.HIGH, None):
        simulator.handle_frame(frame)

    state = simulator.get_state(1)
    assert state is not None
    assert state.target_temperature == 19
    assert state.fan_mode is FanMode.HIGH


def test_noise_before_frame_is_skipped(simulator: SochuangSimulator) -> None:
    """前置噪声不应影响解析。"""

    protocol = SochuangProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 1)))
    assert response is not None
    report = protocol.feed(b"\x11\x22" + response)
    assert len(report.updates) == 1
