"""协议层单元测试：中弘。"""

from __future__ import annotations

import pytest

from custom_components.vrf_bridge.protocol.base import (
    VrfFanMode,
    VrfMode,
    VrfUnitKey,
    VrfUnitState,
)
from custom_components.vrf_bridge.protocol.zhonghong import ZhonghongProtocol

from .support.simulator.model import ClimateMode, ClimateState, FanMode
from .support.simulator.zhonghong import ZhonghongSimulator

SLAVE = 1


@pytest.fixture
def simulator() -> ZhonghongSimulator:
    """返回两台内机的模拟器。"""

    return ZhonghongSimulator(
        -1,
        states=[
            ClimateState(
                address=1,
                power=True,
                mode=ClimateMode.COOL,
                fan_mode=FanMode.MEDIUM,
                target_temperature=24.0,
                current_temperature=27.0,
            ),
            ClimateState(
                address=2,
                power=False,
                mode=ClimateMode.HEAT,
                fan_mode=FanMode.LOW,
                target_temperature=18.0,
                current_temperature=20.0,
            ),
        ],
    )


def test_discover_frame_shape() -> None:
    """发现报文应与设备协议一致（7 字节 + 校验和）。"""

    frame = ZhonghongProtocol(SLAVE).build_discover()
    assert len(frame) == 7
    assert frame[:6] == bytes((SLAVE, 0x50, 0x02, 0xFF, 0xFF, 0xFF))
    assert frame[6] == sum(frame[:6]) & 0xFF


def test_query_frame_shape() -> None:
    """查询报文应携带外机与内机地址。"""

    frame = ZhonghongProtocol(SLAVE).build_query(VrfUnitKey(0, 3))
    assert len(frame) == 7
    assert frame[:6] == bytes((SLAVE, 0x50, 0x01, 0x01, 0, 3))


def test_mode_control_sends_mode_then_power() -> None:
    """开机应先设模式再开机，关机只发开关帧。"""

    protocol = ZhonghongProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    frames = protocol.build_set_mode(key, VrfMode.COOL, None)
    assert len(frames) == 2
    assert frames[0][1] == 0x33  # 模式
    assert frames[0][2] == 0x01  # 制冷
    assert frames[1][1] == 0x31  # 开关
    assert frames[1][2] == 0x01  # 开机

    off_frames = protocol.build_set_mode(key, VrfMode.OFF, None)
    assert len(off_frames) == 1
    assert off_frames[0][1] == 0x31
    assert off_frames[0][2] == 0x00


def test_temperature_is_clamped() -> None:
    """目标温度应限制在 16-30 度。"""

    protocol = ZhonghongProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    assert protocol.build_set_temperature(key, 5, None)[0][2] == 16
    assert protocol.build_set_temperature(key, 99, None)[0][2] == 30
    assert protocol.build_set_temperature(key, 22.4, None)[0][2] == 22


def test_discover_response_parses_units(simulator: ZhonghongSimulator) -> None:
    """应从在线列表中解析出全部内机。"""

    protocol = ZhonghongProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_discover())
    assert response is not None
    report = protocol.feed(response)
    assert sorted(unit.indoor for unit in report.units) == [1, 2]
    assert report.updates == ()


def test_state_response_parses_values(simulator: ZhonghongSimulator) -> None:
    """应正确解析运行模式、目标温度与室温。"""

    protocol = ZhonghongProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    response = simulator.handle_frame(protocol.build_query(key))
    assert response is not None
    report = protocol.feed(response)
    assert len(report.updates) == 1
    update = report.updates[0]
    assert update.key == key
    assert update.power is True
    assert update.mode is VrfMode.COOL
    assert update.fan_mode is VrfFanMode.MEDIUM
    assert update.target_temperature == 24
    assert update.current_temperature == 27


def test_offline_unit_reports_off(simulator: ZhonghongSimulator) -> None:
    """关机状态应映射为 OFF 模式。"""

    protocol = ZhonghongProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 2)))
    assert response is not None
    update = protocol.feed(response).updates[0]
    assert update.power is False
    assert update.mode is VrfMode.OFF


def test_control_frames_are_accepted_by_device(simulator: ZhonghongSimulator) -> None:
    """控制报文应被设备接受并改变内机状态。"""

    protocol = ZhonghongProtocol(SLAVE)
    key = VrfUnitKey(0, 1)
    for frame in protocol.build_set_mode(key, VrfMode.HEAT, None):
        simulator.handle_frame(frame)
    for frame in protocol.build_set_temperature(key, 20, None):
        simulator.handle_frame(frame)
    for frame in protocol.build_set_fan(key, VrfFanMode.HIGH, None):
        simulator.handle_frame(frame)

    state = simulator.get_state((0, 1))
    assert state is not None
    assert state.power is True
    assert state.mode is ClimateMode.HEAT
    assert state.target_temperature == 20
    assert state.fan_mode is FanMode.HIGH


def test_state_merge_keeps_unknown_values() -> None:
    """未知字段应保留上一次的值。"""

    state = VrfUnitState(
        key=VrfUnitKey(0, 1),
        power=True,
        mode=VrfMode.COOL,
        fan_mode=VrfFanMode.AUTO,
        target_temperature=24,
        current_temperature=26,
    )
    from custom_components.vrf_bridge.protocol.base import VrfUnitUpdate

    merged = state.merge(VrfUnitUpdate(key=state.key, power=True))
    assert merged.target_temperature == 24
    assert merged.current_temperature == 26
    assert merged.fan_mode is VrfFanMode.AUTO


def test_noise_before_frame_is_skipped(simulator: ZhonghongSimulator) -> None:
    """前置噪声不应影响解析。"""

    protocol = ZhonghongProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_discover())
    assert response is not None
    report = protocol.feed(b"\x00\xff\x55" + response)
    assert len(report.units) == 2


def test_partial_frame_is_buffered(simulator: ZhonghongSimulator) -> None:
    """分片到达的报文应被正确拼装。"""

    protocol = ZhonghongProtocol(SLAVE)
    response = simulator.handle_frame(protocol.build_query(VrfUnitKey(0, 1)))
    assert response is not None
    report = protocol.feed(response[:5])
    assert report.updates == ()
    report = protocol.feed(response[5:])
    assert len(report.updates) == 1
