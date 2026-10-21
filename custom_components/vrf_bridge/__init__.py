"""VRF 中央空调网关集成。

支持通过本地串口（USB-RS485 / RS232）或局域网 TCP / UDP 直接与 VRF 室外机
通讯，自动发现内机并在 Home Assistant 中生成对应的空调实体。
"""

from __future__ import annotations

import logging

try:  # Home Assistant 2026.3+ 用 probatio 提供 voluptuous 兼容层
    import probatio as vol
except ImportError:  # pragma: no cover - 兼容旧版 Home Assistant
    import voluptuous as vol  # type: ignore[no-redef]
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ADDRESS,
    ATTR_DEVICE_ID,
    CONF_ALLOW_HEAT,
    CONF_BAUD_RATE,
    CONF_BRAND,
    CONF_CLEANUP_OFFLINE,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_INTERVAL,
    CONF_HOST,
    CONF_IGNORED_UNITS,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ADDR,
    CONF_STOPBITS,
    DEFAULT_ALLOW_HEAT,
    DEFAULT_CLEANUP_OFFLINE,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    SERIAL_STOPBITS,
    SERVICE_REMOVE_UNIT,
    SERVICE_RESCAN,
    SERVICE_RESTORE_UNIT,
)
from .coordinator import VrfCoordinator
from .transport import (
    CONNECTION_TCP,
    CONNECTION_UDP,
    SerialTransport,
    TcpTransport,
    UdpTransport,
    VrfTransport,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE]

# 本集成只能通过配置项添加，不支持 YAML 配置。
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

RESCAN_SCHEMA = vol.Schema({vol.Optional(ATTR_DEVICE_ID): cv.string})
UNIT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ADDRESS): vol.Coerce(int),
        vol.Optional(ATTR_DEVICE_ID): cv.string,
    }
)


def _build_transport(values: dict) -> tuple[VrfTransport, str]:
    """按配置构造传输对象与显示名称。"""

    kind = values[CONF_CONNECTION_TYPE]
    if kind == CONNECTION_TCP:
        host, port = str(values[CONF_HOST]), int(values[CONF_PORT])
        return TcpTransport(host, port), f"{host}:{port}"
    if kind == CONNECTION_UDP:
        host, port = str(values[CONF_HOST]), int(values[CONF_PORT])
        return UdpTransport(host, port), f"{host}:{port}"
    port_path = str(values[CONF_SERIAL_PORT])
    stopbits = float(values.get(CONF_STOPBITS, SERIAL_STOPBITS))
    return (
        SerialTransport(port_path, int(values[CONF_BAUD_RATE]), stopbits=stopbits),
        port_path,
    )


def _coordinators_for(
    hass: HomeAssistant, device_id: str | None
) -> list[VrfCoordinator]:
    """按设备 ID 筛选协调器；未指定时返回全部。"""

    entries: dict[str, VrfCoordinator] = hass.data.get(DOMAIN, {})
    if not device_id:
        return list(entries.values())
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []
    return [
        entries[entry_id] for entry_id in device.config_entries if entry_id in entries
    ]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """注册集成提供的服务。"""

    hass.data.setdefault(DOMAIN, {})

    async def _handle_rescan(call: ServiceCall) -> None:
        """立即重新扫描内机。"""

        for coordinator in _coordinators_for(hass, call.data.get(ATTR_DEVICE_ID)):
            await coordinator.async_request_rescan()

    async def _handle_remove_unit(call: ServiceCall) -> None:
        """移除指定内机的实体。"""

        address = int(call.data[ATTR_ADDRESS])
        for coordinator in _coordinators_for(hass, call.data.get(ATTR_DEVICE_ID)):
            coordinator.async_remove_unit(address)

    async def _handle_restore_unit(call: ServiceCall) -> None:
        """恢复被移除的内机实体。"""

        address = int(call.data[ATTR_ADDRESS])
        for coordinator in _coordinators_for(hass, call.data.get(ATTR_DEVICE_ID)):
            coordinator.async_restore_unit(address)

    hass.services.async_register(
        DOMAIN, SERVICE_RESCAN, _handle_rescan, schema=RESCAN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_UNIT, _handle_remove_unit, schema=UNIT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE_UNIT, _handle_restore_unit, schema=UNIT_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置一条 VRF 网关配置。"""

    options = {**entry.data, **entry.options}
    transport, label = _build_transport(options)
    coordinator = VrfCoordinator(
        hass,
        entry,
        transport=transport,
        transport_label=label,
        slave_addr=int(options[CONF_SLAVE_ADDR]),
        brand=str(options[CONF_BRAND]),
        poll_interval=int(options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        discovery_interval=int(
            options.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL)
        ),
        allow_heat=bool(options.get(CONF_ALLOW_HEAT, DEFAULT_ALLOW_HEAT)),
        cleanup_offline=bool(
            options.get(CONF_CLEANUP_OFFLINE, DEFAULT_CLEANUP_OFFLINE)
        ),
        ignored_units=options.get(CONF_IGNORED_UNITS, []),
    )
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await coordinator.async_start()
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载一条 VRF 网关配置。"""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: VrfCoordinator = entry.runtime_data
        await coordinator.async_stop()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """配置发生变化后重新加载。"""

    await hass.config_entries.async_reload(entry.entry_id)
