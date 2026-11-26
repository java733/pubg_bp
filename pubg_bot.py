# -*- coding: utf-8 -*-
"""
PUBG 挂机脚本（Python 版）—— 按键精灵 PC 版源码的 1:1 移植
================================================================================

这份脚本的目标只有一个：**和按键精灵那版功能完全一样，但你能看懂、能改**。
所有函数名、执行顺序、等待毫秒数、坐标、颜色都和原脚本一一对应，
只是把按键精灵的「内置命令」换成了 Python 的等价写法。

--------------------------------------------------------------------------------
一、怎么运行
--------------------------------------------------------------------------------
    cd d:\\development\\workspace\\pubg_bp
    .\\.venv\\Scripts\\python.exe pubg_bot.py

    调试用：打印屏幕上某个点的颜色（改坐标/颜色时用得上）
    .\\.venv\\Scripts\\python.exe pubg_bot.py --color 969 57
        -> 输出 969,57 这个点的颜色，比如 F2F3F2
        -> 如果和脚本里写的颜色不一样了，把 CONFIG 里的常量改掉即可

    更完整的单功能调试工具（推荐）：demo.py
    .\\.venv\\Scripts\\python.exe demo.py            查看所有命令
    .\\.venv\\Scripts\\python.exe demo.py state      一次看全：窗口 + 关键点颜色 + 判断结果
    .\\.venv\\Scripts\\python.exe demo.py move 300   只测「转视角」，并自动判断有没有转

--------------------------------------------------------------------------------
二、运行前提（不满足就会跑歪）
--------------------------------------------------------------------------------
    1. 游戏必须是「窗口化」或「无边框窗口」，分辨率 1920x1080。
       全屏独占模式下截屏取色会拿到黑屏，鼠标键盘也可能无效。
    2. 游戏已启动，停在「大厅」。
    3. 显示缩放（DPI）最好是 100%；脚本已启用 DPI 感知，125% 也能用，
       但 1920x1080 的坐标是按 100% 标定的。
    4. 游戏如果是以管理员身份启动的，这个脚本也要用管理员身份启动，
       否则发不进去鼠标键盘。
    5. 运行期间不要动鼠标键盘（脚本会占用）。

--------------------------------------------------------------------------------
三、紧急停止
--------------------------------------------------------------------------------
    F12             -> 随时停止（全局热键，游戏里直接按就行，不用切窗口）
                       停止后会自动松开按住的键（W / Shift）
    Ctrl + C        -> 在命令行窗口里也能停
    鼠标甩到屏幕左上角 (0,0) -> pydirectinput 的 FAILSAFE 会抛异常，脚本停止

    想换停止键：改下面「可调参数」里的 STOP_HOTKEY（可用 "f12"、"ctrl+q" 等）

--------------------------------------------------------------------------------
四、按键精灵命令 -> Python 写法 对照表
--------------------------------------------------------------------------------
    TracePrint("x")            -> log("x")                打印带时间的日志
    Delay 1000                 -> delay(1000)             睡 1000 毫秒
    KeyPress "F", 1            -> key_press("f")          按一下再松开
    KeyDown "W", 1             -> key_down("w")           按住不放
    KeyUp "W", 1               -> key_up("w")             松开
    MoveTo 100, 200            -> move_to(100, 200)       鼠标移到屏幕绝对坐标
    MoveRS -10, 0              -> move_rs(-10, 0)         鼠标相对移动（转头）
    LeftClick 1                -> left_click()            左键单击
    LeftDown 1 / LeftUp 1      -> left_down() / left_up() 左键按下 / 松开
    RightClick 1               -> right_click()           右键单击
    GetPixelColor(x, y)        -> get_pixel_color(x, y)   取一个点的颜色 "RRGGBB"
    FindColor 区域, 颜色       -> find_color(...)         在矩形里找颜色
    Plugin.Window.Find/Move    -> 调整窗口()              找窗口并移到 (0,0)

--------------------------------------------------------------------------------
五、原脚本里还没做完的 TODO（我原样保留了，没有替你实现）
--------------------------------------------------------------------------------
    - 自动进圈（毒圈判断）
    - 自动捡东西（捡东西() 函数写好了，但主流程里没有调用）
    - 死亡后自动开下一局（目前和原脚本一样，跑完一轮就结束）
    想开多轮：把下面的 RUN_FOREVER 改成 True。
"""

