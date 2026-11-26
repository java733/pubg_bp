"""PUBG PC 纯视觉挂机脚本。

分层：capture -> vision -> fsm -> action，safety 横切。
不允许在导入期访问游戏窗口或截屏，所有副作用都在 main() 里发生。
"""

__version__ = "0.1.0"
