"""VRF 中央空调网关集成的配置流程。"""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    BAUD_RATES,
    BRAND_AUTO,
    BRAND_LABELS,
    CONF_ALLOW_HEAT,
    CONF_BAUD_RATE,
    CONF_BRAND,
    CONF_CLEANUP_OFFLINE,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_INTERVAL,
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ADDR,
    CONF_STOPBITS,
    DEFAULT_ALLOW_HEAT,
    DEFAULT_BAUD_RATE,
    DEFAULT_CLEANUP_OFFLINE,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SLAVE_ADDR,
    DOMAIN,
    MAX_DISCOVERY_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_DISCOVERY_INTERVAL,
    MIN_POLL_INTERVAL,
    SERIAL_STOPBITS,
)
from .protocol import protocol_brands
from .transport import (
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    CONNECTION_UDP,
    SerialTransport,
    TcpTransport,
    UdpTransport,
    VrfTransport,
)

_LOGGER = logging.getLogger(__name__)

BRAND_OPTIONS = [BRAND_AUTO, *protocol_brands()]
CONNECTION_OPTIONS = [CONNECTION_SERIAL, CONNECTION_TCP, CONNECTION_UDP]

# 探测结果
PROBE_OK = "ok"
PROBE_NO_PORT = "no_port"
PROBE_NO_RESPONSE = "no_response"

# 属于「连接参数」的键：改动后需要重载集成
CONNECTION_KEYS = (
    CONF_BRAND,
    CONF_CONNECTION_TYPE,
    CONF_SERIAL_PORT,
    CONF_BAUD_RATE,
    CONF_HOST,
    CONF_PORT,
    CONF_SLAVE_ADDR,
    CONF_STOPBITS,
)

# 属于「运行参数」的键：存进 options
TUNING_KEYS = (
    CONF_POLL_INTERVAL,
    CONF_DISCOVERY_INTERVAL,
    CONF_ALLOW_HEAT,
    CONF_CLEANUP_OFFLINE,
)


def _detect_serial_ports() -> list[str]:
    """列出系统可见的串口设备（阻塞调用，必须放在执行器里执行）。"""

    try:
        from serial.tools import list_ports

        return [port.device for port in list_ports.comports()]
    except Exception:  # noqa: BLE001 - 串口未安装时返回空列表
        return []


async def _async_detect_serial_ports(hass: HomeAssistant) -> list[str]:
    """在执行器中枚举串口，避免阻塞 Home Assistant 事件循环。"""

    return await hass.async_add_executor_job(_detect_serial_ports)


def _build_transport(values: dict[str, Any]) -> VrfTransport:
    """按配置构造传输对象。"""

    kind = values[CONF_CONNECTION_TYPE]
    if kind == CONNECTION_TCP:
        return TcpTransport(str(values[CONF_HOST]), int(values[CONF_PORT]))
    if kind == CONNECTION_UDP:
        return UdpTransport(str(values[CONF_HOST]), int(values[CONF_PORT]))
    return SerialTransport(
        str(values[CONF_SERIAL_PORT]),
        int(values[CONF_BAUD_RATE]),
        stopbits=float(values.get(CONF_STOPBITS, SERIAL_STOPBITS)),
    )


def _blocking_probe(
    transport: VrfTransport, brand: str, slave_addr: int, timeout: float
) -> tuple[str, str | None, int]:
    """在阻塞线程中探测总线，返回 (状态, 品牌, 内机数量)。"""

    from .protocol import create_protocol

    candidates = [brand] if brand != BRAND_AUTO else list(protocol_brands())
    per_candidate = max(0.5, timeout / max(1, len(candidates)))
    try:
        transport.open()
    except Exception as err:  # noqa: BLE001 - 端口不可用
        _LOGGER.debug("链路探测失败: %s", err)
        return PROBE_NO_PORT, None, 0

    try:
        for candidate in candidates:
            protocol = create_protocol(candidate, slave_addr)
            try:
                transport.reset_input()
                transport.write(protocol.build_discover())
            except Exception:  # noqa: BLE001 - 继续尝试下一个协议
                continue
            deadline = time.monotonic() + per_candidate
            while time.monotonic() < deadline:
                chunk = transport.read(64)
                if not chunk:
                    continue
                report = protocol.feed(chunk)
                if report.units:
                    return PROBE_OK, candidate, len(report.units)
    except Exception as err:  # noqa: BLE001 - 读取中断
        _LOGGER.debug("链路读取中断: %s", err)
    finally:
        transport.close()
    return PROBE_NO_RESPONSE, None, 0