import ctypes
import ctypes.wintypes as wintypes
import sys
import threading
import time

import mss          # 截屏取色（比一个点一个点调系统 API 快很多）
import numpy as np  # 在截图里批量找颜色
import pydirectinput  # 模拟鼠标键盘（用的是扫描码，游戏能识别）
import win32con
import win32gui


# ==============================================================================
# 零、可调参数（改这里就够了，不用动下面的逻辑）
# ==============================================================================

# 是否无限重复「开局 -> 跳伞 -> 死亡 -> 退出 -> 再开局」
# 原按键精灵脚本跑完一轮就结束，所以这里默认 False（保持一致）
RUN_FOREVER = False

# 游戏窗口的类名，任务管理器 / Spy 工具里能看到
WINDOW_CLASS = "UnrealWindow"

# 窗口标题里包含这个关键字就算找到了。
# 原脚本是完整标题 "PUBG：绝地求生 "，这里放宽成包含 "PUBG"，
# 这样游戏更名、标题前面多了标记也能找到。
WINDOW_TITLE_KEYWORD = "PUBG"


# ------------------------------------------------------------------------------
# 颜色常量（格式 "RRGGBB"，注意是 **RGB** 顺序）
#
# ⚠ 颜色顺序：按键精灵的 GetPixelColor / FindColor 返回的是 **BGR 顺序**
#   （"BBGGRR"），而 Python 这边统一用 RGB（"RRGGBB"）。
#   所以原脚本里红、蓝分量不同的颜色必须把两端对调，否则永远匹配不上：
#
#       原脚本写法        真实 RGB（本脚本要写的）
#       11E5E9        ->  E9E511     （黄标，已实测确认）
#       00C8F8        ->  F8C800
#
#   对称的颜色（F2F3F2 / E5E5E5 / F2F2F2）两端一样，不用改。
#   拿不准时用：python demo.py color x y  或  python demo.py marker
# ------------------------------------------------------------------------------
COLOR_ENTERED_GAME = "F2F3F2"   # 判断是否进入游戏：头顶箭头 (969, 57)
COLOR_PLANE_READY_1 = "E5E5E5"  # 判断是否可跳伞：左"跳伞"文字 (1096, 661)
COLOR_PLANE_READY_2 = "F2F2F2"  # 判断是否可跳伞：右"跳伞"文字 (1146, 663)
COLOR_DEAD_1 = "F8C800"         # 判断是否死亡：(113, 182)   原脚本 00C8F8（BGR）
COLOR_DEAD_2 = "F8C800"         # 判断是否死亡：(373, 195)   原脚本 00C8F8（BGR）
COLOR_MAP_MARKER = "E9E511"     # 黄标（地图标记）颜色，实测 RGB=E9E511，原脚本 11E5E9（BGR）

# 颜色比较容差：每个通道允许相差多少。
#   0 = 严格相等（按键精灵 IfColor 的原始行为）
#   3 = 允许每个通道差 3
# 为什么默认 3：窗口化/无边框模式下 Windows 桌面合成（DWM）会对画面做一次
# 颜色转换，实测游戏里的 F2F3F2 读出来是 F1F2F1（每通道差 1）。
# 如果严格比较，状态判断会永远不成立 —— 所以这里留一点余量。
COLOR_TOLERANCE = 3


