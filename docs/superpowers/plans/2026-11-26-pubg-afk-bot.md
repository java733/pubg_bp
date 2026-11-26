# PUBG PC 挂机脚本 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 PUBG PC（Steam，1920×1080 窗口化）上实现纯外部视觉的无人值守挂机：自动排队、跳伞、落地趴住、按地图标点与罗盘黄标跟圈。

**Architecture:** 五层单向数据流 `capture → vision → fsm → action`，`safety` 横切。vision 层是纯函数（帧 + 配置 → 观测），不持有状态，可用合成帧做单元测试；fsm 持有状态与防抖，只产出语义命令；action 把命令翻译成键鼠原语并保证成对释放。M0/M1 先只识别不输入，actions 以空实现注入。

**Tech Stack:** Python 3.12.6 (64-bit)、numpy、opencv-python、dxcam、mss、pywin32、keyboard、pydirectinput、pytest

**Spec:** [docs/superpowers/specs/2026-11-26-pubg-afk-bot-design.md](../specs/2026-11-26-pubg-afk-bot-design.md)

## Global Constraints

- 仅支持客户区 1920×1080；实际尺寸与配置不符时**拒绝启动**并给出提示，不做缩放适配。
- 游戏进程 `TslGame.exe`，窗口类 `UnrealWindow`；按进程名/类名查找，不按窗口标题。
- 启动即调用 `SetProcessDpiAwareness(2)`；脚本需管理员权限，启动自检 `is_admin()` 并提示。
- 帧率 10–15 FPS（`frame_interval_ms` 默认 80），单帧处理预算 ≤ 60 ms。
- 所有 ROI、HSV 区间、像素系数、超时全部来自 `config/`，代码中不出现魔法数字。
- 视觉帧统一为 BGR `uint8` ndarray；捕获层负责把 dxcam 的 RGB、mss 的 BGRA 转成 BGR。
- 不读游戏内存、不注入、不修改游戏文件、不涉及网络协议。
- `UNKNOWN` 状态期间不发送任何输入。
- 任何按键必须成对释放：异常、F12、Ctrl+C、看门狗路径都要先 `release_all()`。
- 测试使用**缩小分辨率的合成帧与合成配置**（如 64×48），不依赖真实游戏画面；1920×1080 的限制在启动与帧校验处执行。

## 开发环境限制与日志规范

**开发机（当前机器）没有安装游戏**，因此：

- 单元测试一律用合成帧（缩小分辨率）与合成配置，**任何模块在导入期不得依赖游戏窗口存在**。
- 所有真实截图、ROI 标定、M0–M4 人工闸门都在用户的游戏机上执行；本机只验证"代码能跑通、逻辑正确、报错可读"。
- 交付时提供 `tools/doctor.py` 环境自检：依赖导入、管理员权限、DPI 缩放、显示器列表、dxcam/mss 可用性、窗口查找结果、试截一帧，逐项 PASS/FAIL + 处理建议。
- 配置初始 `calibrated: false` 时，识别器每帧只告警一次醒目 WARN，提示需要先跑 `tools/pick_roi.py`。

**日志规范（本项目的调试生命线）**

- 每帧一条 JSONL，字段：`frame`、`ts_ms`、`state`、`confidence`、`scores`（各锚点分数）、`world`（`marker_x`/`dist_ratio`/`mark_point`）、`command`、`reason`、`elapsed_ms`。
- 关键节点 INFO：窗口发现、客户区矩形、DPI、捕获后端与首帧尺寸、状态切换（`from → to` 及触发锚点与分数）、命令执行（`kind`/`arg`）、模式切换（`marking`/`running`/`prone`）、安全事件（F12、看门狗、会话上限、前台丢失、权限不足）。
- 异常统一格式 `[ERR] <模块>: <原因> -> <建议动作>`，不允许裸 traceback 直接终止；例如找不到窗口时，列出当前可见窗口的标题与类名供排查。
- 所有 `reason` 字段写人话，例如 `"outside_zone dist_ratio=1.42"`、`"marker_lost 2.1s"`、`"foreground_lost"`。

## Review Focus

1. **截屏返回黑帧、尺寸不符或窗口被最小化**：期望判定为无效帧并跳过，绝不把黑帧/错尺寸帧送进识别与决策 —— 测试在 Task 3。
2. **游戏不在前台（用户切到浏览器/锁屏）**：期望不发任何输入并记日志 —— 测试在 Task 9、Task 10。
3. **按键持有时中断**（F12、Ctrl+C、未捕获异常、看门狗超时）：期望 W 键一定被释放 —— 测试在 Task 9、Task 10。
4. **客户区尺寸/DPI 与预期不符**：期望拒绝启动或拒绝该帧，而不是错位匹配后乱按键 —— 测试在 Task 2、Task 11。
5. **真实状态与 UNKNOWN 来回抖动**：期望不重复触发一次性动作（反复按 F、反复点开始）—— 测试在 Task 7、Task 8。

---

### Task 1: 工程骨架与配置加载

**Files:**
- Create: `pyproject.toml`、`.gitignore`、`pubgbot/__init__.py`、`pubgbot/config.py`
- Create: `config/anchors.1920x1080.json`、`config/policy.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) Rect(x:int, y:int, w:int, h:int)`，属性 `right -> int`、`bottom -> int`，方法 `intersect(other: Rect) -> Rect | None`
  - `@dataclass(frozen=True) AnchorSpec(name:str, roi:Rect, template:Path|None, threshold:float)`
  - `@dataclass(frozen=True) AnchorsConfig(resolution:tuple[int,int], rois:dict[str,Rect], anchors:dict[str,AnchorSpec], hsv:dict[str,tuple[tuple[int,int,int],tuple[int,int,int]]], values:dict[str,float], calibrated:bool)`，方法 `roi(key:str)->Rect`、`hsv_range(key:str)->tuple[np.ndarray,np.ndarray]`、`value(key:str)->float`
  - `@dataclass(frozen=True) PolicyConfig(values:dict[str,float])`，方法 `value(key:str)->float`、`int_value(key:str)->int`
  - `@dataclass(frozen=True) AppConfig(anchors:AnchorsConfig, policy:PolicyConfig)`
  - `load_config(anchors_path: Path, policy_path: Path) -> AppConfig`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
import json
from pathlib import Path
import pytest
from pubgbot.config import ConfigError, Rect, load_config

def write(tmp_path, anchors=None, policy=None):
    a = {"version": 1, "resolution": [64, 48], "calibrated": False,
         "rois": {"compass": [8, 0, 48, 6], "minimap": [44, 32, 16, 16]},
         "anchors": {"lobby_start": {"roi": "compass", "template": None, "threshold": 0.85}},
         "hsv": {"waypoint_yellow": [[18, 90, 120], [38, 255, 255]]},
         "values": {"dead_zone_px": 6, "circle_inner_factor": 0.9}}
    p = {"values": {"frame_interval_ms": 80, "vote_frames": 3, "unused_float": 0.5}}
    (tmp_path / "a.json").write_text(json.dumps(anchors or a), encoding="utf-8")
    (tmp_path / "p.json").write_text(json.dumps(policy or p), encoding="utf-8")
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

def test_loads_rect_and_values(tmp_path):
    cfg = write(tmp_path)
    assert cfg.anchors.resolution == (64, 48)
    assert cfg.anchors.roi("compass") == Rect(8, 0, 48, 6)
    assert cfg.anchors.value("dead_zone_px") == 6
    assert cfg.anchors.anchors["lobby_start"].threshold == 0.85
    assert cfg.policy.int_value("vote_frames") == 3

def test_hsv_range_returns_lower_upper(tmp_path):
    lower, upper = write(tmp_path).anchors.hsv_range("waypoint_yellow")
    assert lower.tolist() == [18, 90, 120]
    assert upper.tolist() == [38, 255, 255]

def test_missing_value_key_raises(tmp_path):
    cfg = write(tmp_path)
    with pytest.raises(ConfigError, match="no_such_key"):
        cfg.anchors.value("no_such_key")

def test_rect_intersect_clips(tmp_path):
    assert Rect(-10, -10, 30, 30).intersect(Rect(0, 0, 64, 48)) == Rect(0, 0, 20, 20)
    assert Rect(100, 100, 10, 10).intersect(Rect(0, 0, 64, 48)) is None

