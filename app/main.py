"""入口：python -m app.main [--wav 路径] [--no-gui]"""
import argparse
import logging
import sys


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", help="输出到 WAV 文件而非扬声器（测试用）")
    args = ap.parse_args()

    from . import config
    backend = {"type": "wav", "path": args.wav} if args.wav else None

    from .service import Service
    svc = Service(engine_backend=backend)

    from PySide6.QtWidgets import QApplication
    from .gui.main_window import MainWindow
    app = QApplication(sys.argv)
    win = MainWindow(svc)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
