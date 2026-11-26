# pubg_bp — PUBG PC 纯视觉挂机脚本

通过**外部截屏 + 键鼠模拟**完成 PUBG PC 的挂机流程：自动排队 → 跳伞 → 落地趴住 → 按地图标点与罗盘黄标跟圈。不读游戏内存、不注入、不修改游戏文件、不碰网络协议。

> 首次在游戏机上运行前，请先按下面的「部署步骤」逐项完成标定；`config/anchors.1920x1080.json` 里现在是**占位 ROI**（`calibrated: false`），未标定前识别结果不可信。

## 目标环境

- Windows 11 64 位、Python 3.12（64 位）
- PUBG PC（Steam），**客户区 1920×1080 窗口化**（不会做缩放适配，尺寸不符会拒绝启动）
- 游戏内设置基线：简体中文、HUD 缩放默认、小地图默认、色盲模式关闭
- 显示器不自动休眠/锁屏；不要用远程桌面（DXGI 截屏会失效）
- 终端需**以管理员身份运行**（否则 F10/F12 与 SendInput 会被系统拦截）

## 安装

```powershell
cd d:\workspace_py\pubg_bp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
# 国内网络可加：-i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 部署步骤（在游戏机上一次做完）

1. **启动游戏并停在大厅**，窗口化、不要最小化。

2. **环境自检**

   ```powershell
   .\.venv\Scripts\python.exe tools\doctor.py
   ```

   逐项检查 Python 位数、管理员、DPI、显示器、依赖、配置、窗口、试截一帧。有 `FAIL` 按提示处理。

3. **M0 截图验证**（决定能否继续）

   ```powershell
   .\.venv\Scripts\python.exe tools\probe_capture.py
   ```

   打开 `samples\probe_dxcam.png` 与 `samples\probe_mss.png`，确认是游戏画面而不是纯黑/桌面/锁屏。

4. **标定 ROI 与模板**（逐个锚点）

   ```powershell
   .\.venv\Scripts\python.exe tools\pick_roi.py --list           # 看还有哪些没标
   .\.venv\Scripts\python.exe tools\pick_roi.py --name lobby_start
   ```

   会截当前画面并弹出框选窗口：框住该 UI 元素 → Enter。模板存到 `templates\1920x1080\<name>.png`，ROI 回写到 `config\anchors.1920x1080.json`。

   再标定两个**按钮点击区域**（大厅开始按钮 / 结算返回按钮）：

   ```powershell
   .\.venv\Scripts\python.exe tools\pick_roi.py --roi lobby_button
   .\.venv\Scripts\python.exe tools\pick_roi.py --roi return_button
   ```

   **全部锚点标完后**，把该文件里的 `"calibrated": false` 改为 `true`。

5. **观察器模式**（只识别、不发送任何按键，可放心长时间跑）

   ```powershell
   .\.venv\Scripts\python.exe -m pubgbot --observe --log logs\observe.jsonl
   ```

   你正常手动玩几局，它在后台记录每帧的状态、分数、世界信息。此模式下鼠标键盘完全不受影响。

6. **抽检识别结果**

   ```powershell
   .\.venv\Scripts\python.exe tools\report.py logs\observe.jsonl --samples 20
   ```

   看 `state_counts` 与 `unknown_ratio`；抽出的样本对应日志里的 `scores`，判断是哪块识别不准，再微调对应锚点的 ROI 或阈值。

7. **实操作模式**（前面都稳定后再跑）

   ```powershell
   .\.venv\Scripts\python.exe -m pubgbot --live
   ```

   - **F10** 开始执行（在此之前只记录、不按键）
   - **F12** 急停（立即释放所有按键）
   - **Ctrl+C** 退出
   - 想省掉 F10：加 `--autostart`

   建议先加 `--max-frames 300`（约 25 秒）验证行为，再放开长跑。

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--observe` / `--live` | 只识别（默认） / 真正操作游戏 |
| `--autostart` | 启动即执行，不等 F10 |
| `--max-frames N` | 跑 N 帧后退出 |
| `--prefer dxcam\|mss` | 优选截屏后端 |
| `--log PATH` | JSONL 日志路径（默认 `logs/run.jsonl`） |
| `--anchors/--policy` | 指定配置文件 |

