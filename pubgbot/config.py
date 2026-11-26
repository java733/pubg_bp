"""配置加载。

所有 ROI、HSV 区间、超时、阈值都来自 config/*.json，代码里不写魔法数字。
加载期的所有错误都抛 ConfigError 并带上下文，方便在游戏机上直接定位。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class ConfigError(Exception):
    """配置缺失、格式非法或引用不存在的键。"""


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def intersect(self, other: "Rect") -> "Rect | None":
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class AnchorSpec:
    name: str
    roi: Rect
    template: Path | None
    threshold: float
    # 该锚点归属的视觉状态（Task 7 的投票用）；None 表示只作为辅助分。
    state: str | None = None


@dataclass(frozen=True)
class AnchorsConfig:
    resolution: tuple[int, int]
    rois: dict[str, Rect]
    anchors: dict[str, AnchorSpec]
    hsv: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]]
    values: dict[str, float]
    calibrated: bool

    def roi(self, key: str) -> Rect:
        try:
            return self.rois[key]
        except KeyError:
            raise ConfigError(f"missing roi: {key}") from None

    def hsv_range(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        try:
            lower, upper = self.hsv[key]
        except KeyError:
            raise ConfigError(f"missing hsv range: {key}") from None
        return (
            np.array(lower, dtype=np.uint8),
            np.array(upper, dtype=np.uint8),
        )

    def value(self, key: str) -> float:
        try:
            return self.values[key]
        except KeyError:
            raise ConfigError(f"missing config key: {key}") from None


@dataclass(frozen=True)
class HumanizeConfig:
    """输入人类化参数。区间均为 [min, max]，时间单位毫秒。

    默认值即为启用状态；policy.json 里缺省 humanize 段时按默认值运行。
    """

    enabled: bool = True
    seed: int | None = None
    key_hold_ms: tuple[int, int] = (55, 145)
    reaction_ms: tuple[int, int] = (30, 120)
    inter_action_ms: tuple[int, int] = (20, 90)
    turn_segments: tuple[int, int] = (3, 8)
    turn_segment_pause_ms: tuple[int, int] = (4, 18)
    turn_jitter_ratio: float = 0.03
    click_offset_ratio: float = 0.10


# 默认键位：PUBG 默认 Z=趴下（再按一次起身）。null 表示停用该动作。
DEFAULT_KEYS: dict[str, str | None] = {
    "forward": "w",
    "jump": "f",
    "map": "m",
    "prone": "z",
    "stand": "z",
}


@dataclass(frozen=True)
class PolicyConfig:
    values: dict[str, float]
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    keys: dict[str, str | None] = field(default_factory=lambda: dict(DEFAULT_KEYS))

    def value(self, key: str) -> float:
        try:
            return self.values[key]
        except KeyError:
            raise ConfigError(f"missing config key: {key}") from None

    def int_value(self, key: str) -> int:
        return int(round(self.value(key)))

    def key(self, name: str) -> str | None:
        """键位绑定；None 表示该动作被配置停用。"""
        try:
            return self.keys[name]
        except KeyError:
            raise ConfigError(f"missing key binding: {name}") from None


@dataclass(frozen=True)
class AppConfig:
    anchors: AnchorsConfig
    policy: PolicyConfig


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path} -> 请确认在仓库根目录运行，或改用 --config 指定路径")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 JSON 解析失败: {path}: {exc} -> 请检查逗号/引号/注释") from exc


def _parse_rect(obj: object, where: str) -> Rect:
    if not isinstance(obj, (list, tuple)) or len(obj) != 4:
        raise ConfigError(f"{where}: 期望 [x, y, w, h] 四元组, 实际为 {obj!r}")
    try:
        x, y, w, h = (int(v) for v in obj)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 坐标必须是整数, 实际为 {obj!r}") from None
    return Rect(x, y, w, h)


def _parse_triplet(obj: object, where: str) -> tuple[int, int, int]:
    if not isinstance(obj, (list, tuple)) or len(obj) != 3:
        raise ConfigError(f"{where}: 期望 [a, b, c] 三元组, 实际为 {obj!r}")
    try:
        return tuple(int(v) for v in obj)  # type: ignore[return-value]
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 分量必须是整数, 实际为 {obj!r}") from None


def _parse_int_range(obj: object, where: str, default: tuple[int, int]) -> tuple[int, int]:
    if obj is None:
        return default
    if not isinstance(obj, (list, tuple)) or len(obj) != 2:
        raise ConfigError(f"{where}: 期望 [min, max] 两元素区间, 实际为 {obj!r}")
    try:
        lo, hi = (int(v) for v in obj)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 区间分量必须是整数, 实际为 {obj!r}") from None
    if lo > hi:
        raise ConfigError(f"{where}: 区间下限 {lo} 大于上限 {hi}")
    return lo, hi


def _parse_ratio(obj: object, where: str, default: float) -> float:
    if obj is None:
        return default
    try:
        value = float(obj)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 期望 0~1 的比例, 实际为 {obj!r}") from None
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{where}: 期望 0~1 的比例, 实际为 {value}")
    return value


def _parse_humanize(raw: object, where: str) -> HumanizeConfig:
    if raw is None:
        return HumanizeConfig()
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 必须是对象")
    default = HumanizeConfig()

    enabled = raw.get("enabled", default.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled: 期望 true/false, 实际为 {enabled!r}")

    seed = default.seed
    if raw.get("seed") is not None:
        try:
            seed = int(raw["seed"])
        except (TypeError, ValueError):
            raise ConfigError(f"{where}.seed: 期望整数, 实际为 {raw['seed']!r}") from None

    return HumanizeConfig(
        enabled=enabled,
        seed=seed,
        key_hold_ms=_parse_int_range(raw.get("key_hold_ms"), f"{where}.key_hold_ms", default.key_hold_ms),
        reaction_ms=_parse_int_range(raw.get("reaction_ms"), f"{where}.reaction_ms", default.reaction_ms),
        inter_action_ms=_parse_int_range(
            raw.get("inter_action_ms"), f"{where}.inter_action_ms", default.inter_action_ms
        ),
        turn_segments=_parse_int_range(
            raw.get("turn_segments"), f"{where}.turn_segments", default.turn_segments
        ),
        turn_segment_pause_ms=_parse_int_range(
            raw.get("turn_segment_pause_ms"), f"{where}.turn_segment_pause_ms", default.turn_segment_pause_ms
        ),
        turn_jitter_ratio=_parse_ratio(
            raw.get("turn_jitter_ratio"), f"{where}.turn_jitter_ratio", default.turn_jitter_ratio
        ),
        click_offset_ratio=_parse_ratio(
            raw.get("click_offset_ratio"), f"{where}.click_offset_ratio", default.click_offset_ratio
        ),
    )


def _parse_keys(raw: object, where: str) -> dict[str, str | None]:
    keys: dict[str, str | None] = dict(DEFAULT_KEYS)
    if raw is None:
        return keys
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 必须是对象")
    for name, value in raw.items():
        if name not in DEFAULT_KEYS:
            raise ConfigError(
                f"{where}.{name}: 未知键位名, 可用: {', '.join(sorted(DEFAULT_KEYS))}"
            )
        if value is None:
            keys[name] = None
        elif isinstance(value, str) and value.strip():
            keys[name] = value.strip()
        else:
            raise ConfigError(f"{where}.{name}: 期望按键名字符串或 null（停用）, 实际为 {value!r}")
    return keys


def load_config(anchors_path: Path, policy_path: Path) -> AppConfig:
    raw = _read_json(anchors_path)
    pol = _read_json(policy_path)

    resolution = raw.get("resolution")
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
        raise ConfigError(f"{anchors_path}: resolution 期望 [w, h], 实际为 {resolution!r}")

    rois: dict[str, Rect] = {}
    for name, ref in (raw.get("rois") or {}).items():
        rois[name] = _parse_rect(ref, f"{anchors_path}: rois.{name}")

    anchors: dict[str, AnchorSpec] = {}
    for name, entry in (raw.get("anchors") or {}).items():
        if not isinstance(entry, dict):
            raise ConfigError(f"{anchors_path}: anchors.{name} 必须是对象")
        roi_name = entry.get("roi")
        if roi_name not in rois:
            raise ConfigError(
                f"{anchors_path}: anchors.{name}.roi = {roi_name!r} 未在 rois 中定义 (missing_roi)"
                f" -> 请先在 rois 里添加同名区域"
            )
        template = entry.get("template")
        anchors[name] = AnchorSpec(
            name=name,
            roi=rois[roi_name],
            template=Path(template) if isinstance(template, str) and template else None,
            threshold=float(entry.get("threshold", 0.85)),
            state=entry.get("state"),
        )

    hsv: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
    for name, pair in (raw.get("hsv") or {}).items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ConfigError(f"{anchors_path}: hsv.{name} 期望 [lower, upper]")
        lower = _parse_triplet(pair[0], f"{anchors_path}: hsv.{name}[0]")
        upper = _parse_triplet(pair[1], f"{anchors_path}: hsv.{name}[1]")
        hsv[name] = (lower, upper)

    values: dict[str, float] = {}
    for name, value in (raw.get("values") or {}).items():
        try:
            values[name] = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{anchors_path}: values.{name} 不是数字: {value!r}") from None

    policy_values: dict[str, float] = {}
    for name, value in (pol.get("values") or {}).items():
        try:
            policy_values[name] = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{policy_path}: values.{name} 不是数字: {value!r}") from None

    anchors_cfg = AnchorsConfig(
        resolution=(int(resolution[0]), int(resolution[1])),
        rois=rois,
        anchors=anchors,
        hsv=hsv,
        values=values,
        calibrated=bool(raw.get("calibrated", False)),
    )
    humanize = _parse_humanize(pol.get("humanize"), f"{policy_path}: humanize")
    keys = _parse_keys(pol.get("keys"), f"{policy_path}: keys")
    return AppConfig(
        anchors=anchors_cfg,
        policy=PolicyConfig(values=policy_values, humanize=humanize, keys=keys),
    )
