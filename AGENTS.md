# AGENTS.md

本仓库是 Home Assistant 自定义集成「VRF 中央空调网关」，
通过串口（USB-RS485 / RS232）或局域网 TCP / UDP 直连 VRF 中央空调室外机。

## 架构

```
custom_components/vrf_bridge/
├── __init__.py        集成入口：装载配置项、注册服务
├── const.py           常量（配置键、默认值、服务名）
├── config_flow.py     配置向导：连接方式 → 参数 → 探测校验
├── coordinator.py     状态协调：总线快照 → Home Assistant 实体
├── climate.py         内机空调实体
├── bus.py             总线工作线程：串行收发、发现/查询/控制调度
├── transport.py       传输层：SerialTransport / TcpTransport / UdpTransport
├── diagnostics.py     诊断数据
└── protocol/
    ├── base.py        协议抽象：VrfProtocol / VrfUnitKey / VrfUnitState
    ├── zhonghong.py   中弘协议
    ├── demry.py       德姆瑞协议
    └── sochuang.py    硕创协议
```

### 关键设计约束

1. **通信必须离开事件循环**：所有串口/网络收发都在 `bus.py` 的独立线程里完成，
   HA 事件线程只接收快照（`hass.loop.call_soon_threadsafe`）。
2. **控制优先于轮询**：用户操作通过 `queue.Queue` 插队，不等下一轮轮询。
3. **单总线单主站**：一个配置项 = 一条总线 = 一个线程，不并发发报文。
4. **协议无关**：新品牌只需在 `protocol/` 加一个实现并注册到 `protocol/__init__.py`。
5. **不落盘**：集成不写任何本地文件，状态全部在内存，重启后从总线重新发现。
6. **实体唯一**：`unique_id = {配置项ID}_{外机地址}_{内机地址}`，重复扫描只更新状态。
7. **用户可见文案走翻译**：中文在 `translations/zh-Hans.json`，英文在 `translations/en.json`，
   `strings.json` 为源文件；Python 代码里不硬编码界面文案（品牌名除外，属专有名词）。

## 开发

```bash
python -m pytest tests -v        # 单元测试（不需要真实设备）
ruff format . && ruff check .    # 格式化与静态检查
mypy custom_components/vrf_bridge # 类型检查
```

测试用 `tests/support/simulator/` 里的**设备端模拟器**（中弘/德姆瑞/硕创各一套）
配合伪终端做端到端对拍；新增协议时，模拟器就是协议行为的参考实现。

## 提交前检查

- `manifest.json` 的 `version` 与发布版本一致
- `strings.json` 与 `translations/*.json` 的键集合一致
- 新增配置项要同时更新 `const.py`、配置向导、翻译与 README
- 改动协议后必须补对应单元测试（用模拟器做对拍）
