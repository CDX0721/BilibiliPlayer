"""主窗口：歌单侧栏 / 曲目表 / 播放控制 / 每曲音效面板。"""
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSlider, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QAbstractItemView, QSizePolicy)

from .. import config, db
from .bridge import EngineBridge

log = logging.getLogger(__name__)

MODES = [("顺序播放", "sequential"), ("循环播放", "loop"), ("随机播放", "shuffle")]
SPEEDS = ["0.5", "0.75", "1.0", "1.25", "1.5", "2.0"]
EQ_PRESETS = {
    "自定义": None,
    "平直": [0] * 10,
    "流行": [4, 3, 1, -1, -2, 0, 2, 4, 5, 4],
    "摇滚": [5, 4, 2, -2, -3, 0, 3, 5, 5, 4],
    "古典": [3, 2, 0, 0, 0, 0, -1, -1, 2, 3],
    "人声": [-3, -2, 0, 3, 5, 5, 3, 1, 0, -1],
    "低音增强": [8, 6, 4, 2, 0, 0, 0, 0, 0, 0],
}


def fmt_t(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


class MainWindow(QMainWindow):
    def __init__(self, service):
        super().__init__()
        self.svc = service
        self.engine = service.engine
        self.bridge = EngineBridge()
        self.current_pid = None
        self.tracks = []
        self.cur_index = -1
        self.cur_qid = None
        self._scrubbing = False
        self._loading_quality = False

        self.setWindowTitle("BilibiliPlayer — B站音频播放器")
        self.resize(1180, 720)
        self._build_ui()
        self._wire_engine()
        self._reload_playlists()

        # 进度刷新
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(250)
        self._pos_timer.timeout.connect(self._tick)
        self._pos_timer.start()

    # ================= UI 构建 =================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- 中部：左歌单 + 右曲目表 ----
        mid = QHBoxLayout()
        root.addLayout(mid, 1)

        left = QVBoxLayout()
        self.pl_list = QListWidget()
        self.pl_list.itemDoubleClicked.connect(self._on_pl_selected)
        left.addWidget(self.pl_list, 1)
        row = QHBoxLayout()
        for text, slot in [("新建歌单", self._new_playlist), ("删除", self._del_playlist),
                           ("导入收藏夹", self._import_fav), ("刷新", self._refresh_pl)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        left.addLayout(row)
        mid.addLayout(left, 1)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "标题", "UP主", "时长"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 420)
        self.table.itemDoubleClicked.connect(self._on_track_dbl)
        mid.addWidget(self.table, 3)

        # ---- 底部：控制条 ----
        prog = QHBoxLayout()
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(48)
        self.btn_prev = QPushButton("⏮")
        self.btn_next = QPushButton("⏭")
        for b, slot in [(self.btn_prev, self._prev), (self.btn_play, self._toggle),
                        (self.btn_next, self._next)]:
            b.setFixedWidth(48)
            b.clicked.connect(slot)
            prog.addWidget(b)
        self.pos_label = QLabel("00:00")
        prog.addWidget(self.pos_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        self.slider.sliderReleased.connect(self._on_scrub_done)
        prog.addWidget(self.slider, 1)
        self.dur_label = QLabel("00:00")
        prog.addWidget(self.dur_label)

        prog.addWidget(QLabel(" 模式"))
        self.mode_box = QComboBox()
        for label, _ in MODES:
            self.mode_box.addItem(label)
        self.mode_box.setCurrentIndex(1)
        prog.addWidget(self.mode_box)

        prog.addWidget(QLabel(" 倍速"))
        self.speed_box = QComboBox()
        self.speed_box.addItems(SPEEDS)
        self.speed_box.setCurrentText("1.0")
        self.speed_box.currentTextChanged.connect(self._on_speed)
        prog.addWidget(self.speed_box)

        prog.addWidget(QLabel(" 音质"))
        self.quality_box = QComboBox()
        self.quality_box.currentIndexChanged.connect(self._on_quality)
        prog.addWidget(self.quality_box)

        prog.addWidget(QLabel(" 音量"))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(90)
        self.vol.setFixedWidth(110)
        self.vol.valueChanged.connect(lambda v: self.engine.set_master(v / 100.0))
        prog.addWidget(self.vol)
        root.addLayout(prog)

        # ---- 右侧音效面板 ----
        dock = QDockWidget("音效（当前曲目独立设置）", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        panel = QWidget()
        g = QGridLayout(panel)
        g.setVerticalSpacing(4)

        def add_slider(row, name, lo, hi, val, tip=""):
            lab = QLabel(f"{name}: {val}")
            lab.setToolTip(tip)
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(val)
            g.addWidget(lab, row, 0)
            g.addWidget(s, row, 1)
            return s, lab

        r = 0
        self.gain_s, self.gain_l = add_slider(r, "响度增益 dB", -20, 20, 0,
                                              "每曲目独立响度：正增幅/负削减"); r += 1
        self.eq_sliders = []
        self.eq_labels = []
        for i, f in enumerate([31, 62, 125, 250, 500, "1k", "2k", "4k", "8k", "16k"]):
            s, l = add_slider(r, f"EQ {f}Hz", -12, 12, 0)
            self.eq_sliders.append(s)
            self.eq_labels.append(l)
            r += 1
        self.eq_preset = QComboBox()
        self.eq_preset.addItems(EQ_PRESETS.keys())
        g.addWidget(QLabel("EQ 预设"), r, 0)
        g.addWidget(self.eq_preset, r, 1)
        self.eq_preset.currentTextChanged.connect(self._on_eq_preset)
        r += 1
        self.rev_s, self.rev_l = add_slider(r, "Reverb 湿声", 0, 100, 0); r += 1
        self.rev_room_s, self.rev_room_l = add_slider(r, "Reverb 房间", 0, 100, 50); r += 1
        self.rev_damp_s, self.rev_damp_l = add_slider(r, "Reverb 阻尼", 0, 100, 50); r += 1
        self.del_s, self.del_l = add_slider(r, "Delay 时间 ms", 20, 2000, 250); r += 1
        self.del_fb_s, self.del_fb_l = add_slider(r, "Delay 反馈 %", 0, 90, 30); r += 1
        self.del_mix_s, self.del_mix_l = add_slider(r, "Delay 湿声", 0, 100, 0); r += 1

        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_effects)
        g.addWidget(reset_btn, r, 1)
        dock.setWidget(panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        for s in [self.gain_s, *self.eq_sliders, self.rev_s, self.rev_room_s, self.rev_damp_s,
                  self.del_s, self.del_fb_s, self.del_mix_s]:
            s.setPageStep(1)
            s.sliderReleased.connect(self._on_effect_released)
            s.valueChanged.connect(self._on_effect_live)

        # ---- 状态栏 ----
        login = "已登录" if self.svc.logged_in else "未登录（仅低音质）"
        self.statusBar().showMessage(f"登录状态: {login} | 数据目录: {config.DATA_DIR}")

    # ================= 引擎桥接 =================
    def _wire_engine(self):
        self.engine.on_position = self.bridge.positionChanged.emit
        self.engine.on_state = self.bridge.stateChanged.emit
        self.engine.on_error = self.bridge.errorOccurred.emit
        self.bridge.positionChanged.connect(lambda p: None)  # 由 _tick 轮询读取
        self.bridge.stateChanged.connect(self._on_engine_state)
        self.bridge.errorOccurred.connect(lambda m: self.statusBar().showMessage(f"错误: {m}", 8000))

    def _on_engine_state(self, state):
        self.btn_play.setText("▶" if state in ("paused", "stopped", "ended") else "⏸")
        if state == "ended":
            self._next(auto=True)

    def _tick(self):
        if self._scrubbing or not self.engine.source:
            return
        pos = self.engine.position()
        if self.engine.media_dur > 0:
            self.slider.blockSignals(True)
            self.slider.setValue(int(pos / self.engine.media_dur * 1000))
            self.slider.blockSignals(False)
        self.pos_label.setText(fmt_t(pos))

    # ================= 播放控制 =================
    def _toggle(self):
        if not self.engine.source:
            self._play_index(self.cur_index if self.cur_index >= 0 else 0)
            return
        self.engine.toggle()

    def _on_scrub_done(self):
        self._scrubbing = False
        if self.engine.media_dur > 0:
            sec = self.slider.value() / 1000.0 * self.engine.media_dur
            self.engine.seek(sec)

    def _on_speed(self, text):
        try:
            self.engine.set_speed(float(text))
        except ValueError:
            pass

    def _next_index(self, auto: bool) -> int:
        n = len(self.tracks)
        if n == 0:
            return -1
        mode = MODES[self.mode_box.currentIndex()][1]
        if mode == "shuffle":
            import random
            if n == 1:
                return 0 if auto else -1
            c = random.randrange(n)
            while c == self.cur_index and n > 1:
                c = random.randrange(n)
            return c
        i = self.cur_index + 1
        if i >= n:
            return 0 if (mode == "loop" or not auto) else -1
        return i

    def _next(self, auto=False):
        i = self._next_index(auto)
        if i >= 0:
            self._play_index(i)

    def _prev(self):
        if self.cur_index > 0:
            self._play_index(self.cur_index - 1)
        elif self.tracks:
            self._play_index(len(self.tracks) - 1)

    def _play_index(self, i: int):
        if not (0 <= i < len(self.tracks)):
            return
        self.cur_index = i
        t = self.tracks[i]
        self.table.selectRow(i)
        try:
            self._loading_quality = True
            self.cur_qid = self.svc.play_track(t)
            self._fill_quality_box(t["bvid"], self.cur_qid)
        except Exception as e:
            log.exception("播放失败")
            QMessageBox.warning(self, "播放失败", str(e))
            return
        finally:
            self._loading_quality = False
        self.dur_label.setText(fmt_t(self.engine.media_dur))
        self.speed_box.setCurrentText(str(self.engine.speed))
        self.statusBar().showMessage(f"正在播放: {t.get('title', '')}")
        self._load_effects(t["id"])

    def _on_track_dbl(self, item):
        row = item.row()
        self._play_index(row)

    def _fill_quality_box(self, bvid, cur_qid):
        self.quality_box.blockSignals(True)
        self.quality_box.clear()
        for qid, label in self.svc.available_qualities(bvid):
            self.quality_box.addItem(label, qid)
            if qid == cur_qid:
                self.quality_box.setCurrentIndex(self.quality_box.count() - 1)
        self.quality_box.blockSignals(False)

    def _on_quality(self, idx):
        if self._loading_quality or idx < 0 or self.cur_index < 0:
            return
        qid = self.quality_box.itemData(idx)
        if qid:
            pos = self.engine.position()
            t = self.tracks[self.cur_index]
            try:
                self.cur_qid = self.svc.play_track(t, quality_pref=qid)
                self.engine.seek(min(pos, max(self.engine.media_dur - 1, 0)))
                db.save_settings(t["id"], quality=qid)
            except Exception as e:
                QMessageBox.warning(self, "切换音质失败", str(e))

    # ================= 歌单 =================
    def _reload_playlists(self):
        self.pl_list.clear()
        for p in db.list_playlists():
            tag = "[收藏夹] " if p["kind"] == "fav" else ""
            item = QListWidgetItem(f"{tag}{p['name']} ({self._pl_count(p['id'])})")
            item.setData(Qt.UserRole, p["id"])
            self.pl_list.addItem(item)
        if self.current_pid is None and self.pl_list.count():
            self.pl_list.setCurrentRow(0)
            self._on_pl_selected(self.pl_list.currentItem())

    def _pl_count(self, pid):
        return len(db.playlist_tracks(pid))

    def _on_pl_selected(self, item):
        pid = item.data(Qt.UserRole)
        if pid is None:
            return
        self.current_pid = pid
        self.tracks = db.playlist_tracks(pid)
        self.table.setRowCount(len(self.tracks))
        for i, t in enumerate(self.tracks):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(t.get("title") or t["bvid"]))
            self.table.setItem(i, 2, QTableWidgetItem(t.get("upper") or ""))
            self.table.setItem(i, 3, QTableWidgetItem(fmt_t(t.get("duration") or 0)))
        self.cur_index = -1

    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, "新建歌单", "歌单名：")
        if ok and name.strip():
            db.create_playlist(name.strip())
            self._reload_playlists()

    def _del_playlist(self):
        if self.current_pid is None:
            return
        if QMessageBox.question(self, "删除歌单", "确定删除当前歌单？") == QMessageBox.Yes:
            db.delete_playlist(self.current_pid)
            self.current_pid = None
            self._reload_playlists()

    def _import_fav(self):
        try:
            folders = self.svc.client.my_fav_folders()
        except Exception as e:
            QMessageBox.warning(self, "导入收藏夹失败", f"{e}\n请先通过菜单登录/放置 Cookie。")
            return
        if not folders:
            QMessageBox.information(self, "导入收藏夹", "未找到收藏夹")
            return
        names = [f"{f['title']} ({f['media_count']})" for f in folders]
        name, ok = QInputDialog.getItem(self, "导入收藏夹", "选择收藏夹：", names, 0, False)
        if not ok:
            return
        f = folders[names.index(name)]
        pid = self.svc.import_fav(f["media_id"], f["title"])
        self._reload_playlists()
        for i in range(self.pl_list.count()):
            if self.pl_list.item(i).data(Qt.UserRole) == pid:
                self.pl_list.setCurrentRow(i)
                self._on_pl_selected(self.pl_list.item(i))

    def _refresh_pl(self):
        p = next((x for x in db.list_playlists() if x["id"] == self.current_pid), None)
        if p and p["kind"] == "fav":
            try:
                self.svc.refresh_fav(p["id"], p["media_id"])
            except Exception as e:
                QMessageBox.warning(self, "刷新失败", str(e))
        self._on_pl_selected(self.pl_list.currentItem())

    # ================= 音效面板 =================
    def _load_effects(self, tid):
        s = db.get_settings(tid)
        for w, v in [(self.gain_s, s["gain_db"]), (self.rev_s, s["reverb"]["mix"] * 100),
                     (self.rev_room_s, s["reverb"]["room"] * 100),
                     (self.rev_damp_s, s["reverb"]["damp"] * 100),
                     (self.del_s, s["delay"]["time_ms"]),
                     (self.del_fb_s, s["delay"]["feedback"] * 100),
                     (self.del_mix_s, s["delay"]["mix"] * 100)]:
            w.blockSignals(True)
            w.setValue(int(round(v)))
            w.blockSignals(False)
        for i, sl in enumerate(self.eq_sliders):
            sl.blockSignals(True)
            sl.setValue(int(round(s["eq_gains"][i])))
            sl.blockSignals(False)
        self._refresh_labels()

    def _collect_effects(self):
        return dict(
            gain_db=self.gain_s.value(),
            eq_gains=[s.value() for s in self.eq_sliders],
            reverb={"mix": self.rev_s.value() / 100, "room": self.rev_room_s.value() / 100,
                    "damp": self.rev_damp_s.value() / 100},
            delay={"time_ms": self.del_s.value(), "feedback": self.del_fb_s.value() / 100,
                   "mix": self.del_mix_s.value() / 100},
        )

    def _on_effect_live(self):
        # 拖动中即时应用到引擎（不落库）
        if self.cur_index < 0:
            return
        e = self._collect_effects()
        self.engine.set_dsp(**e)
        self._refresh_labels()

    def _on_effect_released(self):
        self._on_effect_live()
        if 0 <= self.cur_index < len(self.tracks):
            db.save_settings(self.tracks[self.cur_index]["id"], **self._collect_effects())
            self.statusBar().showMessage("已保存当前曲目的音效设置", 3000)

    def _refresh_labels(self):
        self.gain_l.setText(f"响度增益 dB: {self.gain_s.value()}")
        freqs = [31, 62, 125, 250, 500, "1k", "2k", "4k", "8k", "16k"]
        for i, l in enumerate(self.eq_labels):
            l.setText(f"EQ {freqs[i]}Hz: {self.eq_sliders[i].value()}")
        self.rev_l.setText(f"Reverb 湿声: {self.rev_s.value()}%")
        self.rev_room_l.setText(f"Reverb 房间: {self.rev_room_s.value()}%")
        self.rev_damp_l.setText(f"Reverb 阻尼: {self.rev_damp_s.value()}%")
        self.del_l.setText(f"Delay 时间 ms: {self.del_s.value()}")
        self.del_fb_l.setText(f"Delay 反馈: {self.del_fb_s.value()}%")
        self.del_mix_l.setText(f"Delay 湿声: {self.del_mix_s.value()}%")

    def _on_eq_preset(self, name):
        vals = EQ_PRESETS.get(name)
        if vals is None:
            return
        for sl, v in zip(self.eq_sliders, vals):
            sl.setValue(v)
        self._on_effect_released()

    def _reset_effects(self):
        for s in [self.gain_s, *self.eq_sliders, self.rev_s, self.rev_damp_s,
                  self.del_mix_s]:
            s.setValue(0)
        self.rev_room_s.setValue(50)
        self.rev_damp_s.setValue(50)
        self.del_s.setValue(250)
        self.del_fb_s.setValue(30)
        self._on_effect_released()

    def closeEvent(self, ev):
        self.svc.shutdown()
        super().closeEvent(ev)
