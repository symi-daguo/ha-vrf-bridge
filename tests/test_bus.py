"""总线线程测试：通过伪终端与真实 pyserial 跑完整收发流程。"""

from __future__ import annotations

import time

from custom_components.vrf_bridge.bus import BusSnapshot, VrfBus
from custom_components.vrf_bridge.protocol.base import VrfFanMode, VrfMode, VrfUnitKey
from custom_components.vrf_bridge.transport import SerialTransport

from .support.loopback import Loopback
from .support.simulator.demry import DemrySimulator
from .support.simulator.model import ClimateMode, ClimateState, FanMode
from .support.simulator.zhonghong import ZhonghongSimulator

SLAVE = 1


def _wait_for(predicate, timeout: float = 8.0) -> bool:
    """轮询等待条件成立。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_bus_discovers_units_and_reads_state() -> None:
    """总线应自动发现内机并读取状态。"""

    states = [
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
    ]
    loopback = Loopback(lambda fd: ZhonghongSimulator(fd, states=states))
    snapshots: list[BusSnapshot] = []
    bus = VrfBus(
        transport=SerialTransport(loopback.slave_path, 9600),
        slave_addr=SLAVE,
        brand="zhonghong",
        poll_interval=1,
        discovery_interval=30,
        on_snapshot=lambda snapshot: snapshots.append(snapshot),
    )
    loopback.start()
    bus.start()
    try:
        assert _wait_for(lambda: len(snapshots) >= 2)
        latest = snapshots[-1]
        assert set(latest.units) == {VrfUnitKey(0, 1), VrfUnitKey(0, 2)}
        first = latest.states[VrfUnitKey(0, 1)]
        assert first.mode is VrfMode.COOL
        assert first.fan_mode is VrfFanMode.MEDIUM
        assert first.target_temperature == 24
        assert first.current_temperature == 27
    finally:
        bus.stop()
        loopback.stop()


def test_bus_sends_control_command() -> None:
    """控制报文应下发到设备并改变内机状态。"""

    states = [
        ClimateState(
            address=1,
            power=False,
            mode=ClimateMode.COOL,
            fan_mode=FanMode.AUTO,
            target_temperature=24.0,
            current_temperature=26.0,
        )
    ]
    loopback = Loopback(lambda fd: ZhonghongSimulator(fd, states=states))
    snapshots: list[BusSnapshot] = []
    bus = VrfBus(
        transport=SerialTransport(loopback.slave_path, 9600),
        slave_addr=SLAVE,
        brand="zhonghong",
        poll_interval=1,
        discovery_interval=30,
        on_snapshot=lambda snapshot: snapshots.append(snapshot),
    )
    loopback.start()
    bus.start()
    try:
        assert _wait_for(lambda: bool(snapshots))
        from custom_components.vrf_bridge.protocol.zhonghong import ZhonghongProtocol

        protocol = ZhonghongProtocol(SLAVE)
        key = VrfUnitKey(0, 1)
        bus.submit(protocol.build_set_mode(key, VrfMode.HEAT, None))
        bus.submit(protocol.build_set_temperature(key, 20, None))

        def _device_ready() -> bool:
            """设备端已应用开机与温度指令。"""

            state = loopback.simulator.get_state((0, 1))
            return (
                state is not None
                and state.power is True
                and state.mode is ClimateMode.HEAT
                and state.target_temperature == 20
            )

        assert _wait_for(_device_ready), "设备端未收到控制指令"
        state = loopback.simulator.get_state((0, 1))
        assert state is not None
        assert state.power is True
        assert state.target_temperature == 20
    finally:
        bus.stop()
        loopback.stop()


def test_bus_detects_brand_automatically() -> None:
    """自动探测应识别出实际协议。"""

    states = [ClimateState(address=3, power=True, mode=ClimateMode.COOL)]
    loopback = Loopback(lambda fd: DemrySimulator(fd, states=states))
    detected: list[str] = []
    snapshots: list[BusSnapshot] = []
    bus = VrfBus(
        transport=SerialTransport(loopback.slave_path, 9600),
        slave_addr=SLAVE,
        brand="auto",
        poll_interval=1,
        discovery_interval=30,
        on_snapshot=lambda snapshot: snapshots.append(snapshot),
        on_brand_detected=detected.append,
    )
    loopback.start()
    bus.start()
    try:
        assert _wait_for(lambda: bool(detected))
        assert detected[0] == "demry"
        assert _wait_for(lambda: bool(snapshots))
        assert VrfUnitKey(0, 3) in snapshots[-1].units
    finally:
        bus.stop()
        loopback.stop()


def test_bus_survives_missing_device() -> None:
    """设备不可用时线程不应崩溃。"""

    snapshots: list[BusSnapshot] = []
    bus = VrfBus(
        transport=SerialTransport("/dev/does-not-exist-vrf", 9600),
        slave_addr=SLAVE,
        brand="zhonghong",
        poll_interval=1,
        discovery_interval=30,
        on_snapshot=lambda snapshot: snapshots.append(snapshot),
    )
    bus.start()
    time.sleep(1.0)
    bus.stop()
    assert snapshots == []