# ------------------------------------------------------------------------------
# 坐标常量（屏幕绝对坐标，1920x1080 下标定）
# 游戏更新 / 换分辨率后，大部分只需要改这里
# ------------------------------------------------------------------------------
# —— 大厅：开始游戏按钮 ——
LOBBY_CLICKS = [
    (254, 835),   # 选择游戏模式
    (472, 568),   # （原脚本第 2 下点击）
    (525, 618),   # （原脚本第 3 下点击）
    # (567, 768), # 艾伦格（原脚本注释掉了，这里也注释掉）
    (1140, 765),  # 泰戈（地图）
    (334, 935),   # （原脚本第 5 下点击）
    (206, 994),   # 开始游戏
]

# —— 地图：右键标记点位 ——
MAP_MARK_POINT = (1205, 706)

# —— 黄标检测区域（屏幕顶部的方向指示条）——
MARKER_SEARCH_BOX = (560, 37, 1362, 81)  # x1, y1, x2, y2
MARKER_CENTER_X = 970                    # 黄标 x 坐标在 969~971 之间算对准

# —— 死亡后的结算界面点击 ——
DEATH_CLICKS = [
    (162, 1051),  # 死亡后第 1 下
    (846, 697),   # 死亡后第 2 下
    (167, 1044),  # 退出结算第 1 下（中间等 25 秒）
    (210, 1043),  # 退出结算第 2 下（中间等 5 秒）
]


# ==============================================================================
# 一、基础工具：日志 / 延时 / 全局停止热键
# ==============================================================================

# 全局停止热键：按一下就让脚本停下来（不需要切回命令行窗口）
STOP_HOTKEY = "f12"


class StopRequested(Exception):
    """按下停止热键后抛出的信号，用来从任意位置跳出主流程。"""


# 用一个「事件」做线程间的开关：F12 的回调线程负责 set()，
# delay() 里不停检查它，一旦被 set 就抛 StopRequested 结束流程。
# （set() 和 is_set() 都是线程安全的）
_stop_event = threading.Event()


def _request_stop() -> None:
    """F12 被按下时由 keyboard 库在后台线程调用。"""
    _stop_event.set()


def install_stop_hotkey() -> None:
    """
    注册全局停止热键（默认 F12）。

    为什么用 keyboard 库：它装的是系统级键盘钩子，无论当前焦点在游戏里
    还是在浏览器里，按键都能被收到，不需要切回命令行窗口。

    注册失败（极少见，通常是权限问题）时只打警告，脚本照常运行，
    你还能用 Ctrl+C 停止。
    """
    try:
        import keyboard  # 延迟导入：这个库一导入就开始装钩子，放到这里更可控

        keyboard.add_hotkey(STOP_HOTKEY, _request_stop)
        log(f"已注册停止热键：按 {STOP_HOTKEY.upper()} 可随时停止脚本")
    except Exception as exc:
        log(f"警告：注册停止热键失败（{exc}），请改用 Ctrl+C 停止")


