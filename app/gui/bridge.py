"""引擎线程 → Qt 主线程桥（跨线程信号自动排队）。"""
from PySide6.QtCore import QObject, Signal


class EngineBridge(QObject):
    positionChanged = Signal(float)   # 媒体秒
    stateChanged = Signal(str)        # playing/paused/ended/stopped
    errorOccurred = Signal(str)