def test_unknown_anchor_roi_reference_raises(tmp_path):
    bad = {"version": 1, "resolution": [64, 48], "calibrated": False, "rois": {},
           "anchors": {"x": {"roi": "missing_roi", "template": None, "threshold": 0.85}},
           "hsv": {}, "values": {}}
    with pytest.raises(ConfigError, match="missing_roi"):
        write(tmp_path, anchors=bad)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pubgbot'`

- [ ] **Step 3: 建骨架并实现 `pubgbot/config.py`**

`pyproject.toml`：`requires-python = ">=3.12"`，`dependencies = ["numpy", "opencv-python", "dxcam", "mss", "pywin32", "keyboard", "pydirectinput"]`，`[project.optional-dependencies] dev = ["pytest"]`，构建后端 setuptools，包发现 `pubgbot*`。
`.gitignore`：`.venv/`、`__pycache__/`、`logs/`、`*.png` 排除 `templates/` 之外的临时帧。
配置加载要点：`Rect` 用 `x,y,w,h` 四元组解析；`anchors[*].roi` 必须能在 `rois` 中找到，否则 `ConfigError`；`hsv` 转 `np.array(dtype=np.uint8)`；缺 key 时 `value()` 抛 `ConfigError(f"missing config key: {key}")`；`int_value()` 走 `int(round(...))`。
`config/anchors.1920x1080.json` 与 `config/policy.json` 先按 spec 5.4.1 的 key 清单写入**占位 ROI**，并置 `"calibrated": false`（ROI 真值在 Task 12 用 `pick_roi` 标定覆盖）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml .gitignore pubgbot/__init__.py pubgbot/config.py config tests/test_config.py
git commit -m "feat: project skeleton and config loading"
```

---

### Task 2: 窗口定位与客户区坐标

**Files:**
- Create: `pubgbot/win.py`
- Test: `tests/test_win.py`

**Interfaces:**
- Consumes: `Rect`（Task 1）
- Produces:
  - `enable_dpi_awareness() -> None`
  - `find_game_window(process_name: str = "TslGame.exe", class_name: str = "UnrealWindow") -> int`（找不到抛 `WindowNotFound`）
  - `class WindowNotFound(Exception)`
  - `get_client_rect(hwnd: int) -> Rect`（屏幕绝对坐标）
  - `compute_capture_region(client: Rect, monitor: Rect) -> Rect`（求交，纯函数）
  - `is_foreground(hwnd: int) -> bool`
  - `is_admin() -> bool`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_win.py
from pubgbot.config import Rect
from pubgbot.win import compute_capture_region

def test_fully_inside_monitor_unchanged():
    assert compute_capture_region(Rect(10, 10, 100, 80), Rect(0, 0, 1920, 1080)) == Rect(10, 10, 100, 80)

def test_partially_offscreen_clipped():
    assert compute_capture_region(Rect(-50, 20, 200, 100), Rect(0, 0, 1920, 1080)) == Rect(0, 20, 150, 100)

def test_entirely_offscreen_raises():
    import pytest
    from pubgbot.win import CaptureRegionError
    with pytest.raises(CaptureRegionError):
        compute_capture_region(Rect(2000, 0, 100, 100), Rect(0, 0, 1920, 1080))
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_win.py -v`
Expected: FAIL — 无法导入 `pubgbot.win`

- [ ] **Step 3: 实现 `pubgbot/win.py`**

`compute_capture_region` 直接复用 `Rect.intersect`，交集为空时抛 `CaptureRegionError`。
`find_game_window` 用 `win32gui.EnumWindows` + `win32process.GetWindowThreadProcessId` 取进程名比对，同时校验 `win32gui.GetClassName(hwnd) == class_name`；跳过不可见窗口（`IsWindowVisible`）。
`get_client_rect` = `GetClientRect(hwnd)` 转 `ClientToScreen(hwnd, (0,0))` 得左上角。
`is_foreground` = `GetForegroundWindow() == hwnd`（若为 0 或不同则 False）。
`is_admin` 用 `ctypes.windll.shell32.IsUserAnAdmin()`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_win.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/win.py tests/test_win.py
git commit -m "feat: window discovery and client-area rect"
```

---

### Task 3: 截屏帧源与 M0 可行性验证

**Files:**
- Create: `pubgbot/capture.py`、`tools/probe_capture.py`、`tools/doctor.py`
- Test: `tests/test_capture.py`、`tests/test_doctor.py`

**Interfaces:**
- Consumes: `Rect`、`AppConfig`（Task 1）、`win`（Task 2）
- Produces:
  - `class FrameInvalid(Exception)`
  - `def to_bgr(frame: np.ndarray, source: str) -> np.ndarray`（`source` 取 `"dxcam"`/`"mss"`）
  - `class FrameGate: def __init__(self, expect: tuple[int,int], min_mean: float = 8.0); def validate(self, frame: np.ndarray) -> None`（不合格抛 `FrameInvalid`）
  - `class FrameSource(Protocol): def grab(self) -> np.ndarray`
  - `class DxcamSource(region_provider: Callable[[], Rect])`
  - `class MssSource(region_provider: Callable[[], Rect])`
  - `def open_frame_source(region_provider: Callable[[], Rect], prefer: str = "dxcam") -> FrameSource`（dxcam 初始化失败回退 mss）
  - `def probe(out_dir: Path) -> list[Path]`（两种后端各存一张 png）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_capture.py
import numpy as np
import pytest
from pubgbot.capture import FrameGate, FrameInvalid, to_bgr

def test_black_frame_rejected():
    gate = FrameGate((64, 48))
    with pytest.raises(FrameInvalid, match="black"):
        gate.validate(np.zeros((48, 64, 3), np.uint8))

def test_wrong_size_rejected():
    gate = FrameGate((64, 48))
    with pytest.raises(FrameInvalid, match="shape"):
        gate.validate(np.full((10, 10, 3), 200, np.uint8))

def test_valid_frame_passes():
    FrameGate((64, 48)).validate(np.full((48, 64, 3), 120, np.uint8))

def test_rgb_and_bgra_converted_to_bgr():
    rgb = np.zeros((2, 2, 3), np.uint8); rgb[..., 0] = 255        # R=255
    assert to_bgr(rgb, "dxcam")[0, 0].tolist() == [0, 0, 255]
    bgra = np.zeros((2, 2, 4), np.uint8); bgra[..., 0] = 255       # B=255
    assert to_bgr(bgra, "mss")[0, 0].tolist() == [255, 0, 0]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_capture.py -v`
Expected: FAIL — 无法导入 `pubgbot.capture`

- [ ] **Step 3: 实现 `pubgbot/capture.py` 与 `tools/probe_capture.py`**

