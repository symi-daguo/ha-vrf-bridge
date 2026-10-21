"""仓库规范自检：manifest / hacs.json / 翻译键一致性。

HACS 与 hassfest 在 CI 里会做更严格的检查，这里做本地快速自查。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "vrf_bridge"


def _load(path: Path) -> dict:
    """读取 JSON 文件。"""

    return json.loads(path.read_text(encoding="utf-8"))


def _keys(value: object, prefix: str = "") -> set[str]:
    """展开 JSON 的所有叶子键路径。"""

    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            result |= _keys(item, f"{prefix}.{key}" if prefix else key)
        return result
    return {prefix}


def test_manifest_has_required_keys() -> None:
    """manifest.json 必须包含 HACS 要求的键。"""

    manifest = _load(COMPONENT / "manifest.json")
    for key in (
        "domain",
        "name",
        "codeowners",
        "documentation",
        "issue_tracker",
        "version",
        "requirements",
        "config_flow",
        "integration_type",
        "iot_class",
    ):
        assert key in manifest, f"manifest 缺少 {key}"
    assert manifest["domain"] == "vrf_bridge"
    assert manifest["config_flow"] is True
    assert manifest["version"]


def test_hacs_json_is_valid() -> None:
    """hacs.json 必须是合法的 HACS 配置。"""

    hacs = _load(ROOT / "hacs.json")
    assert hacs["name"]
    assert hacs["content_in_root"] is False
    assert hacs["render_readme"] is True


def test_translations_match_strings() -> None:
    """各语言翻译的键必须与 strings.json 完全一致。"""

    source = _keys(_load(COMPONENT / "strings.json"))
    for locale in ("en.json", "zh-Hans.json"):
        target = _keys(_load(COMPONENT / "translations" / locale))
        assert source == target, f"{locale} 的键与 strings.json 不一致"


def test_services_yaml_matches_registered_services() -> None:
    """services.yaml 必须与代码注册的服务一一对应。"""

    import yaml

    from custom_components.vrf_bridge.const import (
        SERVICE_REMOVE_UNIT,
        SERVICE_RESCAN,
        SERVICE_RESTORE_UNIT,
    )

    services = yaml.safe_load((COMPONENT / "services.yaml").read_text("utf-8"))
    assert set(services) == {SERVICE_RESCAN, SERVICE_REMOVE_UNIT, SERVICE_RESTORE_UNIT}


@pytest.mark.parametrize("name", ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png"])
def test_brand_assets_have_expected_size(name: str) -> None:
    """品牌资源尺寸必须符合 Home Assistant 规范。"""

    import struct

    data = (COMPONENT / "brand" / name).read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    expected = 512 if "@2x" in name else 256
    assert (width, height) == (expected, expected), (
        f"{name} 尺寸应为 {expected}x{expected}"
    )


def test_single_integration_in_repository() -> None:
    """HACS 要求一个仓库只放一个集成。"""

    domains = [
        path.name
        for path in (ROOT / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    ]
    assert domains == ["vrf_bridge"]


def test_readme_exists() -> None:
    """根目录必须有 README（HACS 会渲染它）。"""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# ")
    assert "hacs" in readme.lower()
