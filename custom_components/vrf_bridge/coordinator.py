"""VRF 网关协调器：维护总线连接与内机状态快照。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bus import BusSnapshot, VrfBus
from .const import CONF_IGNORED_UNITS, DOMAIN
from .protocol import (
    VrfFanMode,
    VrfMode,
    VrfProtocol,
    VrfUnitKey,
    VrfUnitState,
    VrfUnitUpdate,
)
from .transport import VrfTransport

_LOGGER = logging.getLogger(__name__)

# 首次快照等待时间；超时不会阻断安装，后台会继续扫描。
FIRST_SNAPSHOT_TIMEOUT = 10.0

# 连续多少次发现结果缺少该内机才判定为已下线。
OFFLINE_CONFIRMATIONS = 2

EntityAdder = Callable[[Iterable[VrfUnitKey]], None]


class VrfCoordinator(DataUpdateCoordinator[dict[VrfUnitKey, VrfUnitState]]):
    """把总线线程的快照转发给 Home Assistant。"""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        transport: VrfTransport,
        transport_label: str,
        slave_addr: int,
        brand: str,
        poll_interval: int,
        discovery_interval: int,
        allow_heat: bool,
        cleanup_offline: bool,
        ignored_units: Iterable[int] = (),
    ) -> None:
        """初始化协调器（尚不建立连接）。"""

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        self._transport_instance = transport
        self.transport_label = transport_label
        self.slave_addr = slave_addr
        self.brand = brand
        self.poll_interval = poll_interval
        self.discovery_interval = discovery_interval
        self.allow_heat = allow_heat
        self.cleanup_offline = cleanup_offline

        self.detected_brand: str | None = None
        self._bus: VrfBus | None = None
        self._entity_adder: EntityAdder | None = None
        self._known_units: set[VrfUnitKey] = set()
        self._ignored_units: set[int] = {int(item) for item in ignored_units}
        self._miss_count: dict[VrfUnitKey, int] = {}
        self._first_snapshot = asyncio.Event()
        self._snapshot: dict[VrfUnitKey, VrfUnitState] = {}

    # -- 生命周期 ---------------------------------------------------------

    async def async_start(self) -> None:
        """启动总线线程并等待第一份快照。"""

        self._bus = VrfBus(
            transport=self._transport_instance,
            slave_addr=self.slave_addr,
            brand=self.brand,
            poll_interval=self.poll_interval,
            discovery_interval=self.discovery_interval,
            on_snapshot=self._publish_from_thread,
            on_brand_detected=self._brand_detected_from_thread,
        )
        for address in self._ignored_units:
            self._bus.ignore_unit(address)
        self._bus.start()
        try:
            async with asyncio.timeout(FIRST_SNAPSHOT_TIMEOUT):
                await self._first_snapshot.wait()
        except TimeoutError:
            _LOGGER.warning("暂未收到 %s 的回应，将持续后台扫描", self.transport_label)

    async def async_stop(self) -> None:
        """停止总线线程。"""

        if self._bus is not None:
            self._bus.stop()
            self._bus = None

    # -- 数据 -------------------------------------------------------------

    async def _async_update_data(self) -> dict[VrfUnitKey, VrfUnitState]:
        """轮询由总线线程驱动，这里直接返回缓存快照。"""

        return self._snapshot

    def _publish_from_thread(self, snapshot: BusSnapshot) -> None:
        """总线线程回调：切换到事件循环线程处理。"""

        self.hass.loop.call_soon_threadsafe(self._apply_snapshot, snapshot)

    def _brand_detected_from_thread(self, brand: str) -> None:
        """总线线程回调：记录识别到的协议。"""

        self.hass.loop.call_soon_threadsafe(self._set_detected_brand, brand)

    @callback
    def _set_detected_brand(self, brand: str) -> None:
        """记录识别到的协议。"""

        self.detected_brand = brand

    @callback
    def _apply_snapshot(self, snapshot: BusSnapshot) -> None:
        """应用新快照并通知订阅者。"""

        self._snapshot = snapshot.states or {}
        self._first_snapshot.set()

        new_units = [
            key
            for key in snapshot.units
            if key not in self._known_units and key.indoor not in self._ignored_units
        ]
        if new_units:
            self._known_units.update(new_units)
            if self._entity_adder is not None:
                self._entity_adder(new_units)

        if snapshot.discovered:
            self._handle_offline_units(snapshot.units)

        self.async_set_updated_data(self._snapshot)

    @callback
    def _handle_offline_units(self, present: Iterable[VrfUnitKey]) -> None:
        """清理已经不再上报的内机实体。"""

        present_set = set(present)
        for key in list(self._known_units):
            if key in present_set:
                self._miss_count.pop(key, None)
                continue
            count = self._miss_count.get(key, 0) + 1
            self._miss_count[key] = count
            if count >= OFFLINE_CONFIRMATIONS and self.cleanup_offline:
                self._remove_unit_entities(key.indoor)
                self._known_units.discard(key)
                self._miss_count.pop(key, None)
                _LOGGER.info("内机 %s 已不再上报，已移除对应实体", key)

    def set_entity_adder(self, adder: EntityAdder) -> None:
        """注册新增内机时的实体创建回调。"""

        self._entity_adder = adder

    @property
    def brand_label(self) -> str:
        """返回品牌显示名（自动探测时优先显示识别结果）。"""

        from .const import BRAND_LABELS

        brand = self.detected_brand or self.brand
        return BRAND_LABELS.get(brand, brand)

    def known_units(self) -> tuple[VrfUnitKey, ...]:
        """返回需要展示的内机地址（已忽略的不在内）。"""

        return tuple(
            key for key in self._known_units if key.indoor not in self._ignored_units
        )

    def state_for(self, key: VrfUnitKey) -> VrfUnitState | None:
        """返回指定内机的最后一次状态。"""

        return self._snapshot.get(key)

    @property
    def ignored_units(self) -> tuple[int, ...]:
        """返回被忽略的内机地址。"""

        return tuple(sorted(self._ignored_units))

    # -- 实体管理 ---------------------------------------------------------

    def _unit_entity_ids(self, address: int) -> list[str]:
        """返回指定内机地址对应的实体 ID。"""

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, self.entry.entry_id)
        return [
            item.entity_id
            for item in entries
            if item.unique_id and item.unique_id.endswith(f"_{address}")
        ]

    @callback
    def _remove_unit_entities(self, address: int) -> list[str]:
        """从实体注册表中删除指定内机的实体。"""

        registry = er.async_get(self.hass)
        removed: list[str] = []
        for entity_id in self._unit_entity_ids(address):
            registry.async_remove(entity_id)
            removed.append(entity_id)
        return removed

    @callback
    def async_remove_unit(self, address: int) -> list[str]:
        """移除指定内机的实体，并加入忽略列表避免再次生成。"""

        removed = self._remove_unit_entities(address)
        self._ignored_units.add(int(address))
        if self._bus is not None:
            self._bus.ignore_unit(address)
        self._persist_ignored_units()
        for key in [key for key in self._known_units if key.indoor == address]:
            self._known_units.discard(key)
            self._miss_count.pop(key, None)
        _LOGGER.info("已移除内机 %s 的实体并加入忽略列表", address)
        return removed

    @callback
    def async_restore_unit(self, address: int) -> None:
        """取消忽略，恢复该内机的实体。"""

        self._ignored_units.discard(int(address))
        if self._bus is not None:
            self._bus.unignore_unit(address)
        self._persist_ignored_units()

    @callback
    def _persist_ignored_units(self) -> None:
        """把忽略列表写入配置项。"""

        options = {
            **self.entry.options,
            CONF_IGNORED_UNITS: sorted(self._ignored_units),
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    # -- 控制 -------------------------------------------------------------

    async def async_request_rescan(self) -> None:
        """请求立即重新扫描内机。"""

        if self._bus is not None:
            self._bus.request_rescan()

    async def async_send_control(
        self, frames: Iterable[bytes], unit: VrfUnitKey | None = None
    ) -> None:
        """下发控制报文（立即优先于轮询执行）。"""

        if self._bus is None:
            raise ConnectionError("VRF 总线未启动")
        self._bus.submit(frames, unit)

    @callback
    def apply_optimistic(self, key: VrfUnitKey, update: VrfUnitUpdate) -> None:
        """乐观更新：控制下发后先刷新界面，等待下一次轮询校准。"""

        current = self._snapshot.get(key)
        if current is None:
            return
        snapshot = dict(self._snapshot)
        snapshot[key] = current.merge(update)
        self._snapshot = snapshot
        self.async_set_updated_data(snapshot)

    def build_set_mode(self, key: VrfUnitKey, mode: VrfMode) -> list[bytes]:
        """构造模式控制报文。"""

        return self._protocol().build_set_mode(key, mode, self.state_for(key))

    def build_set_temperature(self, key: VrfUnitKey, temperature: float) -> list[bytes]:
        """构造目标温度控制报文。"""

        return self._protocol().build_set_temperature(
            key, temperature, self.state_for(key)
        )

    def build_set_fan(self, key: VrfUnitKey, fan_mode: VrfFanMode) -> list[bytes]:
        """构造风速控制报文。"""

        return self._protocol().build_set_fan(key, fan_mode, self.state_for(key))

    def _protocol(self) -> VrfProtocol:
        """返回当前生效的协议实例。"""

        if self._bus is None:
            from .protocol import create_protocol

            return create_protocol(self.brand, self.slave_addr)
        return self._bus.protocol