`FrameGate.validate` 先校验 `frame.shape[:2] == (h, w)`，再校验 `frame.mean() < min_mean` 判黑帧，异常消息分别含 `shape` / `black`。
`DxcamSource.grab` 用 `dxcam.create(output_idx=0)` + `camera.grab(region=(l, t, r, b))`，返回 None 时（`grab` 未取到新帧）沿用上一帧；统一 `to_bgr(..., "dxcam")`。`MssSource` 用 `mss.mss().grab(region)` 取 `np.asarray`，`to_bgr(..., "mss")`。
`probe` 用 `win.find_game_window` + `get_client_rect` + `compute_capture_region`，两种后端各截一张存到 `out_dir/probe_dxcam.png`、`probe_mss.png`，返回路径列表。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_capture.py -v`
Expected: 4 passed

- [ ] **Step 5: 环境自检工具（无游戏也能跑）**

`tools/doctor.py` 输出逐项检查：Python 版本与位数、管理员权限、DPI awareness、显示器枚举、`dxcam`/`mss`/`pynput` 等依赖导入、配置文件加载、游戏窗口查找、试截一帧并报告尺寸与平均亮度。每项打印 `PASS/FAIL + 建议动作`；找不到游戏窗口时列出当前可见窗口的标题与类名。
新增纯函数 `render_report(checks: list[dict]) -> str` 并写一条单测（给定 checks 断言输出包含 `FAIL` 与建议文本）。

Run: `python tools/doctor.py`
Expected（开发机无游戏）：依赖与配置项 PASS，窗口查找 FAIL 且提示"请确认 PUBG 已启动"，进程不崩溃、退出码 0。

- [ ] **Step 6: M0 人工验证（里程碑闸门，在游戏机执行）**

先在管理员 PowerShell 里 `python tools/probe_capture.py`，然后打开两张 png 用肉眼确认：画面是游戏大厅而不是纯黑、不是锁屏、客户区内容完整、无桌面边框混入。
Expected: dxcam 图非黑即通过；若 dxcam 黑而 mss 正常，把 `prefer` 默认值改为 `"mss"` 并在日志里注明；两者皆黑则**停止后续任务**，回到设计文档第 7 节风险表处置。

- [ ] **Step 6: 提交**

```bash
git add pubgbot/capture.py tools/probe_capture.py tools/doctor.py tests/test_capture.py tests/test_doctor.py
git commit -m "feat: frame source, doctor and M0 probe"
```

---

### Task 4: 模板匹配锚点

**Files:**
- Create: `pubgbot/vision/__init__.py`、`pubgbot/vision/template.py`
- Test: `tests/test_template.py`

**Interfaces:**
- Consumes: `Rect`、`AnchorSpec`（Task 1）
- Produces:
  - `@dataclass(frozen=True) Hit(score: float, hit: bool, loc: tuple[int,int])`
  - `def match(frame: np.ndarray, roi: Rect, template: np.ndarray, threshold: float = 0.85) -> Hit`
  - `def match_anchor(frame: np.ndarray, spec: AnchorSpec, templates_dir: Path) -> Hit`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_template.py
import numpy as np
from pubgbot.config import AnchorSpec, Rect
from pubgbot.vision.template import match, match_anchor
from pathlib import Path
import cv2

def make_frame():
    f = np.full((48, 64, 3), 30, np.uint8)
    f[10:20, 20:32] = 200            # 12x10 亮块
    return f

def test_match_finds_template():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    hit = match(make_frame(), Rect(0, 0, 64, 48), tmpl, 0.85)
    assert hit.hit is True
    assert abs(hit.loc[0] - 20) <= 1 and abs(hit.loc[1] - 10) <= 1

def test_match_misses_when_absent():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    hit = match(make_frame(), Rect(40, 30, 24, 18), tmpl, 0.85)
    assert hit.hit is False
    assert hit.score < 0.85

def test_match_anchor_reads_template_file(tmp_path):
    cv2.imwrite(str(tmp_path / "t.png"), np.full((10, 12, 3), 200, np.uint8))
    spec = AnchorSpec("t", Rect(0, 0, 64, 48), Path("t.png"), 0.85)
    assert match_anchor(make_frame(), spec, tmp_path).hit is True

def test_roi_out_of_frame_returns_miss():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    assert match(make_frame(), Rect(60, 44, 20, 10), tmpl, 0.85).hit is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_template.py -v`
Expected: FAIL — 无法导入 `pubgbot.vision.template`

- [ ] **Step 3: 实现**

`match` 先按 roi 切片（越界则把 roi 与帧求交，交集空或小于模板尺寸直接返回 `Hit(0.0, False, (0,0))`），转灰度后 `cv2.matchTemplate(sub, tmpl_gray, cv2.TM_CCOEFF_NORMED)`，取 `minMaxLoc` 的最大值；`hit = score >= threshold`；`loc` 为子图内坐标加 roi 左上偏移。
`match_anchor` 读 `templates_dir / spec.template`，`cv2.imread` 失败抛 `FileNotFoundError`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_template.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/vision/__init__.py pubgbot/vision/template.py tests/test_template.py
git commit -m "feat: template matching anchors"
```

---

### Task 5: 颜色断言与罗盘黄标

**Files:**
- Create: `pubgbot/vision/color.py`、`pubgbot/vision/compass.py`
- Test: `tests/test_color.py`、`tests/test_compass.py`

**Interfaces:**
- Consumes: `Rect`、`AnchorsConfig`（Task 1）
- Produces:
  - `def color_ratio(frame: np.ndarray, roi: Rect, lower: np.ndarray, upper: np.ndarray) -> float`
  - `def find_marker_x(frame: np.ndarray, roi: Rect, lower: np.ndarray, upper: np.ndarray, min_pixels: int = 12) -> int | None`
  - `def steer_pixels(marker_x: int | None, center_x: int, dead_zone: int = 6, max_step: int = 8) -> int`
    （返回正数=向右转多少像素，负数=向左，0=已对准；`marker_x is None` 返回 0，由 FSM 决定扫视）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_color.py
import numpy as np
from pubgbot.config import Rect
from pubgbot.vision.color import color_ratio

LO = np.array([18, 90, 120], np.uint8); HI = np.array([38, 255, 255], np.uint8)

def test_ratio_of_half_yellow():
    f = np.zeros((20, 20, 3), np.uint8)
    f[:, :10] = (0, 255, 255)                     # BGR 黄
    assert color_ratio(f, Rect(0, 0, 20, 20), LO, HI) == 0.5

def test_ratio_zero_when_none():
    assert color_ratio(np.zeros((20, 20, 3), np.uint8), Rect(0, 0, 20, 20), LO, HI) == 0.0
```

```python
# tests/test_compass.py
import numpy as np
from pubgbot.config import Rect
from pubgbot.vision.compass import find_marker_x, steer_pixels

LO = np.array([18, 90, 120], np.uint8); HI = np.array([38, 255, 255], np.uint8)

def frame_with_marker(x0, w=4):
    f = np.zeros((10, 100, 3), np.uint8)
    f[2:8, x0:x0 + w] = (0, 255, 255)
    return f

def test_finds_marker_center():
    assert find_marker_x(frame_with_marker(70), Rect(0, 0, 100, 10), LO, HI) == 71

def test_no_marker_returns_none():
    assert find_marker_x(np.zeros((10, 100, 3), np.uint8), Rect(0, 0, 100, 10), LO, HI) is None

def test_steer_right_when_marker_right():
    assert steer_pixels(71, 50, 6, 8) == 8

def test_steer_left_clamped():
    assert steer_pixels(20, 50, 6, 8) == -8

def test_dead_zone_returns_zero():
    assert steer_pixels(53, 50, 6, 8) == 0

def test_missing_marker_returns_zero():
    assert steer_pixels(None, 50, 6, 8) == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_color.py tests/test_compass.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`color_ratio`：roi 与帧求交后转 HSV，`cv2.inRange`，返回 `countNonZero / 面积`（面积 0 返回 0.0）。
`find_marker_x`：mask 后按列求和，取像素总数 ≥ `min_pixels` 的列，用加权质心（列号 × 像素数 / 总数）取整；不足阈值返回 `None`。
`steer_pixels`：`dx = marker_x - center_x`；`abs(dx) <= dead_zone` 返回 0；否则 `sign(dx) * min(abs(dx), max_step)`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_color.py tests/test_compass.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/vision/color.py pubgbot/vision/compass.py tests/test_color.py tests/test_compass.py
git commit -m "feat: color ratio and compass waypoint steering"
```

---

### Task 6: 白圈检测与标点选点

**Files:**
- Create: `pubgbot/vision/circle.py`
- Test: `tests/test_circle.py`