## 配置文件

- `config/anchors.1920x1080.json`：分辨率、ROI、模板锚点（含 `state` 归属）、HSV 区间、几何参数
  - `calibrated`、`rois.*`、`anchors.*.roi/template/threshold/state`
  - `hsv.waypoint_yellow`（罗盘黄标）、`hsv.circle_white`（白圈）、`hsv.health_white/red`
  - `values.circle_inner_factor`（标点在 0.9R 圈内）、`marker_min_pixels`、`circle_r_min/max`、`scan_step_px`
- `config/policy.json`：所有时间与行为阈值（毫秒）
  - `vote_frames`/`min_dwell_ms`（防抖）、`dead_zone_px`/`steer_step_px`（罗盘闭环）
  - `jump_delay_ms`、`marking_timeout_ms`、`mark_fail_limit`、`run_fail_limit`、`stuck_detect_ms`
  - `remark_interval_ms`、`micro_move_interval_ms`、`downed_crawl_ms`、`freefall_timeout_ms`
  - `watchdog_ms`、`session_limit_ms`、`unknown_alert_ms`、`queue_alert_ms`、`health_drop_*`

## 日志与排错

- 每帧一行 JSONL：`frame / ts_ms / state / confidence / scores / world / mode / command / arg / reason`
- 关键节点 INFO：窗口发现、客户区、DPI、捕获后端、状态切换、命令执行、模式切换、安全事件
- 异常统一为 `[ERR] <模块>: <原因> -> <建议动作>`，不会裸抛 traceback 退出

常见错误与处置：

| 报错 | 原因 / 处置 |
|------|------|
| `分辨率不匹配: expected 1920x1080, got ...` | 游戏窗口尺寸不对，改成 1920×1080 窗口化，不要缩放 |
| `black frame: mean=...` | 最小化/锁屏/独占全屏，或换 `--prefer mss` |
| `找不到 PUBG 窗口` | 游戏没启动、被最小化，或进程名/类名不同（会打印当前可见窗口供核对） |
| `当前不是管理员` | 用管理员身份重开终端（`--observe` 可免） |
| `热键注册失败` | 同上，无热键时用 Ctrl+C 停止 |
| 状态长期 `UNKNOWN` | 未标定 / 模板不匹配 / HUD 缩放被改过，重跑 `pick_roi.py` 与 `report.py` 核对 |

## 当前实现进度

| 里程碑 | 状态 |
|--------|------|
| M0 截屏可行性 | 代码完成（`tools/probe_capture.py`），**需在游戏机验证** |
| M1 状态识别 | 代码完成 + 合成帧单测，真实准确率需标定后用观察器统计 |
| M2 落地趴住 / 标点进圈 | 代码完成 + 逻辑单测（好运、黄标丢失扫视、标点失败、卡住回趴下） |
| M3 全流程动作 | 代码完成，等真机联调 |
| M4 长跑稳定性 | 8h 会话上限、看门狗、UNKNOWN/QUEUE 告警已实现 |

## 已知限制（务必了解）

- 所有 ROI 与模板都需在游戏机上标定；语言、HUD 缩放、色盲模式变化会让模板失效。
- 小地图圈检测失败时按保守策略**原地趴下**，不会盲走。
- `DOWNED` 只做短距离爬行后趴住（单人四排没有队友可救）。
- 只支持 1920×1080 客户区，不做缩放适配。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

单测全部使用缩小分辨率（64×48）的合成帧，不需要游戏；导入任何模块都不会访问游戏窗口。
