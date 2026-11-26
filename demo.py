# -*- coding: utf-8 -*-
"""
demo.py —— 单功能调试工具（不用每次都从头跑一遍主流程）
================================================================================

所有命令都是独立的，跑完就退出，不会影响游戏里的其他状态。

用法（在本目录下执行）：
    .\\.venv\\Scripts\\python.exe demo.py              查看所有命令
    .\\.venv\\Scripts\\python.exe demo.py state        一次看全：窗口、关键点颜色、判断结果
    .\\.venv\\Scripts\\python.exe demo.py color 969 57  取某个点的颜色（RGB）
    .\\.venv\\Scripts\\python.exe demo.py marker       只看顶部方向条里有没有黄标
    .\\.venv\\Scripts\\python.exe demo.py move 300     转视角 300 像素，并自动判断有没有转
    .\\.venv\\Scripts\\python.exe demo.py key m        按一下某个键
    .\\.venv\\Scripts\\python.exe demo.py keydown w    按住某个键
    .\\.venv\\Scripts\\python.exe demo.py keyup w      松开某个键
    .\\.venv\\Scripts\\python.exe demo.py click 254 835   左键点击
    .\\.venv\\Scripts\\python.exe demo.py rightclick 1205 706  右键点击
    .\\.venv\\Scripts\\python.exe demo.py window       找窗口并移到 (0,0)
    .\\.venv\\Scripts\\python.exe demo.py mark         只做「打开地图 + 标记点位」
    .\\.venv\\Scripts\\python.exe demo.py aim          只做「转向黄标」
    .\\.venv\\Scripts\\python.exe demo.py shot 名字    存一张当前游戏截图到 logs/

注意：move / key / click / mark / aim 会真的操作游戏，跑之前先确认游戏在前台。
"""

import sys
import time

import cv2
import numpy as np

import pubg_bot as bot


# ------------------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------------------

def _client():
    """返回游戏客户区在屏幕上的位置和大小 (left, top, width, height)。"""
    hwnd = bot.找游戏窗口()
    left, top = bot.win32gui.ClientToScreen(hwnd, (0, 0))
    width, height = bot.win32gui.GetClientRect(hwnd)[2:]
    return left, top, width, height


def _grab(left, top, width, height):
    """抓一块屏幕，返回 (高, 宽, 3) 的 RGB 数组。"""
    shot = bot._get_screen().grab({"left": left, "top": top, "width": width, "height": height})
    img = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(height, width, 4)
    return img[:, :, [2, 1, 0]]  # BGRA -> RGB


def _diff(a, b):
    """两张图的平均差异（0=完全一样，越大变化越大）。"""
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def _usage():
    print(__doc__)


# ------------------------------------------------------------------------------
# 各个调试命令
# ------------------------------------------------------------------------------

def cmd_state():
    """一次性打印所有关键信息，排查问题先跑这个。"""
    left, top, width, height = _client()
    hwnd = bot.找游戏窗口()
    print(f"窗口句柄   : 0x{hwnd:X}")
    print(f"窗口标题   : {bot.win32gui.GetWindowText(hwnd)!r}")
    print(f"客户区左上 : ({left}, {top})")
    print(f"客户区大小 : {width}x{height}")
    print(f"是否前台   : {bot.win32gui.GetForegroundWindow() == hwnd}")
    print(f"颜色容差   : {bot.COLOR_TOLERANCE}")

    print("\n关键判定点：")
    checks = [
        ("进入游戏(969,57) 期望 " + bot.COLOR_ENTERED_GAME, 969, 57, bot.COLOR_ENTERED_GAME),
        ("可跳伞1(1096,661) 期望 " + bot.COLOR_PLANE_READY_1, 1096, 661, bot.COLOR_PLANE_READY_1),
        ("可跳伞2(1146,663) 期望 " + bot.COLOR_PLANE_READY_2, 1146, 663, bot.COLOR_PLANE_READY_2),
        ("死亡1(113,182) 期望 " + bot.COLOR_DEAD_1, 113, 182, bot.COLOR_DEAD_1),
        ("死亡2(373,195) 期望 " + bot.COLOR_DEAD_2, 373, 195, bot.COLOR_DEAD_2),
    ]
    for name, x, y, want in checks:
        got = bot.get_pixel_color(x, y)
        ok = "✓ 匹配" if bot.color_matches(got, want) else "✗ 不匹配"
        print(f"  {name:<32} -> 实际 {got}  {ok}")

    pos = bot.find_color(*bot.MARKER_SEARCH_BOX, bot.COLOR_MAP_MARKER)
    print(f"\n黄标检测区 {bot.MARKER_SEARCH_BOX} 内找 {bot.COLOR_MAP_MARKER}: {pos}")
    print("  （None = 没找到。地图上没标记是正常的；标记了还 None 就是颜色/区域不对）")


def cmd_color(x, y):
    color = bot.get_pixel_color(x, y)
    print(f"({x}, {y}) -> RGB {color}")


def cmd_findcolor(x1, y1, x2, y2, color):
    pos = bot.find_color(x1, y1, x2, y2, color)
    print(f"在 ({x1},{y1})-({x2},{y2}) 里找 {color}: {pos}")