def delay(ms: int) -> None:
    """
    等价于按键精灵的 Delay：暂停 ms 毫秒。

    和普通 time.sleep 的区别：这里把长等待切成 50ms 一小段，
    每段之间检查一次「是否按了停止热键」，这样哪怕正在等 25 秒的结算动画，
    按 F12 也能在 50ms 内停下来。
    """
    end_time = time.monotonic() + ms / 1000.0
    while True:
        if _stop_event.is_set():
            raise StopRequested  # 有人按了停止热键，直接跳出流程
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def log(msg: str) -> None:
    """等价于按键精灵的 TracePrint：打印一行带时间的日志，方便你对照流程。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ==============================================================================
# 二、屏幕取色（对应 GetPixelColor / FindColor）
# ==============================================================================

# mss 的对象创建一次就好，反复创建会很慢
_screen = None

# 新版 mss 用 mss.MSS()，旧版只有 mss.mss()，这里两种都兼容
_mss_factory = getattr(mss, "MSS", mss.mss)


def _get_screen():
    """懒加载截屏对象：第一次用到时才创建。"""
    global _screen
    if _screen is None:
        _screen = _mss_factory()
    return _screen


def get_pixel_color(x: int, y: int) -> str:
    """
    等价于按键精灵的 GetPixelColor(x, y)。

    返回 "RRGGBB" 格式的大写字符串，比如 "F2F3F2"，
    这样就可以直接和常量做 == 比较，和原脚本写法一致。
    """
    shot = _get_screen().grab({"left": x, "top": y, "width": 1, "height": 1})
    # pixel(0, 0) 直接返回这个点在本截图里的 (R, G, B)
    r, g, b = shot.pixel(0, 0)
    return f"{r:02X}{g:02X}{b:02X}"


def color_matches(actual: str, expected: str) -> bool:
    """
    判断两个 "RRGGBB" 颜色是否相同（允许 COLOR_TOLERANCE 的误差）。

    等价于按键精灵的 IfColor x, y, 颜色, 相似度 —— 只是这里的相似度
    固定用 COLOR_TOLERANCE，避免窗口化模式下的 ±1 色差让判断永远失败。
    """
    for i in (0, 2, 4):  # R、G、B 三段
        if abs(int(actual[i:i + 2], 16) - int(expected[i:i + 2], 16)) > COLOR_TOLERANCE:
            return False
    return True


def find_color(x1: int, y1: int, x2: int, y2: int, color: str):
    """
    等价于按键精灵的 FindColor(x1, y1, x2, y2, color, intX, intY)。

    在矩形区域内寻找颜色 color：
      - 找到 -> 返回第一个匹配点的屏幕坐标 (x, y)
      - 找不到 -> 返回 None（原脚本里是 intX/intY <= 0）
    扫描顺序和按键精灵一样：从上到下、从左到右，取第一个。

    实现说明：
      这里不是逐点调用系统取色（那样太慢），而是一次性把整块区域截下来，
      再用 numpy 做整块比对，速度是逐点取色的几十倍。
    """
    left, top = min(x1, x2), min(y1, y2)
    width = abs(x2 - x1) + 1
    height = abs(y2 - y1) + 1

    shot = _get_screen().grab({"left": left, "top": top, "width": width, "height": height})

    # bgra 是一整块字节，按 (高, 宽, 4) 重新排列 -> [y][x] = [B, G, R, A]
    img = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(height, width, 4)

    # 把 "11E5E9" 拆成 R/G/B 三个数字
    want_r = int(color[0:2], 16)
    want_g = int(color[2:4], 16)
    want_b = int(color[4:6], 16)

    # 三个通道都在容差范围内才算命中（容差见 COLOR_TOLERANCE）
    tol = COLOR_TOLERANCE
    mask = (
        (img[:, :, 2] >= want_r - tol) & (img[:, :, 2] <= want_r + tol)   # R
        & (img[:, :, 1] >= want_g - tol) & (img[:, :, 1] <= want_g + tol)  # G
        & (img[:, :, 0] >= want_b - tol) & (img[:, :, 0] <= want_b + tol)  # B
    )

    # nonzero 返回的顺序就是「先按行、再按列」，和按键精灵的扫描顺序一致
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return left + int(xs[0]), top + int(ys[0])


# ==============================================================================
# 三、鼠标键盘（对应 MoveTo / KeyPress / KeyDown / KeyUp / MoveRS / 点击）
# ==============================================================================

# 记录当前还按着哪些键，脚本异常退出时用来「松开所有键」，
# 避免 W 键一直按着，人物在游戏里自己一直跑
_held_keys = set()


def key_down(key: str) -> None:
    """等价于 KeyDown key, 1：按住某个键不放。"""
    pydirectinput.keyDown(key)
    _held_keys.add(key)


def key_up(key: str) -> None:
    """等价于 KeyUp key, 1：松开某个键。"""
    pydirectinput.keyUp(key)
    _held_keys.discard(key)


def key_press(key: str) -> None:
    """等价于 KeyPress key, 1：按一下然后松开（比如 F 跳伞、M 开地图）。"""
    pydirectinput.press(key)


def move_to(x: int, y: int) -> None:
    """等价于 MoveTo x, y：把鼠标移动到屏幕绝对坐标 (x, y)。"""
    pydirectinput.moveTo(x, y)


# ------------------------------------------------------------------------------
# 为什么 move_rs 不直接用 pydirectinput.moveRel？
#
# 实测（2026-11-26，本机 2560x1440 屏幕 + 1920x1080 窗口）：
#    pydirectinput.moveRel(200, 0)  -> 鼠标绝对坐标变了，但画面差异只有 0.8，视角没动
#    SendInput(相对移动 150, 0)      -> 画面差异 24.5，视角正常转动
#
# 原因：PUBG 用 Raw Input 读鼠标，只认「相对位移」；
#       pydirectinput 的 moveRel 内部是先算绝对坐标再发「绝对移动」事件，
#       这种事件游戏直接忽略（只能挪动系统光标，不能转视角）。
# 所以这里用 Windows 的 SendInput 发一个真正的相对移动事件（MOUSEEVENTF_MOVE）。
# ------------------------------------------------------------------------------

MOUSEEVENTF_MOVE = 0x0001  # 相对移动标志（不要带 MOUSEEVENTF_ABSOLUTE）


class _MOUSEINPUT(ctypes.Structure):
    """Windows SendInput 用的鼠标事件结构体（见 MSDN MOUSEINPUT）。"""
    _fields_ = [
        ("dx", wintypes.LONG),            # 相对位移 X（正数向右）
        ("dy", wintypes.LONG),            # 相对位移 Y（正数向下）
        ("mouseData", wintypes.DWORD),    # 滚轮数据，这里不用
        ("dwFlags", wintypes.DWORD),      # 事件类型，这里是 MOUSEEVENTF_MOVE
        ("time", wintypes.DWORD),         # 时间戳，0 = 系统自动填
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),  # 附加信息，不用
    ]


class _INPUT(ctypes.Structure):
    """SendInput 的通用输入结构体，这里只用鼠标那一路。"""
    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),  # 0 = 鼠标事件
        ("u", _UNION),
    ]


def move_rs(dx: int, dy: int) -> None:
    """
    等价于 MoveRS dx, dy：鼠标相对移动。

    在游戏里就是「转视角」——原脚本用它把人转向黄标。
    必须走 SendInput 的相对移动，绝对不能改成移动光标到某个坐标，
    否则游戏不认（原因见上面那段说明）。
    """
    event = _INPUT(
        type=0,
        u=_INPUT._UNION(
            mi=_MOUSEINPUT(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if sent != 1:
        # 正常情况不会失败；失败通常是权限不足（脚本没用管理员身份运行）
        log("警告：转视角事件发送失败（可能不是管理员权限）")


def left_down() -> None:
    """等价于 LeftDown 1：按下鼠标左键（不松）。"""
    pydirectinput.mouseDown(button="left")


def left_up() -> None:
    """等价于 LeftUp 1：松开鼠标左键。"""
    pydirectinput.mouseUp(button="left")


def left_click() -> None:
    """等价于 LeftClick 1：左键点一下。"""
    pydirectinput.mouseDown(button="left")
    pydirectinput.mouseUp(button="left")


def right_click() -> None:
    """等价于 RightClick 1：右键点一下（地图上标记点位用的就是它）。"""
    pydirectinput.mouseDown(button="right")
    pydirectinput.mouseUp(button="right")


def move_and_click(x: int, y: int) -> None:
    """
    等价于原脚本的 Sub moveAndClick(x, y)。

    原脚本节奏是：等 200ms -> 移动鼠标 -> 等 200ms -> 点击 -> 等 200ms。
    这里的三个 200 是硬编码的（原脚本里 t = 200），保证点击节奏一致，
    太快的话游戏 UI 有时会吃不到点击。
    """
    delay(200)
    move_to(x, y)
    delay(200)
    left_click()
    delay(200)


# ==============================================================================
# 四、窗口处理（对应 Plugin.Window.Find / Plugin.Window.Move）
# ==============================================================================

def enable_dpi_awareness() -> None:
    """
    开启 DPI 感知。

    为什么需要：如果 Windows 缩放是 125%，不开启的话脚本拿到的是「放大后的
    逻辑坐标」，截屏和鼠标坐标会整体错位，取色全错。
    这段必须在任何坐标操作之前调用。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 2 = 按显示器 DPI 感知
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def 找游戏窗口() -> int:
    """
    等价于 Plugin.Window.Find("UnrealWindow", "PUBG：绝地求生 ")。

    找法：遍历所有可见窗口，类名是 UnrealWindow 且标题包含 "PUBG"。
    找到返回窗口句柄 hwnd，找不到抛异常。
    """
    found = []

    def _cb(hwnd, _extra):
        # 只看可见窗口，最小化/隐藏的跳过
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetClassName(hwnd) != WINDOW_CLASS:
            return True
        title = win32gui.GetWindowText(hwnd)
        if WINDOW_TITLE_KEYWORD.lower() in title.lower():
            found.append(hwnd)
            return False  # 返回 False 表示「停止遍历」
        return True

    win32gui.EnumWindows(_cb, None)

    if not found:
        raise RuntimeError(
            "找不到 PUBG 窗口。请确认：游戏已启动、处于窗口化模式、"
            "并且脚本和游戏是同样的管理员权限。"
        )
    return found[0]