**Interfaces:**
- Consumes: `Rect`（Task 1）
- Produces:
  - `@dataclass(frozen=True) Circle(cx: float, cy: float, r: float)`
  - `def detect_circle(frame: np.ndarray, roi: Rect, lower: np.ndarray, upper: np.ndarray, r_min: int, r_max: int) -> Circle | None`
  - `def nearest_edge_point(player: tuple[float,float], circle: Circle, factor: float = 0.9) -> tuple[int,int]`
  - `def clamp_to(rect: Rect, point: tuple[int,int]) -> tuple[int,int]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_circle.py
import cv2, numpy as np
from pubgbot.config import Rect
from pubgbot.vision.circle import Circle, clamp_to, detect_circle, nearest_edge_point

LO = np.array([0, 0, 200], np.uint8); HI = np.array([180, 40, 255], np.uint8)

def ring_frame():
    f = np.zeros((200, 200, 3), np.uint8)
    cv2.circle(f, (100, 100), 60, (255, 255, 255), 3)
    return f

def test_detects_ring_center_and_radius():
    c = detect_circle(ring_frame(), Rect(0, 0, 200, 200), LO, HI, 20, 120)
    assert c is not None
    assert abs(c.cx - 100) <= 3 and abs(c.cy - 100) <= 3
    assert abs(c.r - 60) <= 4

def test_radius_out_of_bounds_returns_none():
    assert detect_circle(ring_frame(), Rect(0, 0, 200, 200), LO, HI, 80, 120) is None

def test_nearest_edge_point_is_inside_towards_player():
    p = nearest_edge_point((20.0, 100.0), Circle(100, 100, 60), 0.9)
    assert p == (46, 100)

def test_clamp_to_rect():
    assert clamp_to(Rect(0, 0, 200, 200), (-5, 250)) == (0, 199)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_circle.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`detect_circle`：roi 切片 → HSV → `inRange` → 形态学开运算（3×3）去噪 → `cv2.HoughCircles(HOUGH_GRADIENT, dp=1.2, minDist=roi.w, param1=100, param2=30, minRadius=r_min, maxRadius=r_max)`；无结果时用 mask 外接轮廓 `minEnclosingCircle` 兜底；半径不在 `[r_min, r_max]` 一律返回 `None`，坐标加上 roi 偏移。
`nearest_edge_point`：`d = player - center`，单位化后 `center + d * r * factor`，四舍五入取整。
`clamp_to`：两个分量分别夹到 `[x, x+w-1]` / `[y, y+h-1]`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_circle.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/vision/circle.py tests/test_circle.py
git commit -m "feat: white circle detection and nearest-edge mark point"
```

---

### Task 7: 状态识别器（投票 + 驻留防抖）

**Files:**
- Create: `pubgbot/vision/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `AppConfig`、`AnchorSpec`（Task 1）、`match_anchor`（Task 4）
- Produces:
  - `class VisualState(StrEnum)`：`LOBBY/QUEUE/SPAWN/PLANE/FREEFALL/GROUNDED/MARKING/DOWNED/RESULT/UNKNOWN`
  - `@dataclass(frozen=True) Observation(state: VisualState, scores: dict[str,float], confidence: float)`
  - `class StateRecognizer: def __init__(self, cfg: AppConfig, templates_dir: Path); def observe(self, frame: np.ndarray, now_ms: int) -> Observation`
    （`vote_frames`、`min_dwell_ms`、`template_threshold` 从 `cfg.policy`/`cfg.anchors` 取）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_state.py
import json
from pathlib import Path
import numpy as np
from pubgbot.config import load_config
from pubgbot.vision.state import StateRecognizer, VisualState

def build(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True,
         "rois": {"full": [0, 0, 64, 48]},
         "anchors": {"lobby_start": {"roi": "full", "template": "lobby.png", "threshold": 0.85},
                     "plane_prompt": {"roi": "full", "template": "plane.png", "threshold": 0.85},
                     "result_text": {"roi": "full", "template": "result.png", "threshold": 0.85}},
         "hsv": {}, "values": {}}
    p = {"values": {"vote_frames": 3, "min_dwell_ms": 500, "template_threshold": 0.85}}
    (tmp_path / "t").mkdir()
    for name, val in (("lobby", 60), ("plane", 130), ("result", 200)):
        import cv2; cv2.imwrite(str(tmp_path / "t" / f"{name}.png"), np.full((20, 20, 3), val, np.uint8))
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json"), tmp_path / "t"

def frame(val):
    f = np.full((48, 64, 3), 10, np.uint8); f[10:30, 10:30] = val; return f

def labels(cfg, tpl):
    return StateRecognizer(cfg, tpl)

def test_switches_after_three_consistent_frames(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    obs = [r.observe(frame(60), now) for now in (0, 100, 200, 300)]
    assert obs[0].state is VisualState.UNKNOWN
    assert obs[-1].state is VisualState.LOBBY

def test_single_outlier_frame_does_not_flip(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    for now in (0, 100, 200): r.observe(frame(60), now)
    assert r.observe(frame(130), 300).state is VisualState.LOBBY

def test_dwell_blocks_fast_flip(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    for now in (0, 100, 200): r.observe(frame(60), now)
    for now in (300, 400, 500): r.observe(frame(130), now)
    assert r.observe(frame(130), 600).state is VisualState.LOBBY   # 驻留未满 500ms 自切换点起算
    for now in (700, 800, 900, 1000): r.observe(frame(130), now)
    assert r.observe(frame(130), 1100).state is VisualState.PLANE

def test_nothing_matches_is_unknown(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    obs = [r.observe(np.zeros((48, 64, 3), np.uint8), now) for now in (0, 100, 200)]
    assert all(o.state is VisualState.UNKNOWN for o in obs)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_state.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

每个 `AnchorSpec` 通过可选字段 `state`（配置里加 `"state": "LOBBY"`）绑定到视觉状态；未绑定 `state` 的锚点只当辅助分。每帧对每个锚点算 `Hit`，按状态聚合：命中数 ≥ 2（该状态锚点数 ≥ 2 时）或该状态唯一锚点命中即视为候选，`confidence` 取该状态命中锚点的最高分。
投票：维护最近 `vote_frames` 个候选的 deque，全部相同才认为稳定候选；稳定候选与当前状态不同时开始计 `pending_since_ms`，持续 `min_dwell_ms` 才真正切换（首次从 `UNKNOWN` 切换仍要求 `min_dwell_ms` 满足，保证单帧不抖）。`UNKNOWN` 作为候选仅在无任何锚点命中时产生。
`Observation.scores` 记录所有锚点分数，供日志与调试用。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_state.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/vision/state.py tests/test_state.py
git commit -m "feat: state recognizer with voting and dwell debounce"
```

---

### Task 8: 状态机与决策

**Files:**
- Create: `pubgbot/fsm.py`
- Test: `tests/test_fsm.py`

**Interfaces:**
- Consumes: `Observation`、`VisualState`（Task 7）、`AppConfig`（Task 1）
- Produces:
  - `@dataclass(frozen=True) WorldView(marker_x:int|None, compass_center_x:int, dist_ratio:float|None, health_drop_fast:bool, mark_point: tuple[int,int]|None = None)`
  - `@dataclass(frozen=True) Command(kind: str, arg: int | tuple[int,int] | None = None)`，`kind` 取值：`click_start / jump / press_w / release_w / turn / prone / open_map / mark / close_map / scan / idle`
  - `class BotFsm: def __init__(self, cfg: AppConfig); def tick(self, obs: Observation, world: WorldView, now_ms: int) -> Command`
  - `@property mode -> str`（`idle|marking|running|prone`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fsm.py
import json
import pytest
from pathlib import Path
from pubgbot.config import load_config
from pubgbot.fsm import BotFsm, Command, WorldView
from pubgbot.vision.state import Observation, VisualState

@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

def obs(state, now, **kw):
    return Observation(state, {}, 1.0)

W = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)

def test_unknown_emits_idle(cfg):
    assert BotFsm(cfg).tick(obs(VisualState.UNKNOWN, 0), W, 0).kind == "idle"

def test_lobby_clicks_start_with_cooldown(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.LOBBY, 0), W, 0).kind == "click_start"
    assert f.tick(obs(VisualState.LOBBY, 0), W, 1000).kind == "idle"
    assert f.tick(obs(VisualState.LOBBY, 0), W, 5000).kind == "click_start"

def test_plane_jumps_after_delay(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.PLANE, 0), W, 0)
    assert f.tick(obs(VisualState.PLANE, 0), W, 19999).kind == "idle"
    assert f.tick(obs(VisualState.PLANE, 0), W, 20000).kind == "jump"

def test_grounded_outside_zone_starts_marking(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 0).kind == "open_map"
    assert f.mode == "marking"

def test_grounded_inside_zone_prones(cfg):
    w = WorldView(marker_x=None, compass_center_x=50, dist_ratio=0.5, health_drop_fast=False)
    assert BotFsm(cfg).tick(obs(VisualState.GROUNDED, 0), w, 0).kind == "prone"

def test_marking_marks_then_closes(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    marked = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5,
                       health_drop_fast=False, mark_point=(120, 340))
    cmd = f.tick(obs(VisualState.MARKING, 0), marked, 100)
    assert cmd.kind == "mark" and cmd.arg == (120, 340)
    assert f.tick(obs(VisualState.MARKING, 0), marked, 200).kind == "close_map"

