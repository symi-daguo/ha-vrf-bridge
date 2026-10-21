"""协议层单元测试：德姆瑞。"""

from __future__ import annotations

import pytest

from custom_components.vrf_bridge.protocol.base import (
    VrfFanMode,
    VrfMode,
    VrfUnitKey,
)
from custom_components.vrf_bridge.protocol.demry import DemryProtocol

from .support.simulator.demry import DemrySimulator
from .support.simulator.model import ClimateMode, ClimateState, FanMode

SLAVE = 1


@pytest.fixture
def simulator() -> DemrySimulator:
    """返回两台内机的模拟器。"""

    return DemrySimulator(
        -1,
        states=[
            ClimateState(
                address=1,
                power=True,
                mode=ClimateMode.HEAT,
                fan_mode=FanMode.AUTO,
                target_temperature=22.0,
                current_temperature=19.0,
            ),
            ClimateState(
                address=5,
                power=False,
                mode=ClimateMode.DRY,
                fan_mode=FanMode.HIGH,
                target_temperature=26.0,
                current_temperature=30.0,
            ),
        ],
    )


def test_discover_frame_shape() -> None:
    """发现报文应为 10 字节。"""

    frame = DemryProtocol(SLAVE).build_discover()
    assert len(frame) == 10
    assert frame[0] == SLAVE
    assert frame[1] == 0xAA
    assert frame[9] == sum(frame[:9]) & 0xFF


def test_query_frame_uses_hold_fields() -> None:
    """查询报文除地址外全部使用保持值。"""

    frame = DemryProtocol(SLAVE).build_query(VrfUnitKey(0, 5))
    assert frame[:2] == bytes((SLAVE, 5))
    assert set(frame[2:9]) == {0xFF}


def test_discover_response_parses_bitmap(simulator: DemrySimulator) -> None:
    """应从位图解析出内机地址。"""

    protocol = DemryProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_discover())
    assert response is not None
    report = protocol.feed(response)
    assert sorted(unit.indoor for unit in report.units) == [1, 5]


def test_state_response_parses_values(simulator: DemrySimulator) -> None:
    """应正确解析状态字段。"""

    protocol = DemryProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 1)))
    assert response is not None
    update = protocol.feed(response).updates[0]
    assert update.key == VrfUnitKey(0, 1)
    assert update.power is True
    assert update.mode is VrfMode.HEAT
    assert update.fan_mode is VrfFanMode.AUTO
    assert update.target_temperature == 22
    assert update.current_temperature == 19


def test_control_frame_carries_full_state(simulator: DemrySimulator) -> None:
    """控制报文应带上开关、模式、温度与风速。"""

    protocol = DemryProtocol(SLAVE)
    from custom_components.vrf_bridge.protocol.base import VrfUnitState

    state = VrfUnitState(
        key=VrfUnitKey(0, 1),
        power=True,
        mode=VrfMode.COOL,
        fan_mode=VrfFanMode.MEDIUM,
        target_temperature=24,
        current_temperature=26,
    )
    frame = protocol.build_set_temperature(VrfUnitKey(0, 1), 21, state)[0]
    assert frame[2] == 0x01  # 开机
    assert frame[3] == 0x02  # 制冷
    assert frame[4] == 21
    assert frame[5] == 0x02  # 中风

    simulator.handle_frame(frame)
    state_after = simulator.get_state(1)
    assert state_after is not None
    assert state_after.target_temperature == 21


def test_control_frame_is_accepted_by_device(simulator: DemrySimulator) -> None:
    """控制报文应被设备接受。"""

    protocol = DemryProtocol(SLAVE)
    key = VrfUnitKey(0, 5)
    for frame in protocol.build_set_mode(key, VrfMode.COOL, None):
        simulator.handle_frame(frame)
    state = simulator.get_state(5)
    assert state is not None
    assert state.power is True
    assert state.mode is ClimateMode.COOL


def test_noise_before_frame_is_skipped(simulator: DemrySimulator) -> None:
    """前置噪声不应影响解析。"""

    protocol = DemryProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 1)))
    assert response is not None
    report = protocol.feed(b"\x00\xff" + response)
    assert len(report.updates) == 1
