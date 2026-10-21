"""VRF 网关诊断信息。"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import VrfCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回配置项诊断数据。"""

    coordinator: VrfCoordinator = entry.runtime_data
    units: dict[str, Any] = {}
    for key, state in (coordinator.data or {}).items():
        units[str(key)] = {
            "power": state.power,
            "mode": state.mode,
            "fan_mode": state.fan_mode,
            "target_temperature": state.target_temperature,
            "current_temperature": state.current_temperature,
        }
    return {
        "config": {"data": dict(entry.data), "options": dict(entry.options)},
        "detected_brand": coordinator.detected_brand,
        "units": units,
    }