async def _async_probe(
    hass: HomeAssistant, values: dict[str, Any]
) -> tuple[str, str | None, int]:
    """探测总线（放入执行器，避免阻塞事件循环）。"""

    return await hass.async_add_executor_job(
        _blocking_probe,
        _build_transport(values),
        values[CONF_BRAND],
        int(values[CONF_SLAVE_ADDR]),
        6.0,
    )


def _normalize(values: dict[str, Any]) -> dict[str, Any]:
    """统一字段类型。"""

    result = dict(values)
    if CONF_BAUD_RATE in result:
        result[CONF_BAUD_RATE] = int(result[CONF_BAUD_RATE])
    if CONF_STOPBITS in result:
        result[CONF_STOPBITS] = float(result[CONF_STOPBITS])
    if CONF_SLAVE_ADDR in result:
        result[CONF_SLAVE_ADDR] = int(result[CONF_SLAVE_ADDR])
    if CONF_PORT in result:
        # NumberSelector 回填的是浮点数，端口必须是整数。
        result[CONF_PORT] = int(result[CONF_PORT])
    if CONF_POLL_INTERVAL in result:
        result[CONF_POLL_INTERVAL] = int(result[CONF_POLL_INTERVAL])
    if CONF_DISCOVERY_INTERVAL in result:
        result[CONF_DISCOVERY_INTERVAL] = int(result[CONF_DISCOVERY_INTERVAL])
    return result


def _connection_data(values: dict[str, Any]) -> dict[str, Any]:
    """取出连接参数。"""

    return {key: values[key] for key in CONNECTION_KEYS if key in values}


def _tuning_options(values: dict[str, Any]) -> dict[str, Any]:
    """取出运行参数。"""

    return {key: values[key] for key in TUNING_KEYS if key in values}