def test_running_steers_toward_marker(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    f.tick(obs(VisualState.MARKING, 0), W, 100)
    f.tick(obs(VisualState.MARKING, 0), W, 200)
    f.tick(obs(VisualState.GROUNDED, 0), W, 300)          # 关图后回地面
    left = WorldView(marker_x=20, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 400).kind == "turn"
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 500).kind == "turn"
    centered = WorldView(marker_x=52, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), centered, 600).kind == "press_w"

def test_marker_lost_triggers_scan(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    f.tick(obs(VisualState.MARKING, 0), W, 100)
    f.tick(obs(VisualState.MARKING, 0), W, 200)
    lost = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    f.tick(obs(VisualState.GROUNDED, 0), lost, 300)
    assert f.tick(obs(VisualState.GROUNDED, 0), lost, 2400).kind == "scan"

def test_health_drop_releases_and_prones(cfg):
    f = BotFsm(cfg)
    hurt = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.2, health_drop_fast=True)
    assert f.tick(obs(VisualState.GROUNDED, 0), hurt, 0).kind == "release_w"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fsm.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现 `pubgbot/fsm.py`**

状态进入时重置计时器（`entered_ms`）。`SPAWN` 与 `UNKNOWN` 一律返回 `idle`。`PLANE` 计时从进入 PLANE 起算，到 `jump_delay_ms` 发一次 `jump` 并置 `jumped=True`。`GROUNDED`：`health_drop_fast` 优先返回 `release_w` 并把 `mode` 置 `prone`；`dist_ratio is None or dist_ratio > 1 - zone_margin_factor` → `open_map` 并置 `mode="marking"`；否则 `prone`。`MARKING`：`mode="marking"`，首帧发 `mark`，命令的 `arg` 由 `WorldView` 扩展字段提供——为保持接口稳定，`WorldView` 增加 `mark_point: tuple[int,int] | None = None`；`mark_point` 为空则回退 `idle`，有值时发一次 `mark`，300 ms 后发 `close_map`。`GROUNDED` 且 `mode=="running"`：`steer_pixels(...) != 0` 发 `turn`（`arg` 为像素步长），`== 0` 发 `press_w`；`marker_x is None` 且持续 `marker_lost_scan_ms` 发 `scan`；`scan` 累计超过 `scan_max_ms` 或 `dist_ratio <= 1 - zone_margin_factor` 时回 `prone`。
`mark` 只发一次：成功后置 `marked=True`，直到回 `GROUNDED` 或超时重置。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fsm.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/fsm.py tests/test_fsm.py
git commit -m "feat: bot fsm with per-state commands"
```

---

### Task 9: 动作层（键鼠原语与必释放）

**Files:**
- Create: `pubgbot/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `Command`（Task 8）、`AppConfig`（Task 1）
- Produces:
  - `class InputBackend(Protocol)`：`key_down(key: str) -> None`、`key_up(key: str) -> None`、`move_rel(dx: int, dy: int) -> None`、`click_right_at(x: int, y: int) -> None`、`click_left_at(x: int, y: int) -> None`
  - `class PyDirectBackend`（真实实现，基于 `pydirectinput`）
  - `class ActionExecutor: def __init__(self, backend: InputBackend, cfg: AppConfig, foreground_ok: Callable[[], bool]); def execute(self, cmd: Command) -> None; def release_all(self) -> None; def held_keys(self) -> set[str]`
  - `def center_of(rect: Rect) -> tuple[int,int]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_actions.py
import json
import pytest
from pathlib import Path
from pubgbot.actions import ActionExecutor
from pubgbot.config import load_config
from pubgbot.fsm import Command

class Fake:
    def __init__(self): self.calls = []
    def key_down(self, k): self.calls.append(("down", k))
    def key_up(self, k): self.calls.append(("up", k))
    def move_rel(self, dx, dy): self.calls.append(("move", dx, dy))
    def click_right_at(self, x, y): self.calls.append(("right", x, y))
    def click_left_at(self, x, y): self.calls.append(("left", x, y))

@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"steer_step_px": 8}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

def ex(cfg, fg=True):
    b = Fake(); return ActionExecutor(b, cfg, lambda: fg), b

def test_press_w_then_release(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w")); e.execute(Command("release_w"))
    assert b.calls == [("down", "w"), ("up", "w")]

def test_release_all_on_exception(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w"))
    with pytest.raises(RuntimeError):
        with e.held():
            raise RuntimeError("boom")
    assert ("up", "w") in b.calls

def test_turn_moves_mouse_by_step(cfg):
    e, b = ex(cfg)
    e.execute(Command("turn", -8))
    assert b.calls == [("move", -8, 0)]

def test_no_input_when_foreground_lost(cfg):
    e, b = ex(cfg, fg=False)
    e.execute(Command("press_w")); e.execute(Command("mark", (10, 20)))
    assert b.calls == []

def test_prone_releases_w_first(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w")); e.execute(Command("prone"))
    assert b.calls[:4] == [("down", "w"), ("up", "w"), ("down", "c"), ("up", "c")]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_actions.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`execute` 先 `foreground_ok()`，False 时直接返回并记（由调用方写日志）。
`press_w` 幂等（已按下不重复按）；`release_w` 未按下不发。`held_keys()` 返回内部集合，`release_all()` 逆序释放并清空。`held()` 是 `@contextmanager`，`finally` 调 `release_all()`。
`turn`：`move_rel(cmd.arg, 0)`。`prone`：先 `release_all()` 再点按 `c`（down/up）。`jump`：点按 `f`。`open_map`/`close_map`：点按 `m`。`mark`：`click_right_at(*cmd.arg)`。`click_start`：`click_left_at` 大厅按钮配置坐标。`scan`：水平小步扫视 `move_rel(30, 0)` 三次。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_actions.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/actions.py tests/test_actions.py
git commit -m "feat: action layer with guaranteed key release"
```

---

### Task 10: 安全层（热键、看门狗、权限与前台）

**Files:**
- Create: `pubgbot/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Consumes: `AppConfig`（Task 1）
- Produces:
  - `class Safety: def __init__(self, cfg: AppConfig, release: Callable[[], None], clock: Callable[[], int], foreground_ok: Callable[[], bool]); def install_hotkeys(self) -> None; def start(self) -> None; def stop_requested(self) -> bool; def heartbeat(self) -> None; def check(self) -> None; def request_stop(self, reason: str) -> None; def shutdown(self) -> None`
  - `class SafetyStop(Exception)`（`check()` 在需要停止时抛出）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_safety.py
import json
import pytest
from pathlib import Path
from pubgbot.config import load_config
from pubgbot.safety import Safety, SafetyStop

@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"watchdog_ms": 2000, "session_limit_ms": 28800000,
                    "unknown_alert_ms": 10000, "queue_alert_ms": 600000}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

class Clock:
    def __init__(self): self.t = 0
    def __call__(self): return self.t

def build(cfg, clock, fg=True):
    released = []
    s = Safety(cfg, lambda: released.append(1), clock, lambda: fg)
    return s, released

def test_watchdog_triggers_release(cfg):
    c = Clock(); s, rel = build(cfg, c)
    s.start(); s.heartbeat()
    c.t = 2001
    with pytest.raises(SafetyStop, match="watchdog"):
        s.check()
    assert rel == [1]

def test_session_limit_stops(cfg):
    c = Clock(); s, rel = build(cfg, c)
    s.start()
    c.t = 28800001
    with pytest.raises(SafetyStop, match="session"):
        s.check()

def test_foreground_lost_stops(cfg):
    c = Clock(); s, rel = build(cfg, c, fg=False)
    s.start()
    with pytest.raises(SafetyStop, match="foreground"):
        s.check()

def test_hotkey_stop_releases_once(cfg):
    c = Clock(); s, rel = build(cfg, c)
    s.start(); s.heartbeat()
    s.request_stop("F12")
    with pytest.raises(SafetyStop): s.check()
    assert rel == [1]
    assert s.stop_requested() is True
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_safety.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`install_hotkeys` 用 `keyboard.add_hotkey("f10", self._on_start)` / `("f12", self._on_stop)`，内部只置事件与时间戳，不做输入。`check()` 顺序：`stop_requested` → `foreground_ok()` 为 False 抛 `SafetyStop("foreground lost")` → 心跳超 `watchdog_ms` → 会话超 `session_limit_ms`。每个分支在抛异常前调用一次 `self._release_once()` 保证只释放一次。
`request_stop` 幂等：已停止则直接返回。
`install_hotkeys` 在测试中不调用（测试只覆盖 `check`/`request_stop`）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_safety.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/safety.py tests/test_safety.py
git commit -m "feat: safety layer with hotkeys, watchdog and session limit"
```

