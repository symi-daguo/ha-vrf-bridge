"""VRF 中央空调网关集成的常量定义。"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "vrf_bridge"
VERSION: Final = "1.0.7"

# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------
CONF_BRAND: Final = "brand"
CONF_SERIAL_PORT: Final = "serial_port"
CONF_BAUD_RATE: Final = "baud_rate"
CONF_SLAVE_ADDR: Final = "slave_addr"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_DISCOVERY_INTERVAL: Final = "discovery_interval"
CONF_ALLOW_HEAT: Final = "allow_heat"
CONF_CLEANUP_OFFLINE: Final = "cleanup_offline_units"
CONF_STOPBITS: Final = "stopbits"
CONF_IGNORED_UNITS: Final = "ignored_units"
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

# ---------------------------------------------------------------------------
# 品牌（协议变体）
# ---------------------------------------------------------------------------
BRAND_AUTO: Final = "auto"
BRAND_ZHONGHONG: Final = "zhonghong"
BRAND_DEMRY: Final = "demry"
BRAND_SOCHUANG: Final = "sochuang"

BRAND_LABELS: Final[dict[str, str]] = {
    BRAND_AUTO: "自动探测",
    BRAND_ZHONGHONG: "中弘",
    BRAND_DEMRY: "德姆瑞",
    BRAND_SOCHUANG: "硕创",
}

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------
DEFAULT_BAUD_RATE: Final = 9600
BAUD_RATES: Final[tuple[int, ...]] = (4800, 9600, 19200, 38400, 57600, 115200)
DEFAULT_SLAVE_ADDR: Final = 1
DEFAULT_POLL_INTERVAL: Final = 2
DEFAULT_DISCOVERY_INTERVAL: Final = 60
DEFAULT_ALLOW_HEAT: Final = True
DEFAULT_CLEANUP_OFFLINE: Final = False
DEFAULT_HOST: Final = "192.168.1.10"
DEFAULT_PORT: Final = 502

MIN_POLL_INTERVAL: Final = 2
MAX_POLL_INTERVAL: Final = 300
MIN_DISCOVERY_INTERVAL: Final = 30
MAX_DISCOVERY_INTERVAL: Final = 900

# ---------------------------------------------------------------------------
# 运行参数
# ---------------------------------------------------------------------------
MIN_TEMP: Final = 16
MAX_TEMP: Final = 30
TARGET_TEMP_STEP: Final = 1

# 单轮最多查询的内机数量。VRF 网关连续收帧会锁死（表现为彻底不应答），
# 这里每轮只查 1 台，把总线占用压到最低，与原固件的节奏一致。
MAX_UNITS_PER_CYCLE: Final = 1

# 下发控制指令后的静默窗口（秒）：设备执行期间不做状态回读，
# 避免设备尚未回报的新值被旧值覆盖，造成界面与设备不同步。
CONTROL_SETTLE_SECONDS: Final = 2.5

# 串口与帧超时（秒）。
SERIAL_READ_TIMEOUT: Final = 0.05
SERIAL_WRITE_TIMEOUT: Final = 1.0

# 串口停止位：VRF 网关绝大多数是 8N1（1 个停止位）；2 个停止位会让设备直接不应答。
SERIAL_STOPBITS: Final = 1
# 部分 232/485 转换器用 RTS 作为发送使能，需要常高。
SERIAL_RTS_LEVEL: Final = True
# 两条报文之间的最小间隔（秒）。设备处理一帧需要时间，密集发帧会导致
# 回包被截断、状态错位——原版固件同样保留 100ms 的最小间隔。
BUS_MIN_GAP: Final = 0.15
# 控制指令下发后的总线静默时间（秒），期间不发任何查询。
# 中弘网关执行指令后约 2 秒才会更新状态，过早查询会拿到旧值。
CONTROL_BUS_QUIET: Final = 2.0
# 单台内机查询失败后的重试次数（线路偶发丢字节）。
QUERY_RETRY_TIMES: Final = 1
# 连续失败多少次后进入冷却，让网关自行恢复。
FAILURE_BACKOFF_THRESHOLD: Final = 3
# 冷却时长（秒）。
FAILURE_BACKOFF_SECONDS: Final = 15
FRAME_WAIT_TIMEOUT: Final = 0.6
DISCOVERY_FRAME_TIMEOUT: Final = 0.8
RECONNECT_INTERVAL: Final = 5

# 解析缓冲区上限，防止异常数据导致内存无限增长。
MAX_BUFFER_SIZE: Final = 2048

# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
SERVICE_RESCAN: Final = "rescan"
SERVICE_REMOVE_UNIT: Final = "remove_unit"
SERVICE_RESTORE_UNIT: Final = "restore_unit"
ATTR_ADDRESS: Final = "address"
ATTR_DEVICE_ID: Final = "device_id"

LOGGER_NAME: Final = f"custom_components.{DOMAIN}"
