"""配置流程单元测试：表单结构必须能被 Home Assistant 正常构造。"""

from __future__ import annotations

import pytest

from custom_components.vrf_bridge.config_flow import (
    _connection_data,
    _entry_title,
    _form_for,
    _identity,
    _normalize,
    _tuning_options,
)
from custom_components.vrf_bridge.const import (
    BRAND_AUTO,
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
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
)
from custom_components.vrf_bridge.transport import (
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    CONNECTION_UDP,
)


def test_serial_form_builds() -> None:
    """串口表单应能正常构造。"""

    schema = _form_for(CONNECTION_SERIAL, {}, ["/dev/ttyUSB0"], with_tuning=False)
    assert schema({}) is not None


def test_network_form_builds() -> None:
    """网络表单应能正常构造。"""

    for kind in (CONNECTION_TCP, CONNECTION_UDP):
        schema = _form_for(kind, {}, [], with_tuning=False)
        assert schema({}) is not None


def test_options_form_contains_tuning() -> None:
    """选项表单应同时包含连接参数与运行参数。"""

    schema = _form_for(CONNECTION_SERIAL, {}, [], with_tuning=True)
    validated = schema({})
    for key in (
        CONF_BRAND,
        CONF_SERIAL_PORT,
        CONF_BAUD_RATE,
        CONF_SLAVE_ADDR,
        CONF_STOPBITS,
        CONF_POLL_INTERVAL,
        CONF_DISCOVERY_INTERVAL,
        CONF_ALLOW_HEAT,
        CONF_CLEANUP_OFFLINE,
    ):
        assert key in validated, f"选项表单缺少 {key}"


def test_options_form_contains_network_fields() -> None:
    """网络连接也要能改主机与端口。"""

    schema = _form_for(CONNECTION_TCP, {}, [], with_tuning=True)
    validated = schema({})
    assert CONF_HOST in validated
    assert CONF_PORT in validated


def test_normalize_casts_types() -> None:
    """表单值应被规范成正确的类型。"""

    values = _normalize(
        {
            CONF_BAUD_RATE: "19200",
            CONF_STOPBITS: "2",
            CONF_SLAVE_ADDR: "3",
            CONF_POLL_INTERVAL: "5",
            CONF_DISCOVERY_INTERVAL: "60",
        }
    )
    assert values[CONF_BAUD_RATE] == 19200
    assert values[CONF_STOPBITS] == 2.0
    assert values[CONF_SLAVE_ADDR] == 3
    assert values[CONF_POLL_INTERVAL] == 5
    assert values[CONF_DISCOVERY_INTERVAL] == 60


def test_split_data_and_options() -> None:
    """连接参数与运行参数应被正确分开。"""

    values = {
        CONF_BRAND: BRAND_AUTO,
        CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
        CONF_SERIAL_PORT: "/dev/ttyS1",
        CONF_BAUD_RATE: 9600,
        CONF_SLAVE_ADDR: 1,
        CONF_STOPBITS: 1.0,
        CONF_POLL_INTERVAL: 2,
        CONF_DISCOVERY_INTERVAL: 60,
        CONF_ALLOW_HEAT: True,
        CONF_CLEANUP_OFFLINE: False,
    }
    data = _connection_data(values)
    options = _tuning_options(values)
    assert CONF_SERIAL_PORT in data
    assert CONF_BRAND not in options
    assert CONF_POLL_INTERVAL in options
    assert CONF_SERIAL_PORT not in options


def test_identity_differs_per_link() -> None:
    """不同链路不能撞唯一标识。"""

    serial = {
        CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
        CONF_SERIAL_PORT: "/dev/ttyS1",
    }
    tcp = {
        CONF_CONNECTION_TYPE: CONNECTION_TCP,
        CONF_HOST: DEFAULT_HOST,
        CONF_PORT: DEFAULT_PORT,
    }
    assert _identity(serial) != _identity(tcp)
    assert _identity(serial) == f"{DOMAIN}:serial:/dev/ttyS1"


def test_title_contains_brand_and_link() -> None:
    """标题应包含品牌与链路信息。"""

    title = _entry_title(
        {
            CONF_BRAND: BRAND_AUTO,
            CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
            CONF_SERIAL_PORT: "/dev/ttyS1",
        }
    )
    assert "VRF" in title
    assert "/dev/ttyS1" in title


@pytest.mark.parametrize(
    ("kind", "expected_key"),
    [
        (CONNECTION_SERIAL, CONF_SERIAL_PORT),
        (CONNECTION_TCP, CONF_HOST),
        (CONNECTION_UDP, CONF_HOST),
    ],
)
def test_every_connection_type_has_its_fields(kind: str, expected_key: str) -> None:
    """每种连接方式都要有对应字段。"""

    schema = _form_for(kind, {}, [], with_tuning=False)
    assert expected_key in schema({})