def 调整窗口() -> None:
    """
    等价于原脚本的 Sub 调整窗口()：
        Hwnd = Plugin.Window.Find(...)
        Call Plugin.Window.Move(Hwnd, 0, 0)

    把游戏窗口挪到屏幕左上角 (0, 0)。
    注意：只移动、不改变大小 —— 所以游戏必须先设置成 1920x1080 窗口化。
    """
    hwnd = 找游戏窗口()
    log(f"找到游戏窗口 hwnd=0x{hwnd:X} 标题={win32gui.GetWindowText(hwnd)!r}")

    # SetWindowPos 的参数：(句柄, 置顶顺序, x, y, 宽, 高, 标志)
    # SWP_NOSIZE = 不改大小；SWP_SHOWWINDOW = 顺便显示出来
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOP,
        0, 0, 0, 0,
        win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
    )

    # 原脚本没有这一步，但窗口不在前台时，按键和点击会发到别的程序上，
    # 所以这里强制把游戏切到前台（失败也不影响，继续跑）
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:
        log(f"提示：切前台失败（{exc}），请手动点一下游戏窗口")

    delay(500)  # 给系统一点时间完成窗口移动


# ==============================================================================
# 五、状态判断（对应原脚本的三个 Function）
# ==============================================================================

def 判断是否进入游戏() -> bool:
    """
    原脚本：循环 300 次，每次判断 (969, 57) 这个点是不是 F2F3F2。
    这个点是人物头顶的箭头，说明已经出生在等待上飞机的场景里了。

    返回 True  -> 已经进入游戏（stats 1）
    返回 False -> 30 秒内一直没检测到（原脚本也只是打印一行分隔线，然后继续）
    """
    for _ in range(300):
        if color_matches(get_pixel_color(969, 57), COLOR_ENTERED_GAME):
            log("已进入游戏，等待上飞机")
            return True
        delay(1000)
    return False


