"""配置流程单元测试：表单结构必须能被 Home Assistant 正常构造。"""

from __future__ import annotations

from custom_components.vrf_bridge.config_flow import (
    _network_schema,
    _serial_schema,
)
from custom_components.vrf_bridge.const import (
    BRAND_AUTO,
    CONF_BAUD_RATE,
    CONF_BRAND,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ADDR,
    DEFAULT_BAUD_RATE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SLAVE_ADDR,
)


def test_serial_schema_builds() -> None:
    """串口表单应能正常构造（选项必须是字符串）。"""

    schema = _serial_schema({}, [])
    assert schema({}) is not None


def test_serial_schema_with_detected_ports() -> None:
    """检测到串口时也应能正常构造。"""

    schema = _serial_schema({}, ["/dev/ttyUSB0", "/dev/ttyS0"])
    assert schema({}) is not None


def test_network_schema_builds() -> None:
    """网络表单应能正常构造。"""

    schema = _network_schema({})
    assert schema({}) is not None


def test_defaults_are_used() -> None:
    """默认值应覆盖常用参数。"""

    schema = _serial_schema({}, [])
    validated = schema({})
    assert validated[CONF_BRAND] == BRAND_AUTO
    assert int(validated[CONF_BAUD_RATE]) == DEFAULT_BAUD_RATE
    assert validated[CONF_SLAVE_ADDR] == DEFAULT_SLAVE_ADDR
    assert validated[CONF_SERIAL_PORT]


def test_network_defaults_are_used() -> None:
    """网络表单默认值。"""

    schema = _network_schema({})
    validated = schema({})
    assert validated[CONF_HOST] == DEFAULT_HOST
    assert validated[CONF_PORT] == DEFAULT_PORT