def _entry_title(values: dict[str, Any]) -> str:
    """按配置生成配置项标题。"""

    brand = values[CONF_BRAND]
    title = f"VRF {BRAND_LABELS.get(brand, brand)}"
    if values[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL:
        return f"{title} ({values[CONF_SERIAL_PORT]})"
    return f"{title} ({values[CONF_HOST]}:{values[CONF_PORT]})"


def _identity(values: dict[str, Any]) -> str:
    """返回网关的唯一标识（同一链路不允许重复添加）。"""

    if values[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL:
        return f"{DOMAIN}:serial:{values[CONF_SERIAL_PORT]}"
    kind = values[CONF_CONNECTION_TYPE]
    return f"{DOMAIN}:{kind}:{values[CONF_HOST]}:{values[CONF_PORT]}"


def _brand_selector(defaults: dict[str, Any]) -> Any:
    """品牌下拉。"""

    return SelectSelector(
        SelectSelectorConfig(
            options=BRAND_OPTIONS,
            mode=SelectSelectorMode.LIST,
            translation_key="brand",
        )
    )


def _connection_selector(defaults: dict[str, Any]) -> Any:
    """连接方式下拉。"""

    return SelectSelector(
        SelectSelectorConfig(
            options=CONNECTION_OPTIONS,
            mode=SelectSelectorMode.LIST,
            translation_key="connection_type",
        )
    )


def _tuning_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    """运行参数表单。"""

    return {
        vol.Required(
            CONF_POLL_INTERVAL,
            default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_POLL_INTERVAL,
                max=MAX_POLL_INTERVAL,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(
            CONF_DISCOVERY_INTERVAL,
            default=defaults.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL),
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_DISCOVERY_INTERVAL,
                max=MAX_DISCOVERY_INTERVAL,
                step=10,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(
            CONF_ALLOW_HEAT,
            default=defaults.get(CONF_ALLOW_HEAT, DEFAULT_ALLOW_HEAT),
        ): BooleanSelector(),
        vol.Required(
            CONF_CLEANUP_OFFLINE,
            default=defaults.get(CONF_CLEANUP_OFFLINE, DEFAULT_CLEANUP_OFFLINE),
        ): BooleanSelector(),
    }


def _serial_fields(defaults: dict[str, Any], ports: list[str]) -> dict[Any, Any]:
    """串口连接字段。"""

    port_default = defaults.get(CONF_SERIAL_PORT) or (
        ports[0] if ports else "/dev/ttyUSB0"
    )
    return {
        vol.Required(
            CONF_BRAND, default=defaults.get(CONF_BRAND, BRAND_AUTO)
        ): _brand_selector(defaults),
        vol.Required(CONF_SERIAL_PORT, default=port_default): SelectSelector(
            SelectSelectorConfig(
                options=ports or [port_default],
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        ),
        vol.Required(
            CONF_BAUD_RATE,
            default=str(defaults.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)),
        ): SelectSelector(
            SelectSelectorConfig(
                options=[str(rate) for rate in BAUD_RATES],
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                translation_key="baud_rate",
            )
        ),
        vol.Required(
            CONF_SLAVE_ADDR,
            default=defaults.get(CONF_SLAVE_ADDR, DEFAULT_SLAVE_ADDR),
        ): NumberSelector(
            NumberSelectorConfig(min=0, max=255, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_STOPBITS,
            default=str(defaults.get(CONF_STOPBITS, SERIAL_STOPBITS)),
        ): SelectSelector(
            SelectSelectorConfig(
                options=["1", "2"],
                mode=SelectSelectorMode.LIST,
                translation_key="stopbits",
            )
        ),
    }


def _network_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    """TCP / UDP 连接字段。"""

    return {
        vol.Required(
            CONF_BRAND, default=defaults.get(CONF_BRAND, BRAND_AUTO)
        ): _brand_selector(defaults),
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
        vol.Required(
            CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_SLAVE_ADDR,
            default=defaults.get(CONF_SLAVE_ADDR, DEFAULT_SLAVE_ADDR),
        ): NumberSelector(
            NumberSelectorConfig(min=0, max=255, step=1, mode=NumberSelectorMode.BOX)
        ),
    }


def _form_for(
    kind: str, defaults: dict[str, Any], ports: list[str], with_tuning: bool
) -> vol.Schema:
    """按连接方式生成完整表单。"""

    fields: dict[Any, Any] = {}
    if kind == CONNECTION_SERIAL:
        fields.update(_serial_fields(defaults, ports))
    else:
        fields.update(_network_fields(defaults))
    if with_tuning:
        fields.update(_tuning_schema(defaults))
    return vol.Schema(fields)


class VrfConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """处理 VRF 网关的配置流程。"""

    VERSION = 1

    def __init__(self) -> None:
        """初始化流程状态。"""

        self._values: dict[str, Any] = {}

    # -- 新增 -------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """第一步：选择连接方式。"""

        if user_input is not None:
            return await self._async_step_connection(
                user_input[CONF_CONNECTION_TYPE], user_input
            )
        return self.async_show_menu(
            step_id="user", menu_options=list(CONNECTION_OPTIONS)
        )

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> Any:
        """本地串口（USB-RS485 / RS232）。"""

        return await self._async_step_connection(CONNECTION_SERIAL, user_input)

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> Any:
        """局域网 TCP 透传。"""

        return await self._async_step_connection(CONNECTION_TCP, user_input)

    async def async_step_udp(self, user_input: dict[str, Any] | None = None) -> Any:
        """局域网 UDP 透传。"""

        return await self._async_step_connection(CONNECTION_UDP, user_input)

    # -- 重新配置 ---------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """重新配置已添加的网关（⋮ 菜单里的「重新配置」）。"""

        entry = self._get_reconfigure_entry()
        self._values = {**entry.data, **entry.options}
        if user_input is not None:
            return await self._async_step_connection(
                user_input[CONF_CONNECTION_TYPE], user_input
            )
        return self.async_show_menu(
            step_id="reconfigure", menu_options=list(CONNECTION_OPTIONS)
        )

    # -- 内部实现 ---------------------------------------------------------

    async def _async_step_connection(
        self, kind: str, user_input: dict[str, Any] | None
    ) -> Any:
        """按连接方式展示表单并校验。"""

        errors: dict[str, str] = {}
        defaults = dict(self._values)
        if user_input is not None:
            values = _normalize({**defaults, **user_input, CONF_CONNECTION_TYPE: kind})
            error = await self._async_validate(values)
            if not error:
                return await self._async_finish(values)
            errors = error
            defaults = values

        ports = await _async_detect_serial_ports(self.hass)
        schema = _form_for(kind, defaults, ports, with_tuning=False)
        placeholders = {"ports": "、".join(ports) if ports else "未检测到"}
        return self.async_show_form(
            step_id=kind,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_validate(self, values: dict[str, Any]) -> dict[str, str]:
        """校验配置能否连通并识别到内机。"""

        errors: dict[str, str] = {}
        kind = values[CONF_CONNECTION_TYPE]
        if kind == CONNECTION_SERIAL:
            if not values.get(CONF_SERIAL_PORT):
                return {CONF_SERIAL_PORT: "serial_port_required"}
        elif not values.get(CONF_HOST):
            return {CONF_HOST: "host_required"}

        status, _detected, _count = await _async_probe(self.hass, values)
        if status == PROBE_NO_PORT:
            errors["base"] = "cannot_connect"
        return errors

    async def _async_finish(self, values: dict[str, Any]) -> Any:
        """保存配置。"""

        title = _entry_title(values)
        data = _connection_data(values)

        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            self.hass.config_entries.async_update_entry(entry, title=title, data=data)
            return self.async_abort(reason="reconfigure_successful")

        await self.async_set_unique_id(self._identity(values))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)


class VrfOptionsFlow(OptionsFlow):
    """齿轮按钮：连接参数与运行参数都能改，改完自动重载。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """先选连接方式。"""

        if user_input is not None:
            return await self._async_step_connection(
                user_input[CONF_CONNECTION_TYPE], None
            )
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_TYPE,
                        default=current.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL),
                    ): _connection_selector(current)
                }
            ),
        )

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> Any:
        """串口参数。"""

        return await self._async_step_connection(CONNECTION_SERIAL, user_input)

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> Any:
        """TCP 参数。"""

        return await self._async_step_connection(CONNECTION_TCP, user_input)

    async def async_step_udp(self, user_input: dict[str, Any] | None = None) -> Any:
        """UDP 参数。"""

        return await self._async_step_connection(CONNECTION_UDP, user_input)

    async def _async_step_connection(
        self, kind: str, user_input: dict[str, Any] | None
    ) -> Any:
        """展示并保存连接参数 + 运行参数。"""

        entry = self.config_entry
        errors: dict[str, str] = {}
        defaults = {**entry.data, **entry.options}

        if user_input is not None:
            values = _normalize({**defaults, **user_input, CONF_CONNECTION_TYPE: kind})
            error = await _async_validate_values(self.hass, values)
            if not error:
                data = _connection_data(values)
                options = _tuning_options(values)
                # 连接参数写回配置项（触发重载），运行参数写进 options
                self.hass.config_entries.async_update_entry(
                    entry, title=_entry_title(values), data=data
                )
                return self.async_create_entry(title="", data=options)
            errors = error
            defaults = values

        ports = await _async_detect_serial_ports(self.hass)
        schema = _form_for(kind, defaults, ports, with_tuning=True)
        return self.async_show_form(
            step_id=kind,
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "ports": "、".join(ports) if ports else "未检测到"
            },
        )


async def _async_validate_values(
    hass: HomeAssistant, values: dict[str, Any]
) -> dict[str, str]:
    """校验配置能否连通（供选项流程复用）。"""

    errors: dict[str, str] = {}
    kind = values[CONF_CONNECTION_TYPE]
    if kind == CONNECTION_SERIAL:
        if not values.get(CONF_SERIAL_PORT):
            return {CONF_SERIAL_PORT: "serial_port_required"}
    elif not values.get(CONF_HOST):
        return {CONF_HOST: "host_required"}

    status, _detected, _count = await _async_probe(hass, values)
    if status == PROBE_NO_PORT:
        errors["base"] = "cannot_connect"
    return errors
