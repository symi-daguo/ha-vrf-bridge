"""VRF 内机空调实体。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAX_TEMP, MIN_TEMP, TARGET_TEMP_STEP
from .coordinator import VrfCoordinator
from .protocol import VrfFanMode, VrfMode, VrfUnitKey, VrfUnitUpdate

_LOGGER = logging.getLogger(__name__)

VRF_TO_HVAC_MODE = {
    VrfMode.OFF: HVACMode.OFF,
    VrfMode.COOL: HVACMode.COOL,
    VrfMode.HEAT: HVACMode.HEAT,
    VrfMode.DRY: HVACMode.DRY,
    VrfMode.FAN_ONLY: HVACMode.FAN_ONLY,
}
HVAC_TO_VRF_MODE = {value: key for key, value in VRF_TO_HVAC_MODE.items()}

# Home Assistant 2026 起不再提供 FanMode 枚举，风速统一使用约定字符串。
FAN_AUTO = "auto"
FAN_LOW = "low"
FAN_MEDIUM = "medium"
FAN_HIGH = "high"

VRF_TO_HVAC_FAN = {
    VrfFanMode.AUTO: FAN_AUTO,
    VrfFanMode.LOW: FAN_LOW,
    VrfFanMode.MEDIUM: FAN_MEDIUM,
    VrfFanMode.HIGH: FAN_HIGH,
}
HVAC_TO_VRF_FAN = {value: key for key, value in VRF_TO_HVAC_FAN.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """为已发现的内机创建空调实体。"""

    coordinator: VrfCoordinator = entry.runtime_data

    entities = [
        VrfClimate(coordinator, key) for key in sorted(coordinator.known_units())
    ]
    async_add_entities(entities)

    def _add_new(new_keys: Any) -> None:
        """运行时发现新内机时补充实体。"""

        async_add_entities([VrfClimate(coordinator, key) for key in new_keys])

    coordinator.set_entity_adder(_add_new)


class VrfClimate(CoordinatorEntity[VrfCoordinator], ClimateEntity):
    """一台 VRF 内机对应的空调实体。"""

    _attr_has_entity_name = True
    _attr_translation_key = "indoor_unit"
    _attr_target_temperature_step = TARGET_TEMP_STEP
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = 1
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: VrfCoordinator, key: VrfUnitKey) -> None:
        """初始化实体。"""

        super().__init__(coordinator)
        self._key = key
        self._last_active_mode = HVACMode.COOL
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{key.outdoor}_{key.indoor}"
        )
        self._attr_translation_placeholders = {"address": str(key.indoor)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            translation_key="vrf_gateway",
            translation_placeholders={
                "brand": coordinator.brand_label,
                "port": coordinator.transport_label,
            },
        )

    # -- 状态 -------------------------------------------------------------

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """返回支持的运行模式（可禁止制热）。"""

        modes = [HVACMode.OFF]
        if self.coordinator.allow_heat:
            modes.append(HVACMode.HEAT)
        modes.extend([HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY])
        return modes

    @property
    def fan_modes(self) -> list[str]:
        """返回支持的风速。"""

        return [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """返回当前运行模式。"""

        state = self.coordinator.state_for(self._key)
        if state is None:
            return None
        return VRF_TO_HVAC_MODE.get(state.mode)

    @property
    def current_temperature(self) -> float | None:
        """返回室温。"""

        state = self.coordinator.state_for(self._key)
        return state.current_temperature if state else None

    @property
    def target_temperature(self) -> float | None:
        """返回目标温度。"""

        state = self.coordinator.state_for(self._key)
        return state.target_temperature if state else None

    @property
    def fan_mode(self) -> str | None:
        """返回当前风速。"""

        state = self.coordinator.state_for(self._key)
        if state is None:
            return None
        return VRF_TO_HVAC_FAN.get(state.fan_mode)

    @property
    def available(self) -> bool:
        """已经收到过该内机数据才视为可用。"""

        return self.coordinator.state_for(self._key) is not None

    # -- 控制 -------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """设置运行模式。"""

        mode = HVAC_TO_VRF_MODE.get(hvac_mode)
        if mode is None:
            return
        if mode is VrfMode.HEAT and not self.coordinator.allow_heat:
            mode = VrfMode.COOL
            hvac_mode = HVACMode.COOL

        frames = self.coordinator.build_set_mode(self._key, mode)
        await self.coordinator.async_send_control(frames, self._key)
        if hvac_mode is not HVACMode.OFF:
            self._last_active_mode = hvac_mode
        self.coordinator.apply_optimistic(
            self._key,
            VrfUnitUpdate(key=self._key, power=mode is not VrfMode.OFF, mode=mode),
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """设置目标温度。"""

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        temperature = float(temperature)
        frames = self.coordinator.build_set_temperature(self._key, temperature)
        await self.coordinator.async_send_control(frames, self._key)
        self.coordinator.apply_optimistic(
            self._key,
            VrfUnitUpdate(key=self._key, target_temperature=temperature),
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """设置风速。"""

        mode = HVAC_TO_VRF_FAN.get(fan_mode)
        if mode is None:
            return
        frames = self.coordinator.build_set_fan(self._key, mode)
        await self.coordinator.async_send_control(frames, self._key)
        self.coordinator.apply_optimistic(
            self._key, VrfUnitUpdate(key=self._key, fan_mode=mode)
        )

    async def async_turn_on(self) -> None:
        """开机：恢复上一次的非关机模式。"""

        await self.async_set_hvac_mode(self._last_active_mode)

    async def async_turn_off(self) -> None:
        """关机。"""

        await self.async_set_hvac_mode(HVACMode.OFF)
