"""VRF 485 总线工作线程。

所有串口读写都发生在该线程内，Home Assistant 事件循环不会被阻塞；
控制命令优先于轮询，保证用户操作能立即下发。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .const import (
    BRAND_AUTO,
    DISCOVERY_FRAME_TIMEOUT,
    FRAME_WAIT_TIMEOUT,
    MAX_UNITS_PER_CYCLE,
    RECONNECT_INTERVAL,
)
from .protocol import (
    VrfProtocol,
    VrfReport,
    VrfUnitKey,
    VrfUnitState,
    VrfUnitUpdate,
    create_protocol,
    protocol_brands,
)
from .transport import VrfTransport

_LOGGER = logging.getLogger(__name__)

SnapshotCallback = Callable[["BusSnapshot"], None]
BrandCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class BusCommand:
    """一条待执行的总线命令。"""

    frames: tuple[bytes, ...] = ()
    rescan: bool = False


@dataclass(frozen=True, slots=True)
class BusSnapshot:
    """一次总线采样结果。

    ``units`` 是已发现的内机地址（含尚未读到状态的），``states`` 是本次
    读到状态的内机，两者分开是为了让实体在拿到首个状态前就能被创建。
    """

    units: tuple[VrfUnitKey, ...] = ()
    states: dict[VrfUnitKey, VrfUnitState] | None = None
    discovered: bool = False


class VrfBus:
    """串行访问一条 485 总线的工作线程。"""

    def __init__(
        self,
        *,
        transport: VrfTransport,
        slave_addr: int,
        brand: str,
        poll_interval: int,
        discovery_interval: int,
        on_snapshot: SnapshotCallback,
        on_brand_detected: BrandCallback | None = None,
    ) -> None:
        """初始化总线（不会立即建立连接）。"""

        self._transport = transport
        self._slave_addr = slave_addr
        self._brand = brand
        self._poll_interval = poll_interval
        self._discovery_interval = discovery_interval
        self._on_snapshot = on_snapshot
        self._on_brand_detected = on_brand_detected

        self._protocol: VrfProtocol = create_protocol(
            brand if brand != BRAND_AUTO else protocol_brands()[0], slave_addr
        )
        self._detected_brand: str | None = None
        self._states: dict[VrfUnitKey, VrfUnitState] = {}
        self._units: dict[VrfUnitKey, None] = {}
        self._commands: queue.Queue[BusCommand] = queue.Queue()
        self._ignored: set[int] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_discovery = 0.0
        self._next_cycle = 0.0
        self._query_cursor = 0

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        """启动工作线程。"""

        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vrf-bus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止工作线程并关闭串口。"""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self._close_transport()

    def submit(self, frames: Iterable[bytes]) -> None:
        """提交一组待发送的控制报文。"""

        payload = tuple(bytes(frame) for frame in frames if frame)
        if payload:
            self._commands.put(BusCommand(frames=payload))

    def request_rescan(self) -> None:
        """请求立即重新扫描内机。"""

        self._commands.put(BusCommand(rescan=True))

    def ignore_unit(self, address: int) -> None:
        """忽略指定内机地址（不再查询与上报）。"""

        self._ignored.add(int(address))

    def unignore_unit(self, address: int) -> None:
        """取消忽略。"""

        self._ignored.discard(int(address))

    def units(self) -> tuple[VrfUnitKey, ...]:
        """返回已知的内机地址。"""

        return tuple(self._units)

    @property
    def protocol(self) -> VrfProtocol:
        """返回当前生效的协议实例。"""

        return self._protocol

    # -- 工作线程 ---------------------------------------------------------

    def _run(self) -> None:
        """总线主循环。"""

        while not self._stop.is_set():
            try:
                self._ensure_transport()
                timeout = max(0.0, self._next_cycle - time.monotonic())
                try:
                    command = self._commands.get(timeout=timeout)
                except queue.Empty:
                    command = None

                if command is not None:
                    if command.rescan:
                        self._last_discovery = 0.0
                        self._next_cycle = time.monotonic()
                    elif command.frames:
                        self._send_frames(list(command.frames))
                    continue

                self._run_cycle()
                self._next_cycle = time.monotonic() + self._poll_interval
            except Exception as err:  # noqa: BLE001 - 异常统一按断线处理
                _LOGGER.warning("VRF 总线异常，将重连: %s", err)
                self._close_transport()
                self._stop.wait(RECONNECT_INTERVAL)

    def _run_cycle(self) -> None:
        """执行一轮「发现 + 查询」。"""

        now = time.monotonic()
        if not self._units or now - self._last_discovery >= self._discovery_interval:
            self._discover()
        self._query_units()

    # -- 链路 -------------------------------------------------------------

    def _ensure_transport(self) -> None:
        """确保链路已建立。"""

        if self._transport.is_open:
            return
        self._close_transport()
        self._transport.open()

    def _close_transport(self) -> None:
        """关闭链路。"""

        self._transport.close()

    def _write(self, frame: bytes) -> None:
        """发送一帧数据。"""

        self._transport.reset_input()
        self._transport.write(frame)

    def _read_report(self, protocol: VrfProtocol, timeout: float) -> VrfReport | None:
        """读取并解析回应，返回 None 表示超时无有效数据。"""

        deadline = time.monotonic() + timeout
        units: tuple[VrfUnitKey, ...] = ()
        updates: tuple[VrfUnitUpdate, ...] = ()
        while time.monotonic() < deadline:
            chunk = self._transport.read(64)
            if not chunk:
                continue
            report = protocol.feed(chunk)
            units = units + report.units
            updates = updates + report.updates
            if report.units or report.updates:
                return VrfReport(units=units, updates=updates)
        protocol.flush()
        if units or updates:
            return VrfReport(units=units, updates=updates)
        return None

    def _send_frames(self, frames: list[bytes]) -> None:
        """发送控制报文并吸收设备的确认帧。"""

        for frame in frames:
            self._write(frame)
            self._read_report(self._protocol, 0.15)

    # -- 协议动作 ---------------------------------------------------------

    def _discover(self) -> bool:
        """发送内机发现报文，返回是否收到回应。"""

        candidates = (
            [self._brand] if self._brand != BRAND_AUTO else list(protocol_brands())
        )
        for brand in candidates:
            protocol = (
                self._protocol
                if brand == self._protocol.key
                else create_protocol(brand, self._slave_addr)
            )
            try:
                report = self._read_report_after_write(
                    protocol, protocol.build_discover(), DISCOVERY_FRAME_TIMEOUT
                )
            except Exception as err:  # noqa: BLE001 - 换协议继续尝试
                _LOGGER.debug("协议 %s 探测失败: %s", brand, err)
                continue
            if report is None:
                continue
            if protocol is not self._protocol:
                self._protocol = protocol
            if self._detected_brand != protocol.key:
                self._detected_brand = protocol.key
                _LOGGER.info("已识别 VRF 协议: %s", protocol.label)
                if self._on_brand_detected is not None:
                    self._on_brand_detected(protocol.key)
            self._last_discovery = time.monotonic()
            self._apply_report(report, discovery=True)
            return True
        return False

    def _read_report_after_write(
        self, protocol: VrfProtocol, frame: bytes, timeout: float
    ) -> VrfReport | None:
        """发送报文并等待回应。"""

        self._write(frame)
        return self._read_report(protocol, timeout)

    def _query_units(self) -> None:
        """轮询内机状态（每次最多 MAX_UNITS_PER_CYCLE 台）。"""

        units = [key for key in self._units if key.indoor not in self._ignored]
        if not units:
            return
        count = min(MAX_UNITS_PER_CYCLE, len(units))
        for offset in range(count):
            index = (self._query_cursor + offset) % len(units)
            key = units[index]
            try:
                report = self._read_report_after_write(
                    self._protocol, self._protocol.build_query(key), FRAME_WAIT_TIMEOUT
                )
            except Exception as err:  # noqa: BLE001 - 单台失败继续下一台
                _LOGGER.debug("查询内机 %s 失败: %s", key, err)
                continue
            if report is not None:
                self._apply_report(report)
        self._query_cursor = (self._query_cursor + count) % len(units)

    def _apply_report(self, report: VrfReport, discovery: bool = False) -> None:
        """合并解析结果并向外推送快照。"""

        for key in report.units:
            if key not in self._units:
                self._units[key] = None
                _LOGGER.info("发现内机: %s", key)
        for update in report.updates:
            current = self._states.get(update.key)
            if current is None:
                current = VrfUnitState(key=update.key)
            self._states[update.key] = current.merge(update)
        self._on_snapshot(
            BusSnapshot(
                units=tuple(self._units),
                states=dict(self._states),
                discovered=discovery,
            )
        )
