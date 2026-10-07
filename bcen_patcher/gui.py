"""Window + tray app: check for game updates, build/update/roll back the English copy.

The work runs in a separate process (python -m bcen_patcher.pipeline ...) so the
window stays responsive; its PROGRESS/RESULT lines drive the progress bar.
"""
from __future__ import annotations

import json
import os
import sys

from PyQt5.QtCore import QProcess, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                             QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

from . import __version__
from .common import ROOT, W, config

TITLE = "Battle Cats EN Patcher"


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(255, 196, 0))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    f = QFont("Segoe UI", 22)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(30, 30, 30))
    p.drawText(pm.rect(), Qt.AlignCenter, "EN")
    p.end()
    return QIcon(pm)


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{TITLE} {__version__}")
        self.setWindowIcon(make_icon())
        self.resize(620, 460)
        self.proc: QProcess | None = None
        self.last_check: dict = {}
        self.quitting = False

        central = QWidget()
        lay = QVBoxLayout(central)
        grid = QGridLayout()
        self.ver = {}
        for row, (k, label) in enumerate((("jp", "Japanese game (にゃんこ大戦争)"),
                                          ("en", "English game (The Battle Cats)"),
                                          ("mod", f"English copy ({config()['app_name']})"))):
            grid.addWidget(QLabel(label), row, 0)
            self.ver[k] = QLabel("–")
            self.ver[k].setStyleSheet("font-weight: bold")
            grid.addWidget(self.ver[k], row, 1)
        lay.addLayout(grid)
        self.status = QLabel("Starting…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding: 6px; background: palette(alternate-base); border-radius: 4px")
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        self.btn_check = QPushButton("Check now")
        self.btn_update = QPushButton("Update English app")
        self.btn_rollback = QPushButton("Roll back")
        self.btn_folder = QPushButton("Open data folder")
        self.btn_update.setStyleSheet("font-weight: bold")
        for b in (self.btn_check, self.btn_update, self.btn_rollback, self.btn_folder):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.bar = QProgressBar()
        self.step = QLabel("")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.bar)
        lay.addWidget(self.step)
        lay.addWidget(self.log, 1)
        self.setCentralWidget(central)

        self.btn_check.clicked.connect(lambda: self.check(silent=False))
        self.btn_update.clicked.connect(self.update_app)
        self.btn_rollback.clicked.connect(self.rollback)
        self.btn_folder.clicked.connect(lambda: os.startfile(str(W)))

        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        for text, slot in (("Show", self.show_window), ("Check now", lambda: self.check(silent=False)),
                           ("Update English app", self.update_app)):
            a = QAction(text, menu)
            a.triggered.connect(slot)
            menu.addAction(a)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window()
                                    if reason == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip(TITLE)
        self.tray.show()

        minutes = int(config().get("check_minutes") or 0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.check(silent=True))
        if minutes:
            self.timer.start(minutes * 60_000)
        QTimer.singleShot(1500, lambda: self.check(silent=True))

    # -- window behaviour ----------------------------------------------------
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.hide()
        self.tray.showMessage(TITLE, "Still running in the tray; it checks for game updates "
                                     f"every {config().get('check_minutes')} minutes.")

    def quit(self) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            if QMessageBox.question(self, TITLE, "An update is running. Stop it and quit?") != QMessageBox.Yes:
                return
            self.proc.kill()
        self.quitting = True
        self.tray.hide()
        QApplication.quit()

    def set_busy(self, busy: bool) -> None:
        for b in (self.btn_check, self.btn_update, self.btn_rollback):
            b.setEnabled(not busy)

    # -- running the pipeline --------------------------------------------------
    def run(self, args: list[str], on_done, show_progress: bool) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            return
        self.set_busy(True)
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments(["-X", "utf8", "-m", "bcen_patcher.pipeline", *args])
        proc.setWorkingDirectory(str(ROOT))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        buf = {"text": "", "result": None}

        def read() -> None:
            buf["text"] += bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            *lines, buf["text"] = buf["text"].split("\n")
            for line in lines:
                line = line.rstrip("\r")
                if line.startswith("RESULT|"):
                    buf["result"] = json.loads(line[7:])
                elif line.startswith("PROGRESS|"):
                    _, pct, msg = line.split("|", 2)
                    if show_progress:
                        self.bar.setValue(int(pct))
                        self.step.setText(msg)
                elif line.strip() and show_progress:
                    self.log.appendPlainText(line)

        def done(*_) -> None:
            read()
            self.set_busy(False)
            on_done(buf["result"] or {"ok": False, "error": "the patcher stopped unexpectedly"})

        proc.readyReadStandardOutput.connect(read)
        proc.finished.connect(done)
        self.proc = proc
        proc.start()

    # -- actions -----------------------------------------------------------------
    def check(self, silent: bool) -> None:
        if not silent:
            self.status.setText("Checking…")

        def done(res: dict) -> None:
            self.last_check = res
            if not res.get("ok") or not res.get("ready"):
                msg = res.get("error") or res.get("message") or "not ready"
                self.status.setText(f"⚠ {msg}")
                if not silent:
                    self.tray.showMessage(TITLE, msg)
                return
            for k in ("jp", "en", "mod"):
                self.ver[k].setText(res.get(k) or "not installed")
            lines = []
            if res.get("update_needed"):
                lines.append("Update available:\n• " + "\n• ".join(res["changes"]))
                self.tray.showMessage(f"{TITLE}: update available",
                                      "\n".join(res["changes"]) + "\nOpen the patcher → Update English app")
            else:
                lines.append("✓ The English app is up to date.")
                if not silent:
                    self.tray.showMessage(TITLE, "The English app is up to date.")
            if res.get("notes"):
                lines.append("Notes:\n• " + "\n• ".join(res["notes"]))
            self.status.setText("\n".join(lines))

        self.run(["check"], done, show_progress=False)

    def update_app(self) -> None:
        self.show_window()
        changes = "\n".join(self.last_check.get("changes", [])) or "Rebuild with the latest game files."
        if QMessageBox.question(
                self, "Update English app",
                f"{changes}\n\nBuild and install the English app now?\n\n"
                "This takes about 5–15 minutes the first time, less afterwards. "
                "Don't play the English app until it's finished.") != QMessageBox.Yes:
            return
        self.log.clear()
        self.bar.setValue(0)

        def done(res: dict) -> None:
            if res.get("ok"):
                t = res.get("text", {})
                msg = (f"✓ Updated to {res.get('jp')}: {t.get('official', 0):,} English text strings, "
                       f"{res.get('images', {}).get('swapped', 0):,} English images.")
                if res.get("notes"):
                    msg += "\nNotes:\n• " + "\n• ".join(res["notes"])
                self.status.setText(msg)
                self.tray.showMessage(TITLE, msg.split("\n")[0])
                self.check(silent=True)
            else:
                self.status.setText("✗ Update failed, nothing was changed:\n" + res.get("error", "?"))
                self.tray.showMessage(TITLE, "Update failed: " + res.get("error", "?"))

        self.status.setText("Updating…")
        self.run(["update"], done, show_progress=True)

    def rollback(self) -> None:
        if QMessageBox.question(self, "Roll back", "Reinstall the previous build of the English app?") \
                != QMessageBox.Yes:
            return
        self.log.clear()

        def done(res: dict) -> None:
            self.status.setText(f"✓ Reinstalled {res.get('installed')}." if res.get("ok")
                                else "✗ Roll back failed: " + res.get("error", "?"))
            self.check(silent=True)

        self.run(["rollback"], done, show_progress=True)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = Window()
    if "--tray" not in sys.argv:
        win.show()
    return app.exec_()