---

### Task 11: JSONL 日志与观察器主循环

**Files:**
- Create: `pubgbot/jsonlog.py`、`pubgbot/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `capture`（Task 3）、`StateRecognizer`（Task 7）、`BotFsm`（Task 8）、`ActionExecutor`（Task 9）、`Safety`（Task 10）
- Produces:
  - `class JsonlLogger: def __init__(self, path: Path); def write(self, record: dict) -> None`
  - `class ResolutionMismatch(Exception)`
  - `def validate_resolution(client: Rect, cfg: AppConfig) -> None`（不符抛 `ResolutionMismatch`，消息含期望与实际尺寸）
  - `class NullExecutor`（`execute()` 空实现，`release_all()` 空实现）—— 观察器模式注入
  - `def run(source_factory, gate, recognizer, fsm, executor, safety, logger, clock, max_frames: int | None = None) -> int`
  - `def main(argv: list[str] | None = None) -> int`（支持 `--observe`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_main.py
import json
from pathlib import Path
import pytest
from pubgbot.config import Rect, load_config
from pubgbot.jsonlog import JsonlLogger
from pubgbot.main import ResolutionMismatch, run, validate_resolution

def test_logger_writes_one_json_per_line(tmp_path):
    log = JsonlLogger(tmp_path / "x.jsonl")
    log.write({"frame": 1, "state": "LOBBY"}); log.write({"frame": 2, "state": "LOBBY"})
    lines = (tmp_path / "x.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["frame"] == 2

def test_validate_resolution_rejects_wrong_size(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    cfg = load_config(tmp_path / "a.json", tmp_path / "p.json")
    validate_resolution(Rect(0, 0, 64, 48), cfg)
    with pytest.raises(ResolutionMismatch, match="expected 64x48, got 1920x1080"):
        validate_resolution(Rect(0, 0, 1920, 1080), cfg)

def test_run_stops_on_safety_and_releases(tmp_path):
    from pubgbot.safety import SafetyStop
    state = {"n": 0}
    def source():
        state["n"] += 1
        return "FRAME"
    def gate(f): return None
    class Rec:
        def observe(self, fr, now): return "OBS"
    class Fsm:
        def tick(self, o, w, now):
            if now >= 2: raise SafetyStop("test")
            return type("C", (), {"kind": "idle"})()
    released = []
    class Ex:
        def execute(self, c): pass
        def release_all(self): released.append(1)
    class S:
        def check(self): pass
        def stop_requested(self): return False
        def heartbeat(self): pass
    run(lambda: source(), gate, Rec(), Fsm(), Ex(), S(), JsonlLogger(tmp_path / "l.jsonl"), lambda: state["n"], max_frames=10)
    assert released == [1]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`run` 循环：`safety.check()` → `frame = source()` → `gate(frame)` → `obs = recognizer.observe(frame, now)` → `world`（本任务先给默认空 `WorldView`，Task 15 填充）→ `cmd = fsm.tick(...)` → `executor.execute(cmd)` → `logger.write({...})` → `safety.heartbeat()` → 按 `frame_interval_ms` 限速。
`finally` 里 `executor.release_all()` 并记一条 `{"event":"shutdown"}`；`SafetyStop` 与 `KeyboardInterrupt` 都转换成正常返回码。
`main` 组装真实对象：`enable_dpi_awareness` → `is_admin` 检查（非管理员打印提示并返回 2）→ `find_game_window` → `validate_resolution(get_client_rect(hwnd), cfg)`（不符提示并返回 3）→ `open_frame_source` → `StateRecognizer` → `BotFsm` → `--observe` 时注入 `NullExecutor`，否则 `ActionExecutor(PyDirectBackend(), ...)` → `Safety`（`install_hotkeys` + `start`）→ `run`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_main.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add pubgbot/jsonlog.py pubgbot/main.py tests/test_main.py
git commit -m "feat: jsonl logging and observer main loop"
```

---

### Task 12: 锚点标定与叠加调试工具

**Files:**
- Create: `tools/pick_roi.py`、`tools/overlay.py`
- Modify: `config/anchors.1920x1080.json`（用标定结果替换占位 ROI，`calibrated` 置 `true`）
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `load_config`、`StateRecognizer`（Task 7）、`match_anchor`（Task 4）、`find_marker_x`（Task 5）、`detect_circle`（Task 6）
- Produces:
  - `tools/pick_roi.py`：`def crop_and_save(frame: np.ndarray, roi: Rect, out_png: Path) -> None`、`def next_anchor_name(cfg: AnchorsConfig) -> str`、`--list` 打印所有锚点及其标定状态
  - `tools/overlay.py`：`def render(frame: np.ndarray, cfg: AppConfig, obs: Observation, marker_x: int|None, circle: Circle|None) -> np.ndarray`（在图上画所有 ROI 边框、命中锚点、罗盘中心线、圈与标点）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tools.py
import cv2
import numpy as np
from pubgbot.config import Rect
from tools.pick_roi import crop_and_save

def test_crop_and_save_writes_roi(tmp_path):
    f = np.zeros((48, 64, 3), np.uint8); f[10:20, 20:32] = 200
    out = tmp_path / "t.png"
    crop_and_save(f, Rect(20, 10, 12, 10), out)
    assert cv2.imread(str(out)).shape == (10, 12, 3)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL — `tools.pick_roi` 不存在

- [ ] **Step 3: 实现并完成标定**

`pick_roi.py`：管理员 PowerShell 运行，先 `probe` 存一张当前帧，`cv2.imshow` 后用 `cv2.selectROI` 框选，写入 `templates/1920x1080/<name>.png` 并在 anchors json 里新增/覆盖对应条目与 `roi`。`--list` 输出每个锚点是否已有模板、`calibrated` 是否为 true。
`overlay.py`：全部绘制用不同颜色区分（ROI 灰、命中锚点绿、未命中红、罗盘中心线青、圈黄、标点红）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_tools.py -v`
Expected: 1 passed

- [ ] **Step 5: 人工标定（里程碑前置）**

在游戏里分别停在大厅、出生岛、飞机、跳伞、落地、地图打开、结算界面，各跑一次 `python tools/pick_roi.py` 框选对应 ROI 并保存模板；然后运行 `python tools/overlay.py` 确认所有 ROI 与命中点位置正确。
Expected: `--list` 中所有锚点 `template` 非空且 `calibrated: true`。

- [ ] **Step 6: 提交**

```bash
git add tools/pick_roi.py tools/overlay.py config/anchors.1920x1080.json templates tests/test_tools.py
git commit -m "feat: roi calibration and overlay debugging tools"
```

---

### Task 13: 离线回放、报告与 M1 验收

**Files:**
- Create: `tools/replay.py`、`tools/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `StateRecognizer`（Task 7）、JSONL 日志（Task 11）
- Produces:
  - `tools/report.py`：`def summarize(path: Path) -> dict`（返回 `{"frames": int, "state_seconds": dict[str,int], "unknown_ratio": float, "flaps": int}`；`flaps` 为同一状态片段内相邻不同状态的切换次数）
  - `def sample_points(path: Path, n: int, seed: int) -> list[dict]`（随机抽 n 条日志记录，含帧号与状态，供人工核对）
  - `tools/replay.py`：`def replay(frames_dir: Path, cfg: AppConfig, recognizer: StateRecognizer) -> list[Observation]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_report.py
import json
from pathlib import Path
from tools.report import summarize, sample_points

def make_log(tmp_path, states):
    p = tmp_path / "l.jsonl"
    rows = [{"frame": i, "state": s, "ts_ms": i * 100} for i, s in enumerate(states)]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p

def test_unknown_ratio_excludes_queue(tmp_path):
    s = summarize(make_log(tmp_path, ["GROUNDED", "UNKNOWN", "QUEUE", "GROUNDED"]))
    assert s["frames"] == 4
    assert round(s["unknown_ratio"], 3) == 0.333

def test_flaps_counts_switches_within_segment(tmp_path):
    s = summarize(make_log(tmp_path, ["LOBBY", "LOBBY", "GROUNDED", "LOBBY", "LOBBY"]))
    assert s["flaps"] == 2

def test_sample_points_deterministic(tmp_path):
    p = make_log(tmp_path, ["A", "B", "C", "D", "E"])
    assert [r["state"] for r in sample_points(p, 3, seed=7)] == [r["state"] for r in sample_points(p, 3, seed=7)]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

`unknown_ratio` = `UNKNOWN` 条数 / (总条数 − `QUEUE` 条数)，分母为 0 时返回 0.0。
`flaps` 统计相邻两条状态不同且都非 `QUEUE`/`UNKNOWN` 的次数。
`sample_points` 用 `random.Random(seed).sample`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_report.py -v`
Expected: 3 passed