def 判断是否可跳伞() -> bool:
    """
    原脚本：循环 200 次，每次判断两个点：
        (1096, 661) 是 E5E5E5  且  (1146, 663) 是 F2F2F2
    这两个点都在「跳伞」按钮的文字上，两个都对上才说明飞机到点了。

    返回 True  -> 可以跳伞了（stats 2）
    返回 False -> 大约 200 秒都没等到（原脚本打印分隔线后继续往下走）
    """
    for _ in range(200):
        c1 = get_pixel_color(1096, 661)
        c2 = get_pixel_color(1146, 663)
        if color_matches(c1, COLOR_PLANE_READY_1) and color_matches(c2, COLOR_PLANE_READY_2):
            log("可以跳伞了")
            return True
        delay(1000)
    return False


def 判断是否死亡() -> bool:
    """
    原脚本：只判断一次（不含循环），两个点同时是 00C8F8 就算死亡：
        (113, 182) 和 (373, 195)
    这两处是死亡后结算界面上的青色文字/按钮。

    注意：和原脚本一样，这个函数内部不等待，
    等待是由主循环里的 Delay 1000 提供的。
    """
    c1 = get_pixel_color(113, 182)
    c2 = get_pixel_color(373, 195)
    if color_matches(c1, COLOR_DEAD_1) and color_matches(c2, COLOR_DEAD_2):
        log("死掉了...")
        return True
    return False


