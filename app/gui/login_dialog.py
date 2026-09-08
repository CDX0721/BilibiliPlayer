"""扫码登录对话框：显示二维码，2 秒轮询，成功后保存 Cookie。"""
import qrcode
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from ..auth import qr_login


def _qr_pixmap(text: str, scale: int = 6) -> QPixmap:
    qr = qrcode.QRCode(border=4)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    quiet = 4
    size = (n + quiet * 2) * scale
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(0xFFFFFFFF)
    black = 0xFF000000
    for y in range(n):
        for x in range(n):
            if m[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        img.setPixel((x + quiet) * scale + dx, (y + quiet) * scale + dy, black)
    return QPixmap.fromImage(img)


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.qr = qr_login.QRLogin()
        self.key = None
        self.setWindowTitle("扫码登录 — 哔哩哔哩")
        self.setFixedSize(360, 430)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignHCenter)
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.qr_label)
        tip = QLabel("打开哔哩哔哩 App → 扫一扫，确认登录")
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
            url, self.key = self.qr.generate()
        except Exception as e:
            self.state_label.setText(f"二维码获取失败: {e}")
            return
        self.qr_label.setPixmap(_qr_pixmap(url))
        self.state_label.setText("等待扫码…")
        self.timer.start()

    def _poll(self):
        if not self.key:
            return
        try:
            r = self.qr.poll(self.key)
        except Exception as e:
            self.state_label.setText(f"网络错误: {e}")
            return
        self.state_label.setText(r["message"])
        if r["code"] == qr_login.QR_OK:
            qr_login.save_login(r["cookies"])
            self.timer.stop()
            QTimer.singleShot(600, self.accept)
        elif r["code"] == qr_login.QR_EXPIRED:
            self.timer.stop()
            self._new_qr()
        # 86101 / 86090：继续轮询
