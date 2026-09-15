"""网易云扫码登录对话框（weapi unikey 流程）。"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .login_dialog import _qr_pixmap


class NeLoginDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.svc = service
        self.key = None
        self.setWindowTitle("扫码登录 — 网易云音乐")
        self.setFixedSize(360, 430)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignHCenter)
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.qr_label)
        tip = QLabel("打开网易云音乐 App → 扫一扫，确认登录")
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(tip)
        self.state_label = QLabel("正在获取二维码…")
        self.state_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.state_label)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._poll)
        self._new_qr()

    def _new_qr(self):
        try:
            self.key = self.svc.ne.qr_create()
        except Exception as e:
            self.state_label.setText(f"二维码获取失败: {e}")
            return
        self.qr_label.setPixmap(_qr_pixmap(
            f"https://music.163.com/login?codekey={self.key}"))
        self.state_label.setText("等待扫码…")
        self.timer.start()

    def _poll(self):
        if not self.key:
            return
        try:
            r = self.svc.ne.qr_poll(self.key)
        except Exception as e:
            self.state_label.setText(f"网络错误: {e}")
            return
        self.state_label.setText(r["message"])
        if r["code"] == 803:
            self.svc.ne_logged_in = bool(self.svc.ne.login_status())
            self.timer.stop()
            QTimer.singleShot(500, self.accept)
        elif r["code"] == 800:
            self.timer.stop()
            self._new_qr()