def cmd_marker():
    """只检查顶部方向条里有没有黄标（不转视角）。"""
    pos = bot.find_color(*bot.MARKER_SEARCH_BOX, bot.COLOR_MAP_MARKER)
    print(f"检测区 {bot.MARKER_SEARCH_BOX}，目标色 {bot.COLOR_MAP_MARKER}")
    if pos is None:
        print("结果: 没找到黄标")
        print("  - 如果你还没在地图上标记点位，这是正常的（先跑 demo.py mark）")
        print("  - 如果已经标记了，说明颜色不对：跑 demo.py shot 看看，或换颜色再试")
        return
    x, y = pos
    print(f"结果: 找到于 ({x}, {y})")
    if x < bot.MARKER_CENTER_X - 1:
        print("  -> 黄标在左边，aim 会向左转")
    elif x > bot.MARKER_CENTER_X + 1:
        print("  -> 黄标在右边，aim 会向右转")
    else:
        print("  -> 已经对准，aim 不会转")


def cmd_move(dx, dy=0):
    """
    测试转视角：转完自动对比前后画面，告诉你到底有没有转。
    这是排查「move_rs 没反应」最直接的方法。
    """
    left, top, width, height = _client()
    # 取客户区中间一块画面做对比（避开四周 UI）
    box = (left + 300, top + 300, min(600, width - 600), min(400, height - 600))

    before = _grab(*box)
    t0 = time.time()
    bot.move_rs(dx, dy)
    time.sleep(0.35)
    after = _grab(*box)

    d = _diff(before, after)
    print(f"已发送相对移动 dx={dx} dy={dy}，耗时 {(time.time() - t0) * 1000:.0f}ms")
    print(f"画面前后差异 = {d:.2f}")
    if d > 5:
        print("结果: ✓ 视角转动了")
    else:
        print("结果: ✗ 画面几乎没变，视角没转")
        print("  排查顺序：")
        print("   1) 游戏是否在对局里（大厅/地图界面转不了视角）")
        print("   2) demo.py state 看「是否前台」，必须是 True")
        print("   3) 游戏和脚本是否同样的管理员权限")


def cmd_key(name):
    bot.key_press(name)
    print(f"已按下并松开: {name}")


def cmd_keydown(name):
    bot.key_down(name)
    print(f"已按住: {name}（别忘了 demo.py keyup {name}）")


def cmd_keyup(name):
    bot.key_up(name)
    print(f"已松开: {name}")


def cmd_click(x, y):
    bot.move_and_click(x, y)
    print(f"已左键点击 ({x}, {y})")


def cmd_rightclick(x, y):
    bot.move_to(x, y)
    bot.delay(200)
    bot.right_click()
    print(f"已右键点击 ({x}, {y})")


def cmd_window():
    bot.调整窗口()
    left, top, width, height = _client()
    print(f"客户区现在位于 ({left}, {top})，大小 {width}x{height}")
    print("坐标是按客户区左上角=(8,31)标定的，如果这里是别的值，所有坐标都会整体偏移")


def cmd_mark():
    """只跑「打开地图 + 右键标记点位」。"""
    bot.打开地图并标记点位()
    print("已在地图上标记 (1205, 706)，可以用 demo.py marker 看黄标有没有出现")


def cmd_aim():
    """只跑「转向黄标」。"""
    bot.调整朝向黄标()
    print("调整朝向黄标 结束（打印了“转向完成”说明对准了）")


def cmd_aim_test():
    """
    转向黄标 + 前后画面差异，用来自动判断到底转没转。
    """
    left, top, width, height = _client()
    box = (left + 300, top + 300, min(600, width - 600), min(400, height - 600))
    before = _grab(*box)
    bot.调整朝向黄标()
    time.sleep(0.2)
    after = _grab(*box)
    d = _diff(before, after)
    print(f"画面前后差异 = {d:.2f}")
    print("结果: " + ("✓ 视角有变化" if d > 3 else "✗ 视角没变化（可能已经对准，或没找到黄标）"))


def cmd_shot(name="shot"):
    left, top, width, height = _client()
    img = _grab(left, top, width, height)
    path = f"logs/demo_{name}.png"
    cv2.imwrite(path, img[:, :, ::-1])  # cv2 要 BGR
    print(f"已保存截图: {path}")


# ------------------------------------------------------------------------------
# 命令分发
# ------------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    if not argv:
        _usage()
        return

    verb = argv[0].lower()

    # 只有真正操作屏幕/游戏时才需要 DPI 感知
    bot.enable_dpi_awareness()

    if verb == "state" and len(argv) == 1:
        cmd_state()
    elif verb == "color" and len(argv) == 3:
        cmd_color(int(argv[1]), int(argv[2]))
    elif verb == "findcolor" and len(argv) == 6:
        cmd_findcolor(int(argv[1]), int(argv[2]), int(argv[3]), int(argv[4]), argv[5].upper())
    elif verb == "marker" and len(argv) == 1:
        cmd_marker()
    elif verb == "move" and len(argv) in (2, 3):
        cmd_move(int(argv[1]), int(argv[2]) if len(argv) == 3 else 0)
    elif verb == "key" and len(argv) == 2:
        cmd_key(argv[1].lower())
    elif verb == "keydown" and len(argv) == 2:
        cmd_keydown(argv[1].lower())
    elif verb == "keyup" and len(argv) == 2:
        cmd_keyup(argv[1].lower())
    elif verb == "click" and len(argv) == 3:
        cmd_click(int(argv[1]), int(argv[2]))
    elif verb == "rightclick" and len(argv) == 3:
        cmd_rightclick(int(argv[1]), int(argv[2]))
    elif verb == "window" and len(argv) == 1:
        cmd_window()
    elif verb == "mark" and len(argv) == 1:
        cmd_mark()
    elif verb == "aim" and len(argv) == 1:
        cmd_aim()
    elif verb == "aimtest" and len(argv) == 1:
        cmd_aim_test()
    elif verb == "shot" and len(argv) == 2:
        cmd_shot(argv[1])
    else:
        print("参数不对。\n")
        _usage()


if __name__ == "__main__":
    main()