# ==============================================================================
# 六、具体动作（对应原脚本的各个 Sub）
# ==============================================================================

def 开始游戏() -> None:
    """
    等价于原脚本的 Sub 开始游戏()：
    在大厅里依次点击「游戏模式 -> ... -> 泰戈 -> ... -> 开始游戏」。

    坐标顺序和原脚本完全一致，想换地图就改 LOBBY_CLICKS。
    """
    for x, y in LOBBY_CLICKS:
        move_and_click(x, y)


def 打开地图并标记点位() -> None:
    """
    等价于原脚本的 Sub 打开地图，标记点位()：
        M 开地图 -> 等 1015ms -> 鼠标移到 (1205, 706) -> 右键标记
        -> 等 141ms -> 再按 M 关地图

    标记出来的黄标，就是后面跳伞时要对准的方向。
    """
    key_press("m")
    delay(1015)

    move_to(*MAP_MARK_POINT)
    delay(106)
    right_click()
    delay(141)

    key_press("m")
    delay(235)


def 调整朝向黄标() -> None:
    """
    等价于原脚本的 Sub 调整朝向黄标()。

    原理：屏幕顶部有一条方向指示条，黄标（11E5E9）出现在哪个 x 位置，
    就说明黄标在人物的左/右/正前方：
        检测区 MARKER_SEARCH_BOX = (560, 37) ~ (1362, 81)
        x < 969  -> 黄标在左边 -> 向左转（鼠标左移 10 像素）
        x > 971  -> 黄标在右边 -> 向右转（鼠标右移 10 像素）
        969 <= x <= 971 -> 已经对准 -> 结束

    最多转 1000 次（每次间隔 10ms），转不到也不再死循环，直接返回
    （原脚本就是这样：For 1000 结束后自然退出 Sub）。
    """
    for _ in range(1000):
        delay(10)

        pos = find_color(*MARKER_SEARCH_BOX, COLOR_MAP_MARKER)

        # 这一帧没找到黄标（比如被 UI 挡住 / 游戏还没刷新），再等下一轮
        if pos is None:
            continue

        x, _y = pos

        if x < MARKER_CENTER_X - 1:
            # 黄标在左侧，向左转
            move_rs(-10, 0)
        elif x > MARKER_CENTER_X + 1:
            # 黄标在右侧，向右转
            move_rs(10, 0)
        else:
            # 969 ~ 971，已经对准
            log("转向完成")
            return

    log("警告：转了 1000 次还没对准黄标，继续往下执行（和原脚本一致）")


def 去黄标() -> None:
    """
    等价于原脚本的 Sub 去黄标()：
        先对准黄标 -> 按一下 "=" 键 -> 等 200ms

    原脚本这里的 TODO：只是简单按了一下键，没有判断是否真的走到了黄标。
    所以这里也保持原样，不额外加逻辑。
    """
    调整朝向黄标()
    key_press("=")
    delay(200)


def 捡东西() -> None:
    """
    等价于原脚本的 Sub 捡东西()。

    ！！注意：原脚本定义了它，但主流程里并没有调用（还没做完）。
    这里也保持「定义但不调用」，等你要做自动捡东西时再接入主流程。

    动作解析：
        按住 W 往前走 269ms -> 开背包 Tab -> 循环 20 次「从 (257,173) 拖到 (514,212)」
        -> 松开 W -> 关背包 Tab
    """
    key_down("w")
    delay(269)

    key_press("tab")

    for _ in range(20):
        delay(82)
        move_to(257, 173)
        delay(29)
        left_down()
        delay(18)
        move_to(514, 212)
        delay(24)
        left_up()
        delay(100)

    key_up("w")
    delay(842)

    key_press("tab")
    delay(205)