- [ ] **Step 5: M1 人工验收（里程碑闸门）**

1. 管理员 PowerShell 运行 `python -m pubgbot.main --observe`，F10 启动，正常打完 10 局（排队、跳伞、落地都按平常操作），F12 结束。
2. `python tools/report.py logs/observer-*.jsonl` 查看 `unknown_ratio` 与 `flaps`。
3. `python tools/report.py --sample 20` 逐局随机抽 20 个点，对照录屏人工判定。
Expected: 识别一致率 ≥ 98%、片段内误切换 ≤ 2 次、非加载期 `UNKNOWN` < 1%。未达标则用 `tools/replay.py` 离线调阈值后重复本步骤。

- [ ] **Step 6: 提交**

```bash
git add tools/replay.py tools/report.py tests/test_report.py
git commit -m "feat: offline replay and recognition report"
```

---

### Task 14: M2 落地趴住（首次真正发输入）

**Files:**
- Modify: `pubgbot/main.py`（接入 `ActionExecutor` 与 `WorldView` 构造）、`pubgbot/fsm.py`（`DOWNED`/`RESULT`/`FREEFALL` 超时）、`pubgbot/actions.py`（新增 `click_return`）、`config/policy.json`（跳伞延迟、微动间隔、爬行时长）
- Test: `tests/test_ground_policy.py`

**Interfaces:**
- Consumes: 全部前序任务
- Produces:
  - `pubgbot/main.py` 中 `class WorldBuilder: def __init__(self, cfg: AppConfig); def build(self, frame: np.ndarray, state: VisualState) -> WorldView`（M2 阶段只填 `dist_ratio=None`，marker 与进圈留到 Task 15）
  - `Command.kind` 新增 `click_return`（点击结算界面的返回大厅按钮，坐标取 `cfg.anchors.roi("return_button")` 中心，冷却复用 `start_click_cooldown_ms`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ground_policy.py
import json
from pathlib import Path
import pytest
from pubgbot.config import load_config
from pubgbot.fsm import BotFsm, WorldView
from pubgbot.vision.state import Observation, VisualState

@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8,
                    "micro_move_interval_ms": 15000, "downed_crawl_ms": 2000,
                    "freefall_timeout_ms": 180000}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

W = WorldView(marker_x=None, compass_center_x=50, dist_ratio=None, health_drop_fast=False)

def test_micro_move_every_interval(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, 0).kind in ("release_w", "idle")
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, 15000).kind == "prone"

def test_freefall_presses_w(cfg):
    assert BotFsm(cfg).tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 0).kind == "press_w"

def test_freefall_timeout_releases(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 0).kind == "press_w"
    assert f.tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 180000).kind == "release_w"

def test_downed_crawls_then_prones(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.DOWNED, {}, 1.0), W, 0).kind == "press_w"
    assert f.tick(Observation(VisualState.DOWNED, {}, 1.0), W, 2001).kind == "prone"

def test_result_clicks_return_with_cooldown(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 0).kind == "click_return"
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 1000).kind == "idle"
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 5000).kind == "click_return"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ground_policy.py -v`
Expected: FAIL — `DOWNED`/`RESULT` 无命令实现，`downed_crawl_ms` 未被使用

- [ ] **Step 3: 实现**

`FREEFALL`：每帧 `press_w`（幂等）；停留超过 `freefall_timeout_ms` 改发 `release_w` 并记异常（跳伞卡住）。
`DOWNED`：进入后朝当前朝向 `press_w` 爬行，超过 `downed_crawl_ms` 改发 `prone`（单人四排无队友，爬只为离开原位）。
`RESULT`：每 `start_click_cooldown_ms` 发一次 `click_return`。
`WorldBuilder.build` 先只返回 `WorldView(None, center_x, None, False)`，`center_x` 取罗盘 ROI 中心。
`actions.py`：`click_return` 读 `cfg.anchors.roi("return_button")` 中心后 `click_left_at`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ground_policy.py -v`
Expected: 5 passed；再跑全量 `pytest -q` 确认无回归

- [ ] **Step 5: M2 人工验收（里程碑闸门）**

管理员 PowerShell 运行 `python -m pubgbot.main`，F10 启动，连续 3 局。
Expected: 每局能自动点开始、进对局、起飞后跳伞、落地趴下并活到首个安全区；全程无卡键（F12 后角色立即停止移动）；期间手动切出游戏时脚本暂停且不发送输入。

- [ ] **Step 6: 提交**

```bash
git add pubgbot/fsm.py pubgbot/main.py config/policy.json tests/test_ground_policy.py
git commit -m "feat: ground prone policy and micro-move"
```

---

### Task 15: M3 地图标点与罗盘闭环进圈

**Files:**
- Modify: `pubgbot/main.py`（`WorldBuilder.build` 填充 `dist_ratio`、`marker_x`、`mark_point`、`health_drop_fast`）、`pubgbot/fsm.py`（`stuck` 与标点失败计数）
- Test: `tests/test_zone_run.py`

**Interfaces:**
- Consumes: `detect_circle`（Task 6）、`nearest_edge_point`/`clamp_to`（Task 6）、`find_marker_x`/`steer_pixels`（Task 5）、`WorldBuilder`（Task 14）
- Produces:
  - `pubgbot/main.py`：`WorldBuilder.build` 完整版：`MARKING` 时检测白圈并用 `nearest_edge_point(player, circle, circle_inner_factor)` 得 `mark_point`，`clamp_to` 到地图 ROI；`GROUNDED` 时用最小地图算 `dist_ratio = dist(player_center, circle_center) / circle_r`；罗盘 ROI 内 `find_marker_x` 得 `marker_x`
  - `pubgbot/main.py`：`class HealthTracker: def __init__(self, window_ms: int, drop_ratio: float); def update(self, ratio: float, now_ms: int) -> bool`（`True` 表示窗口内血量占比下降超过 `drop_ratio`）
  - `BotFsm` 增加 `stuck` 处理：`dist_ratio` 在 `stuck_detect_ms` 内变化小于 `1e-3` 时发 `turn` 小角度绕行并计数，达 `run_fail_limit` 回 `prone`；`MARKING` 中 `mark_point` 为空超过 `marking_timeout_ms` 记一次失败，累计达 `mark_fail_limit` 回 `prone`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_zone_run.py
import json
from pathlib import Path
import pytest
from pubgbot.config import load_config
from pubgbot.fsm import BotFsm, WorldView
from pubgbot.main import HealthTracker
from pubgbot.vision.state import Observation, VisualState
from pubgbot.vision.circle import Circle, nearest_edge_point

@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {},
         "values": {"circle_inner_factor": 0.9}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8,
                    "micro_move_interval_ms": 15000, "health_drop_window_ms": 3000,
                    "health_drop_ratio": 0.05}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")

def test_mark_point_uses_circle_factor(cfg):
    assert nearest_edge_point((10.0, 100.0), Circle(100, 100, 60), cfg.anchors.value("circle_inner_factor")) == (46, 100)

def test_health_tracker_detects_drop():
    h = HealthTracker(window_ms=3000, drop_ratio=0.05)
    assert h.update(1.0, 0) is False
    assert h.update(0.97, 500) is False
    assert h.update(0.90, 1000) is True
    assert h.update(0.90, 5000) is False          # 基线过期后重新建立