# ==============================================================================
# 七、主流程（和原脚本从上到下逐行对应）
# ==============================================================================

def 主流程() -> None:
    """
    对应原脚本从 TracePrint("begin......") 到 TracePrint("一个轮回完成") 的全部内容。

    流程：
        大厅 -> 等待进入游戏 -> 等待可跳伞 -> 标记点位 -> 跳伞 -> 落地
        -> 循环判断死亡 -> 死亡后点掉结算界面 -> 回大厅
    """

    # 全局状态：0-大厅，1-等待上飞机，2-飞机上，3-游戏中
    # （原脚本用 Global stats，Python 里直接用局部变量即可）
    stats = 0

    log("begin......")

    delay(1000)
    调整窗口()
    delay(1000)
    开始游戏()

    # 原脚本：Delay 20000，然后判断是否进入游戏
    delay(20000)
    if 判断是否进入游戏():
        stats = 1
    else:
        log("===========================")

    # 原脚本：Delay 30000，然后判断是否可跳伞
    delay(30000)
    if 判断是否可跳伞():
        stats = 2

        # ---- 第一步：地图上标记点位 ----
        打开地图并标记点位()
        delay(3000)
        delay(100)
        调整朝向黄标()      # 把视角转向刚才标记的黄标
        # 原脚本 TODO：最好等人物离标点近一点再跳，这里没做

        # ---- 第二步：跳伞 ----
        key_press("f")     # 跳伞（离开飞机）
        delay(753)
        key_down("w")      # 按住 W 往前飞
        delay(21381)       # 自由落体 / 平飞
        key_down("shift")  # 按住 Shift 加速垂直下落
        delay(12660)
        key_up("shift")
        delay(1136)
        key_up("w")
        delay(3812)
        log("跳伞完成")

        stats = 3

        # ---- 第三步：落地后往黄标方向走 ----
        delay(1000)
        去黄标()
    else:
        log("==================================")

    # ---- 第四步：游戏中的主循环，只做「判断死亡」----
    while stats == 3:
        if 判断是否死亡():
            # 已经死亡：点掉结算界面，退回大厅
            delay(1000)
            move_and_click(DEATH_CLICKS[0])
            move_and_click(DEATH_CLICKS[1])
            delay(25000)
            move_and_click(DEATH_CLICKS[2])
            delay(5000)
            move_and_click(DEATH_CLICKS[3])
            delay(3000)
            stats = 0
        else:
            # 游戏中
            # 原脚本 TODO：待实现自动进圈、自动捡东西，否则可能被判定挂机
            delay(1000)

    log("一个轮回完成。。。。。。。。。。。。")


# ==============================================================================
# 八、入口
# ==============================================================================

def main() -> None:
    # 调试模式：只打印一个点的颜色，不跑流程
    # 用法：python pubg_bot.py --color 969 57
    if len(sys.argv) >= 4 and sys.argv[1] == "--color":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        enable_dpi_awareness()
        print(f"{x},{y} -> {get_pixel_color(x, y)}")
        return

    # 正常模式：先把 DPI 感知开起来，再做任何坐标相关的事
    enable_dpi_awareness()

    # 注册全局停止热键（F12），这样不用切回命令行窗口就能停
    install_stop_hotkey()

    try:
        while True:
            主流程()
            if not RUN_FOREVER:
                break
    except StopRequested:
        log(f"已停止（你按了 {STOP_HOTKEY.upper()}）")
    except KeyboardInterrupt:
        log("已手动停止（Ctrl+C）")
    finally:
        # 保险措施：把还按着的键全部松开。
        # 否则脚本中途退出时 W / Shift 会一直处于按下状态。
        for key in list(_held_keys):
            try:
                pydirectinput.keyUp(key)
            except Exception:
                pass
            _held_keys.discard(key)


if __name__ == "__main__":
    main()