def test_stuck_count_reaches_limit_then_prone(cfg):
    f = BotFsm(cfg)
    w = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    f.tick(Observation(VisualState.GROUNDED, {}, 1.0), w, 0)          # 触发 open_map
    marked = WorldView(50, 50, 1.2, False, mark_point=(10, 10))
    f.tick(Observation(VisualState.MARKING, {}, 1.0), marked, 100)
    f.tick(Observation(VisualState.MARKING, {}, 1.0), marked, 200)
    allowed = ("press_w", "turn", "idle")
    for now in range(300, 60000, 100):
        assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), w, now).kind in allowed
    assert f.mode == "prone"

def test_mark_failures_lead_to_prone(cfg):
    f = BotFsm(cfg)
    outside = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)
    no_point = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.5,
                         health_drop_fast=False, mark_point=None)
    for base in (0, 5000):
        f.tick(Observation(VisualState.GROUNDED, {}, 1.0), outside, base)
        f.tick(Observation(VisualState.MARKING, {}, 1.0), no_point, base + 100)
        f.tick(Observation(VisualState.MARKING, {}, 1.0), no_point, base + 1700)
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), outside, 10000).kind == "prone"
    assert f.mode == "prone"

def test_remark_after_interval(cfg):
    f = BotFsm(cfg)
    w = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), w, 0).kind == "open_map"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_zone_run.py -v`
Expected: FAIL — `WorldView` 无 `mark_point` 字段

- [ ] **Step 3: 实现**

`mark_point` 已在 Task 8 的 `WorldView` 中预留，本任务负责在 `build` 中真实填充。
`WorldBuilder.build` 在 `MARKING`：`circle = detect_circle(frame, cfg.anchors.roi("map"), ...)`；玩家位置先用 `map` ROI 中心；`mark_point = clamp_to(map_roi, nearest_edge_point(player, circle, factor))`；检测不到圈则为 `None`。
在 `GROUNDED`：`mm = cfg.anchors.roi("minimap")`；玩家视为 `mm` 中心；圈在 `mm` 内检测，`dist_ratio = hypot(cx - mm.cx, cy - mm.cy) / r`；小地图检测不到圈时 `dist_ratio = None`；`health_drop_fast = HealthTracker.update(color_ratio(血量条 ROI 白像素), now)`。
`fsm`：`mode=="running"` 时若 `dist_ratio` 连续 `stuck_detect_ms` 变化 < `1e-3` 则发 `turn`（±25 px）并累加 `stuck_count`，达到 `run_fail_limit` 回 `prone`；`MARKING` 中 `mark_point` 为空超过 `marking_timeout_ms` 发 `close_map` 并把 `mark_fail_count` 加一，达到 `mark_fail_limit` 回 `prone`；`dist_ratio <= 1 - zone_margin_factor` 或距上次标记超 `remark_interval_ms` 时重新 `open_map`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_zone_run.py -v`
Expected: 5 passed；`pytest -q` 全绿

- [ ] **Step 5: M3 人工验收（里程碑闸门）**

管理员 PowerShell 运行 `python -m pubgbot.main`，连续 5 局，观察 `logs/` 与 `tools/overlay.py`。
Expected: ≥ 4 局成功进圈（首次进圈 ≤ 90 s）；黄标丢失能扫视找回或回退趴下；地图不残留打开；F12 立即停下并松开 W。

- [ ] **Step 6: 提交**

```bash
git add pubgbot/main.py pubgbot/fsm.py tests/test_zone_run.py
git commit -m "feat: map marking and compass closed-loop zone run"
```

---

### Task 16: M4 长跑与收尾

**Files:**
- Modify: `pubgbot/main.py`（`--duration` 限制、退出汇总）、`README.md`
- Test: `tests/test_longrun.py`

**Interfaces:**
- Consumes: 全部前序任务
- Produces:
  - `main` 支持 `--duration <minutes>`，到点后走正常 `shutdown` 路径（释放按键、写汇总）
  - `run(...)` 返回 `{"frames": int, "states": dict[str,int], "stops": list[str]}`（供 `main` 打印与写日志）
  - `def summarize_run(rows: list[dict]) -> dict`
  - `def alerts_for(state: str, elapsed_ms: int, cfg: AppConfig) -> list[str]`（`QUEUE` 超 `queue_alert_ms` 返回 `["queue_timeout"]`，`UNKNOWN` 超 `unknown_alert_ms` 返回 `["unknown"]`，否则 `[]`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_longrun.py
import json
from pubgbot.config import load_config
from pubgbot.main import alerts_for, summarize_run

def test_summarize_counts_states():
    rows = [{"state": "LOBBY"}, {"state": "GROUNDED"}, {"state": "GROUNDED"}]
    s = summarize_run(rows)
    assert s["states"]["GROUNDED"] == 2
    assert s["frames"] == 3

def test_alerts_after_thresholds(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"queue_alert_ms": 600000, "unknown_alert_ms": 10000}}
    (tmp_path / "a.json").write_text(json.dumps(a)); (tmp_path / "p.json").write_text(json.dumps(p))
    cfg = load_config(tmp_path / "a.json", tmp_path / "p.json")
    assert alerts_for("QUEUE", 599999, cfg) == []
    assert alerts_for("QUEUE", 600000, cfg) == ["queue_timeout"]
    assert alerts_for("UNKNOWN", 10000, cfg) == ["unknown"]
    assert alerts_for("GROUNDED", 999999, cfg) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_longrun.py -v`
Expected: FAIL — `summarize_run` 不存在

- [ ] **Step 3: 实现**

`summarize_run(rows: list[dict]) -> dict` 统计帧数与各状态计数；`alerts_for` 按状态停留时长与阈值比较；主循环对同一状态只告警一次（记 `{"event":"alert","kind":...}`），不中断运行。`main` 结束时打印并写一条 `{"event":"summary", ...}`。
`README.md` 写清部署与调试顺序（在游戏机执行）：安装 Python 3.12 64 位 → `pip install -e .` → 管理员运行 `python tools/doctor.py` 全 PASS → `python tools/probe_capture.py` 确认非黑帧 → 锁定游戏内设置 → `python tools/pick_roi.py` 标定全部锚点 → `python tools/overlay.py` 目视核对 → `python -m pubgbot.main --observe` 跑 10 局 → `python tools/report.py` 核对识别率 → `python -m pubgbot.main` 正式运行。同时注明 F10/F12、日志位置、1920×1080 窗口化、管理员权限。

- [ ] **Step 4: 运行确认通过**

Run: `pytest -q`
Expected: 全部通过

- [ ] **Step 5: M4 人工验收（里程碑闸门）**

`python -m pubgbot.main --duration 480`，无人值守 8 小时。
Expected: 无崩溃；F12 可随时介入；日志无卡键记录；异常（若有）均以 `SafetyStop` 自停并留下 `event` 记录；结束后进程不残留。

- [ ] **Step 6: 提交**

```bash
git add pubgbot/main.py README.md tests/test_longrun.py
git commit -m "feat: long-run limits, summary and usage docs"
```

---

## 实施顺序与依赖

```
1 → 2 → 3(M0闸门) → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12(标定) → 13(M1闸门) → 14(M2闸门) → 15(M3闸门) → 16(M4闸门)
```

Task 3 的 M0 闸门不通过则整个项目停止；Task 12 未完成标定则 Task 13 无法开始（识别率无从谈起）；每个里程碑闸门都是人工验证，未达标不进入下一阶段。开发机无游戏，所有闸门在用户的游戏机上执行。

## 交付到游戏机的交接清单

1. 代码与配置已提交（含 `config/`、`templates/` 占位）。
2. 游戏机安装 Python 3.12 64 位，`pip install -e .`。
3. 管理员运行 `python tools/doctor.py`，确认依赖、显示器、捕获后端全 PASS。
4. `python tools/probe_capture.py` 打开产物确认非黑帧（M0）。
5. 锁定游戏内设置（简中、单人四排 FPP、HUD 默认、小地图默认、色盲关）。
6. `python tools/pick_roi.py` 逐个标定锚点，`calibrated` 置 `true`。
7. `python tools/overlay.py` 逐项核对 ROI 与命中。
8. `python -m pubgbot.main --observe` 跑 10 局，`python tools/report.py` 看识别率（M1）。
9. 通过后 `python -m pubgbot.main` 正式运行（M2–M4）。
10. 任何异常把 `logs/*.jsonl` 与 `[ERR]` 行贴回，按 `reason` 字段定位。
