from __future__ import annotations

import json
import os
import hashlib
import secrets
import shutil
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(r"E:\科研记录")
DATA_DIR = APP_DIR / "数据"
IMAGE_DIR = DATA_DIR / "images"
CONFIG_PATH = APP_DIR / "config.json"
USER = "kris"


DEFAULT_THEME = {
    "window": "#fbf1e8",
    "sidebar": "#fff8f1",
    "card": "#fffdf9",
    "input": "#fff8f1",
    "text": "#2f211b",
    "muted": "#8b7468",
    "accent": "#e98768",
    "accent2": "#f4c7a5",
    "line": "#ead4c3",
    "green": "#77b98f",
    "calendar": "#fffaf5",
    "card_opacity": 72,
    "input_opacity": 78,
    "background_opacity": 32,
}

QUADRANTS = {
    "urgent_important": ("紧急重要", "#ef6f6c"),
    "urgent_not_important": ("紧急不重要", "#f3b34c"),
    "important_not_urgent": ("重要不紧急", "#68b984"),
    "not_urgent_not_important": ("不紧急不重要", "#a98be8"),
}


def today_key() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(path.suffix + f".broken_{int(time.time())}")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def rgba(hex_color: str, opacity: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {max(0, min(100, opacity)) / 100:.2f})"


def task_due_to_label(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def safe_folder_name(value: str) -> str:
    cleaned = "".join(ch for ch in value.strip() if ch not in r'\/:*?"<>|')
    return cleaned or "未分类"


class Store:
    def __init__(self, user: str = USER) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.config = read_json(CONFIG_PATH, self.default_config())
        self.config.setdefault("theme", DEFAULT_THEME.copy())
        self.config.setdefault("users", {})
        self.config.setdefault("last_user", user or USER)
        self.user = user or self.config.get("last_user", USER)
        self.config["last_user"] = self.user
        self.config["users"].setdefault(
            self.user,
            {
                "data_dir": str(DATA_DIR),
                "created_at": now_iso(),
                "background": {"type": "color", "value": DEFAULT_THEME["window"]},
            },
        )
        self.data_path = DATA_DIR / f"{self.user}_data.json"
        self.data = read_json(self.data_path, self.default_data())
        self.save()

    @staticmethod
    def default_config() -> dict:
        return {
            "last_user": USER,
            "users": {
                USER: {
                    "data_dir": str(DATA_DIR),
                    "created_at": now_iso(),
                    "background": {"type": "color", "value": DEFAULT_THEME["window"]},
                }
            },
            "theme": DEFAULT_THEME.copy(),
        }

    @staticmethod
    def default_data() -> dict:
        return {
            "study_sessions": [],
            "daily_tasks": {},
            "daily_reflections": {},
            "figure_notes": [],
            "active_session": None,
        }

    def save(self) -> None:
        write_json(CONFIG_PATH, self.config)
        write_json(self.data_path, self.data)

    @property
    def theme(self) -> dict:
        theme = DEFAULT_THEME.copy()
        theme.update(self.config.get("theme", {}))
        return theme

    def set_theme(self, theme: dict) -> None:
        self.config["theme"] = theme
        self.save()

    def tasks_for(self, day: str) -> list[dict]:
        return self.data.setdefault("daily_tasks", {}).setdefault(day, [])

    def all_today_tasks(self) -> list[dict]:
        return self.tasks_for(today_key())

    def add_task(self, title: str, quadrant: str, due: str | None = None) -> None:
        title = title.strip()
        if not title:
            return
        self.all_today_tasks().append(
            {
                "id": str(time.time_ns()),
                "title": title,
                "completed": False,
                "quadrant": quadrant,
                "due": due or now_iso(),
            }
        )
        self.save()

    def set_task_done(self, task_id: str, done: bool) -> None:
        for task in self.all_today_tasks():
            if task["id"] == task_id:
                task["completed"] = done
                break
        self.save()

    def delete_task(self, task_id: str) -> None:
        tasks = self.all_today_tasks()
        self.data["daily_tasks"][today_key()] = [t for t in tasks if t["id"] != task_id]
        self.save()

    def add_session(self, seconds: int) -> None:
        if seconds <= 0:
            return
        self.data.setdefault("study_sessions", []).append(
            {"id": str(time.time_ns()), "date": today_key(), "seconds": seconds, "created_at": now_iso()}
        )
        self.save()

    def study_seconds(self, day: str) -> int:
        return sum(s.get("seconds", 0) for s in self.data.get("study_sessions", []) if s.get("date") == day)

    def save_reflection(self, text: str) -> None:
        self.data.setdefault("daily_reflections", {})[today_key()] = text
        self.save()

    def add_figure(self, payload: dict, image_paths: list[str]) -> None:
        saved_images = []
        tags = payload.get("tags", [])
        folder = IMAGE_DIR / safe_folder_name(tags[0] if tags else "未分类")
        folder.mkdir(parents=True, exist_ok=True)
        for src in image_paths:
            p = Path(src)
            if p.exists():
                dest = folder / f"{time.time_ns()}_{p.name}"
                shutil.copy2(p, dest)
                saved_images.append({"kind": "file", "value": str(dest)})
        payload["id"] = str(time.time_ns())
        payload["created_date"] = today_key()
        payload["created_at"] = now_iso()
        payload["images"] = saved_images
        self.data.setdefault("figure_notes", []).insert(0, payload)
        self.save()


def load_config() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    return read_json(CONFIG_PATH, Store.default_config())


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()


def ensure_login_user(config: dict, username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    if not username:
        return False, "请输入账号。"
    users = config.setdefault("users", {})
    user = users.setdefault(
        username,
        {
            "data_dir": str(DATA_DIR),
            "created_at": now_iso(),
            "background": {"type": "color", "value": DEFAULT_THEME["window"]},
        },
    )
    current_hash = user.get("password_hash", "")
    is_current_format = len(current_hash) == 64 and all(ch in "0123456789abcdef" for ch in current_hash.lower())
    if "password_hash" not in user or not is_current_format:
        salt = secrets.token_hex(16)
        user["salt"] = salt
        user["password_hash"] = password_hash(password, salt)
        config["last_user"] = username
        write_json(CONFIG_PATH, config)
        return True, ""
    if user["password_hash"] == password_hash(password, user.get("salt", ""),):
        config["last_user"] = username
        write_json(CONFIG_PATH, config)
        return True, ""
    return False, "密码不正确。"


def remember_login(config: dict, username: str, password: str, remember: bool) -> None:
    if remember:
        config["saved_login"] = {"remember": True, "username": username, "password_cache": password}
    else:
        config["saved_login"] = {"remember": False, "username": username, "password_cache": ""}
    write_json(CONFIG_PATH, config)


@dataclass
class UiState:
    store: Store
    refresh: Callable[[], None]


class Card(QFrame):
    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(12)
        if title:
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            self.layout.addWidget(label)


class SideBar(QWidget):
    navigate = Signal(str)
    theme_requested = Signal()
    appearance_changed = Signal()
    logout_requested = Signal()

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.setObjectName("sidebar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)

        title = QLabel("科研记录")
        title.setObjectName("appTitle")
        account = QLabel(f"账号：{store.user}")
        account.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(account)
        layout.addSpacing(14)

        self.buttons: dict[str, QPushButton] = {}
        for key, text in [("today", "今日"), ("tasks", "任务"), ("figures", "图谱"), ("stats", "统计")]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("navButton")
            btn.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            layout.addWidget(btn)
            self.buttons[key] = btn

        layout.addItem(QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))
        for text, handler in [
            ("自定义", self.theme_requested.emit),
            ("数据位置", self.show_data_path),
            ("退出登录", self.logout_requested.emit),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            layout.addWidget(btn)

    def pick_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择背景图", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.store.config.setdefault("users", {}).setdefault(self.store.user, {})["background"] = {
                "type": "image",
                "value": path,
            }
            self.store.save()
            self.appearance_changed.emit()

    def background_opacity(self) -> None:
        dlg = SliderDialog("背景透明度", "透明度", self.store.theme.get("background_opacity", 18), self)
        if dlg.exec() == QDialog.Accepted:
            theme = self.store.theme
            theme["background_opacity"] = dlg.value()
            self.store.set_theme(theme)
            self.appearance_changed.emit()

    def interface_opacity(self) -> None:
        dlg = OpacityDialog(self.store, self)
        if dlg.exec() == QDialog.Accepted:
            self.appearance_changed.emit()

    def reset_background(self) -> None:
        self.store.config.setdefault("users", {}).setdefault(self.store.user, {})["background"] = {
            "type": "color",
            "value": self.store.theme["window"],
        }
        self.store.save()
        self.appearance_changed.emit()

    def show_data_path(self) -> None:
        QMessageBox.information(self, "数据位置", f"配置：{CONFIG_PATH}\n数据：{self.store.data_path}")

    def set_active(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setChecked(name == key)


class SliderDialog(QDialog):
    def __init__(self, title: str, label: str, value: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        self.value_label = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(f"{label}：{v}%"))
        self.value_label.setText(f"{label}：{value}%")
        ok = QPushButton("确定")
        ok.clicked.connect(self.accept)
        layout.addWidget(self.value_label)
        layout.addWidget(self.slider)
        layout.addWidget(ok)

    def value(self) -> int:
        return self.slider.value()


class LoginDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("科研记录登录")
        self.setMinimumWidth(420)
        self.config = load_config()
        self.username = ""

        layout = QVBoxLayout(self)
        title = QLabel("科研记录")
        title.setObjectName("appTitle")
        subtitle = QLabel("登录后进入你的本地科研记录空间")
        subtitle.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        saved_login = self.config.get("saved_login", {})
        saved_user = saved_login.get("username") if saved_login.get("remember") else self.config.get("last_user", USER)
        self.username_input = QLineEdit(saved_user or USER)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("首次使用可直接设置密码，也可以留空")
        if saved_login.get("remember"):
            self.password_input.setText(saved_login.get("password_cache", ""))
        self.password_input.returnPressed.connect(self.try_login)
        self.show_password_btn = QPushButton("显示")
        self.show_password_btn.setFixedWidth(72)
        self.show_password_btn.clicked.connect(self.toggle_password)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(self.password_input, 1)
        password_row.addWidget(self.show_password_btn)
        self.remember = QCheckBox("记住账号和密码")
        self.remember.setChecked(bool(saved_login.get("remember", True)))
        form.addRow("账号", self.username_input)
        form.addRow("密码", password_row)
        form.addRow("", self.remember)
        layout.addLayout(form)

        hint = QLabel("没有账号会自动创建；已有账号会校验密码。")
        hint.setObjectName("muted")
        layout.addWidget(hint)

        actions = QHBoxLayout()
        register = QPushButton("注册账号")
        login = QPushButton("进入软件")
        register.clicked.connect(self.prepare_register)
        login.clicked.connect(self.try_login)
        actions.addWidget(register)
        actions.addStretch()
        actions.addWidget(login)
        layout.addLayout(actions)

    def prepare_register(self) -> None:
        self.username_input.clear()
        self.password_input.clear()
        self.password_input.setPlaceholderText("输入新账号密码；也可以留空")
        self.username_input.setFocus()

    def toggle_password(self) -> None:
        if self.password_input.echoMode() == QLineEdit.Password:
            self.password_input.setEchoMode(QLineEdit.Normal)
            self.show_password_btn.setText("隐藏")
        else:
            self.password_input.setEchoMode(QLineEdit.Password)
            self.show_password_btn.setText("显示")

    def try_login(self) -> None:
        ok, message = ensure_login_user(self.config, self.username_input.text(), self.password_input.text())
        if not ok:
            QMessageBox.warning(self, "无法登录", message)
            return
        self.username = self.username_input.text().strip()
        remember_login(self.config, self.username, self.password_input.text(), self.remember.isChecked())
        self.accept()


class BackgroundWidget(QWidget):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.setObjectName("root")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = self.store.theme
        painter.fillRect(self.rect(), QColor(theme["window"]))
        bg = self.store.config.get("users", {}).get(self.store.user, {}).get("background", {})
        if bg.get("type") == "image":
            pix = QPixmap(bg.get("value", ""))
            if not pix.isNull():
                painter.setOpacity(max(0, min(100, int(theme.get("background_opacity", 18)))) / 100)
                scaled = pix.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.setOpacity(1)
        super().paintEvent(event)


class OpacityDialog(QDialog):
    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("透明度设置")
        self.store = store
        self.theme = store.theme.copy()
        self.resize(460, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("分别调整背景图、卡片/框、输入框透明度，让界面和背景更协调。"))
        self.slider_row(layout, "背景图透明度", "background_opacity", 0, 100)
        self.slider_row(layout, "卡片/框透明度", "card_opacity", 5, 100)
        self.slider_row(layout, "输入框透明度", "input_opacity", 5, 100)
        actions = QHBoxLayout()
        cancel = QPushButton("取消")
        ok = QPushButton("保存")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        layout.addLayout(actions)

    def slider_row(self, parent_layout: QVBoxLayout, text: str, key: str, min_value: int, max_value: int) -> None:
        row = QHBoxLayout()
        label = QLabel(f"{text}：{self.theme.get(key, 90)}%")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_value, max_value)
        slider.setValue(int(self.theme.get(key, 90)))
        slider.valueChanged.connect(lambda v, l=label, t=text, k=key: (l.setText(f"{t}：{v}%"), self.theme.__setitem__(k, v)))
        row.addWidget(label)
        row.addWidget(slider, 1)
        parent_layout.addLayout(row)

    def accept(self) -> None:
        self.store.set_theme(self.theme)
        super().accept()


class ColorPickerDialog(QDialog):
    PRESETS = [
        "#fbf1e8", "#fff8f1", "#fffdf9", "#2f211b", "#8b7468",
        "#e98663", "#f5b183", "#e1c7b4", "#77b98f", "#ef6f6c",
        "#f3b34c", "#68b984", "#a98be8", "#ffffff", "#f2f4f7",
        "#111827", "#2563eb", "#16a34a", "#dc2626", "#9333ea",
    ]

    def __init__(self, title: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.selected = value
        layout = QVBoxLayout(self)
        self.preview = QLabel(value)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(48)
        layout.addWidget(self.preview)
        grid = QGridLayout()
        for i, color in enumerate(self.PRESETS):
            btn = QPushButton("")
            btn.setFixedSize(44, 34)
            btn.setStyleSheet(f"background:{color}; border:1px solid #999;")
            btn.clicked.connect(lambda _=False, c=color: self.set_color(c))
            grid.addWidget(btn, i // 5, i % 5)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(QLabel("自定义 HEX"))
        self.hex_input = QLineEdit(value)
        self.hex_input.textChanged.connect(self.set_color)
        row.addWidget(self.hex_input)
        layout.addLayout(row)
        actions = QHBoxLayout()
        cancel = QPushButton("取消")
        ok = QPushButton("确定")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        layout.addLayout(actions)
        self.set_color(value)

    def set_color(self, value: str) -> None:
        color = QColor(value)
        if color.isValid():
            self.selected = color.name()
            self.preview.setText(self.selected)
            self.preview.setStyleSheet(f"background:{self.selected}; color:{'#fff' if color.lightness() < 110 else '#111'};")


class ThemeDialog(QDialog):
    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("颜色主题")
        self.resize(620, 520)
        self.store = store
        self.theme = store.theme.copy()
        self.labels = {
            "window": "窗口背景",
            "sidebar": "侧边栏",
            "card": "卡片/框",
            "input": "输入框",
            "calendar": "日历颜色",
            "text": "主要文字",
            "muted": "弱文字",
            "accent": "主按钮",
            "accent2": "辅助强调",
            "line": "边框线",
            "green": "完成色",
        }

        layout = QVBoxLayout(self)
        intro = QLabel("集中设置背景、颜色和透明度。")
        intro.setObjectName("muted")
        layout.addWidget(intro)

        bg_actions = QHBoxLayout()
        pick_bg = QPushButton("选择背景图")
        reset_bg = QPushButton("还原背景")
        pick_bg.clicked.connect(self.pick_background)
        reset_bg.clicked.connect(self.reset_background)
        bg_actions.addWidget(pick_bg)
        bg_actions.addWidget(reset_bg)
        bg_actions.addStretch()
        layout.addLayout(bg_actions)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.color_buttons: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(self.labels.items()):
            btn = QPushButton(label)
            btn.setMinimumHeight(38)
            btn.clicked.connect(lambda _=False, k=key: self.pick_color(k))
            self.color_buttons[key] = btn
            grid.addWidget(btn, i // 2, i % 2)
        layout.addLayout(grid)

        self.card_slider = self.slider_row(layout, "卡片/框透明度", "card_opacity", 5, 100)
        self.input_slider = self.slider_row(layout, "输入框透明度", "input_opacity", 5, 100)
        self.bg_slider = self.slider_row(layout, "背景图透明度", "background_opacity", 0, 100)

        actions = QHBoxLayout()
        reset = QPushButton("恢复默认")
        cancel = QPushButton("取消")
        ok = QPushButton("保存主题")
        reset.clicked.connect(self.reset)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addWidget(reset)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        layout.addLayout(actions)
        self.refresh_buttons()

    def slider_row(self, parent_layout: QVBoxLayout, text: str, key: str, min_value: int, max_value: int) -> QSlider:
        row = QHBoxLayout()
        label = QLabel(f"{text}：{self.theme.get(key, 90)}%")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_value, max_value)
        slider.setValue(int(self.theme.get(key, 90)))
        slider.valueChanged.connect(lambda v, l=label, t=text, k=key: (l.setText(f"{t}：{v}%"), self.theme.__setitem__(k, v)))
        row.addWidget(label)
        row.addWidget(slider, 1)
        parent_layout.addLayout(row)
        return slider

    def pick_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择背景图", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.store.config.setdefault("users", {}).setdefault(self.store.user, {})["background"] = {
                "type": "image",
                "value": path,
            }
            self.store.save()

    def reset_background(self) -> None:
        self.store.config.setdefault("users", {}).setdefault(self.store.user, {})["background"] = {
            "type": "color",
            "value": self.theme["window"],
        }
        self.store.save()

    def pick_color(self, key: str) -> None:
        dlg = ColorPickerDialog(self.labels[key], self.theme[key], self)
        if dlg.exec() == QDialog.Accepted:
            self.theme[key] = dlg.selected
            self.refresh_buttons()

    def refresh_buttons(self) -> None:
        for key, button in self.color_buttons.items():
            button.setStyleSheet(f"background:{self.theme[key]}; color:{self.theme['text']};")

    def reset(self) -> None:
        self.theme = DEFAULT_THEME.copy()
        self.card_slider.setValue(self.theme["card_opacity"])
        self.input_slider.setValue(self.theme["input_opacity"])
        self.bg_slider.setValue(self.theme["background_opacity"])
        self.refresh_buttons()

    def accept(self) -> None:
        self.store.set_theme(self.theme)
        super().accept()


class TaskRow(QWidget):
    changed = Signal()

    def __init__(self, store: Store, task: dict, compact: bool = False) -> None:
        super().__init__()
        self.store = store
        self.task = task
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        done = QPushButton("✓" if task.get("completed") else "○")
        done.setFixedWidth(38)
        done.clicked.connect(self.toggle_done)
        text = QLabel(f"{task.get('title', '')}  {task_due_to_label(task.get('due'))}")
        text.setWordWrap(True)
        text.setObjectName("muted" if task.get("completed") else "normalText")
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self.delete)
        layout.addWidget(done)
        layout.addWidget(text, 1)
        if not compact:
            layout.addWidget(delete)

    def toggle_done(self) -> None:
        self.store.set_task_done(self.task["id"], not self.task.get("completed", False))
        self.changed.emit()

    def delete(self) -> None:
        reply = QMessageBox.question(self, "删除任务", f"确定删除任务“{self.task.get('title', '')}”吗？")
        if reply == QMessageBox.Yes:
            self.store.delete_task(self.task["id"])
            self.changed.emit()


class TodayPage(QWidget):
    def __init__(self, state: UiState) -> None:
        super().__init__()
        self.state = state
        self.elapsed = 0
        self.running = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        top = QHBoxLayout()
        study = Card("学习打卡")
        self.clock = QLabel("00:00:00")
        self.clock.setObjectName("clock")
        self.total = QLabel()
        self.start_btn = QPushButton("开始学习")
        self.stop_btn = QPushButton("结束学习")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        study.layout.addWidget(self.clock)
        study.layout.addWidget(self.total)
        study.layout.addWidget(self.start_btn)
        study.layout.addWidget(self.stop_btn)
        top.addWidget(study, 1)

        tasks = Card("今日任务")
        add_row = QHBoxLayout()
        self.quick_task = QLineEdit()
        self.quick_task.setPlaceholderText("新增今日任务")
        add = QPushButton("添加")
        add.clicked.connect(self.add_task)
        add_row.addWidget(self.quick_task, 1)
        add_row.addWidget(add)
        self.today_tasks = QVBoxLayout()
        tasks.layout.addLayout(add_row)
        tasks.layout.addLayout(self.today_tasks)
        top.addWidget(tasks, 1)
        layout.addLayout(top)

        reflection = Card("每日心得")
        self.reflection = QTextEdit()
        self.reflection.setPlaceholderText("记录今天的想法、问题和下一步安排")
        save = QPushButton("保存心得")
        save.clicked.connect(lambda: (self.state.store.save_reflection(self.reflection.toPlainText()), self.state.refresh()))
        reflection.layout.addWidget(self.reflection)
        reflection.layout.addWidget(save)
        layout.addWidget(reflection, 1)
        self.refresh()

    def tick(self) -> None:
        self.elapsed += 1
        self.clock.setText(str(timedelta(seconds=self.elapsed)))

    def start(self) -> None:
        if not self.running:
            self.running = True
            self.timer.start(1000)

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.timer.stop()
            self.state.store.add_session(self.elapsed)
            self.elapsed = 0
            self.clock.setText("00:00:00")
            self.state.refresh()

    def add_task(self) -> None:
        self.state.store.add_task(self.quick_task.text(), "urgent_important")
        self.quick_task.clear()
        self.state.refresh()

    def refresh(self) -> None:
        seconds = self.state.store.study_seconds(today_key())
        self.total.setText(f"今日累计：{seconds // 60}分钟")
        self.reflection.setText(self.state.store.data.get("daily_reflections", {}).get(today_key(), ""))
        clear_layout(self.today_tasks)
        tasks = self.state.store.all_today_tasks()
        if not tasks:
            self.today_tasks.addWidget(QLabel("今天还没有任务"))
        for task in tasks:
            row = TaskRow(self.state.store, task)
            row.changed.connect(self.state.refresh)
            self.today_tasks.addWidget(row)
        self.today_tasks.addStretch()


class TasksPage(QWidget):
    def __init__(self, state: UiState) -> None:
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        form = Card("新增任务")
        row = QHBoxLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("任务内容")
        self.quadrant = QComboBox()
        for key, (label, _) in QUADRANTS.items():
            self.quadrant.addItem(label, key)
        self.due = QLineEdit(datetime.now().strftime("%Y-%m-%d %H:%M"))
        add = QPushButton("添加任务")
        add.clicked.connect(self.add_task)
        row.addWidget(self.title, 2)
        row.addWidget(self.quadrant)
        row.addWidget(self.due)
        row.addWidget(add)
        form.layout.addLayout(row)
        layout.addWidget(form)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid, 1)
        self.refresh()

    def add_task(self) -> None:
        self.state.store.add_task(self.title.text(), self.quadrant.currentData(), self.due.text())
        self.title.clear()
        self.state.refresh()

    def refresh(self) -> None:
        clear_layout(self.grid)
        by_quad = {key: [] for key in QUADRANTS}
        for task in self.state.store.all_today_tasks():
            by_quad.setdefault(task.get("quadrant", "urgent_important"), []).append(task)
        for index, (key, (label, color)) in enumerate(QUADRANTS.items()):
            card = Card(label)
            card.setStyleSheet(f"QFrame#card {{ border-top: 5px solid {color}; }}")
            for task in by_quad.get(key, []):
                row = TaskRow(self.state.store, task)
                row.changed.connect(self.state.refresh)
                card.layout.addWidget(row)
            card.layout.addStretch()
            self.grid.addWidget(card, index // 2, index % 2)


class FiguresPage(QWidget):
    def __init__(self, state: UiState) -> None:
        super().__init__()
        self.state = state
        self.image_paths: list[str] = []
        layout = QGridLayout(self)
        layout.setSpacing(12)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

        form = Card("添加图谱")
        self.title = QLineEdit()
        self.title.setPlaceholderText("论文题目")
        self.doi = QLineEdit()
        self.doi.setPlaceholderText("DOI 或链接，可不填")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("标签，用逗号分隔，例如 区位图, 方法图")
        self.body = QTextEdit()
        self.body.setPlaceholderText("备注")
        self.image_label = QLabel("已选 0 张图片")
        self.image_label.setObjectName("muted")
        pick = QPushButton("选择图片")
        pick.clicked.connect(self.pick_images)
        save = QPushButton("完成并保存，开始下一篇")
        save.clicked.connect(self.save_figure)
        clear = QPushButton("清空当前文章")
        clear.clicked.connect(self.clear_form)
        form_layout = QFormLayout()
        form_layout.addRow("论文题目", self.title)
        form_layout.addRow("DOI / Links", self.doi)
        form_layout.addRow("标签", self.tags)
        form_layout.addRow("备注", self.body)
        form.layout.addLayout(form_layout)
        form.layout.addWidget(pick)
        form.layout.addWidget(self.image_label)
        form.layout.addWidget(save)
        form.layout.addWidget(clear)

        library = Card("图谱库")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题目、标签、备注")
        self.search.textChanged.connect(self.refresh)
        tabs = QHBoxLayout()
        self.all_btn = QPushButton("全部")
        self.tag_btn = QPushButton("区位图")
        self.all_btn.setCheckable(True)
        self.tag_btn.setCheckable(True)
        self.all_btn.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.all_btn)
        group.addButton(self.tag_btn)
        self.all_btn.clicked.connect(self.refresh)
        self.tag_btn.clicked.connect(self.refresh)
        tabs.addWidget(self.all_btn)
        tabs.addWidget(self.tag_btn)
        tabs.addStretch()
        self.list = QVBoxLayout()
        library.layout.addWidget(self.search)
        library.layout.addLayout(tabs)
        library.layout.addLayout(self.list)

        layout.addWidget(form, 0, 0)
        layout.addWidget(library, 0, 1)
        self.refresh()

    def pick_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图谱图片", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        if paths:
            self.image_paths = paths
            self.image_label.setText(f"已选 {len(paths)} 张图片")

    def save_figure(self) -> None:
        if not self.title.text().strip():
            QMessageBox.warning(self, "缺少题目", "请先填写论文题目。")
            return
        title = self.title.text().strip()
        doi = self.doi.text().strip()
        tags = [t.strip() for t in self.tags.text().replace("，", ",").split(",") if t.strip()]
        payload = {
            "title": title,
            "doi": doi,
            "doi_url": doi if doi.startswith("http") else (f"https://doi.org/{doi}" if doi else ""),
            "scholar_url": f"https://scholar.google.com/scholar?q={title.replace(' ', '%20')}",
            "cnki_url": f"https://kns.cnki.net/kns8s/defaultresult/index?kw={title.replace(' ', '%20')}",
            "tags": tags,
            "body": self.body.toPlainText().strip(),
        }
        self.state.store.add_figure(payload, self.image_paths)
        self.clear_form()
        self.state.refresh()

    def clear_form(self) -> None:
        self.title.clear()
        self.doi.clear()
        self.tags.clear()
        self.body.clear()
        self.image_paths = []
        self.image_label.setText("已选 0 张图片")

    def refresh(self) -> None:
        clear_layout(self.list)
        needle = self.search.text().strip().lower()
        only_location = self.tag_btn.isChecked()
        for note in self.state.store.data.get("figure_notes", []):
            text = " ".join([note.get("title", ""), note.get("body", ""), " ".join(note.get("tags", []))]).lower()
            if needle and needle not in text:
                continue
            if only_location and "区位图" not in note.get("tags", []):
                continue
            self.list.addWidget(FigureCard(note))
        self.list.addStretch()


class FigureCard(QFrame):
    def __init__(self, note: dict) -> None:
        super().__init__()
        self.setObjectName("innerCard")
        layout = QVBoxLayout(self)
        title = QLabel(note.get("title", "未命名图谱"))
        title.setWordWrap(True)
        title.setObjectName("itemTitle")
        layout.addWidget(title)
        tags = QHBoxLayout()
        for tag in note.get("tags", []):
            label = QLabel(tag)
            label.setObjectName("tag")
            tags.addWidget(label)
        tags.addStretch()
        layout.addLayout(tags)
        images = note.get("images", [])
        if images:
            strip = QHBoxLayout()
            for img in images[:3]:
                pix = QPixmap(img.get("value", ""))
                thumb = QLabel()
                thumb.setFixedSize(150, 110)
                thumb.setAlignment(Qt.AlignCenter)
                if not pix.isNull():
                    thumb.setPixmap(pix.scaled(150, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    thumb.setText("图片缺失")
                strip.addWidget(thumb)
            strip.addStretch()
            layout.addLayout(strip)
        links = QHBoxLayout()
        for text, key in [("Scholar", "scholar_url"), ("知网", "cnki_url"), ("DOI", "doi_url")]:
            if note.get(key):
                btn = QPushButton(text)
                btn.clicked.connect(lambda _=False, url=note[key]: webbrowser.open(url))
                links.addWidget(btn)
        links.addStretch()
        layout.addLayout(links)


class TrendChart(QWidget):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.setMinimumHeight(230)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        theme = self.store.theme
        rect = self.rect().adjusted(24, 22, -24, -34)
        painter.setPen(QPen(QColor(theme["line"]), 1))
        painter.drawRect(rect)
        days = [(date.today() - timedelta(days=6 - i)).isoformat() for i in range(7)]
        values = [self.store.study_seconds(d) // 60 for d in days]
        max_value = max(values + [30])
        points = []
        for i, value in enumerate(values):
            x = rect.left() + (rect.width() * i / 6)
            y = rect.bottom() - (rect.height() * value / max_value)
            points.append((x, y, value, days[i][5:]))
        painter.setPen(QPen(QColor(theme["accent"]), 3))
        for a, b in zip(points, points[1:]):
            painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        painter.setBrush(QColor(theme["accent"]))
        for x, y, value, label in points:
            painter.drawEllipse(int(x - 4), int(y - 4), 8, 8)
            painter.drawText(int(x - 18), rect.bottom() + 22, label)
            painter.drawText(int(x - 12), int(y - 10), str(value))


class StatsPage(QWidget):
    def __init__(self, state: UiState) -> None:
        super().__init__()
        self.state = state
        self._last_selected_date: QDate | None = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        top = QGridLayout()
        top.setSpacing(12)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.selectionChanged.connect(self.refresh)
        self.calendar.currentPageChanged.connect(lambda *_: self.apply_calendar_format())
        calendar_card = Card("日历视图")
        calendar_card.layout.addWidget(self.calendar)
        top.addWidget(calendar_card, 0, 0)

        self.ratio = QLabel()
        self.ratio.setAlignment(Qt.AlignCenter)
        self.ratio.setObjectName("ratio")
        ratio_card = Card("任务完成比例")
        ratio_card.layout.addWidget(self.ratio, 1)
        top.addWidget(ratio_card, 0, 1)

        self.detail = QLabel()
        self.detail.setWordWrap(True)
        detail_card = Card("选中日期详情")
        detail_card.layout.addWidget(self.detail, 1)
        top.addWidget(detail_card, 0, 2)
        layout.addLayout(top)

        trend_card = Card("学习时长")
        self.trend = TrendChart(self.state.store)
        trend_card.layout.addWidget(self.trend)
        layout.addWidget(trend_card)
        self.refresh()

    def apply_calendar_format(self) -> None:
        theme = self.state.store.theme
        if self._last_selected_date is not None:
            self.calendar.setDateTextFormat(self._last_selected_date, QTextCharFormat())
        weekday = QTextCharFormat()
        weekday.setForeground(QBrush(QColor(theme["text"])))
        weekday.setBackground(QBrush(QColor(theme.get("calendar", theme["card"]))))
        weekend = QTextCharFormat()
        weekend.setForeground(QBrush(QColor("#e33b2f")))
        weekend.setBackground(QBrush(QColor(theme.get("calendar", theme["card"]))))
        for day in [Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday]:
            self.calendar.setWeekdayTextFormat(day, weekday)
        self.calendar.setWeekdayTextFormat(Qt.Saturday, weekend)
        self.calendar.setWeekdayTextFormat(Qt.Sunday, weekend)
        selected = self.calendar.selectedDate()
        selected_fmt = QTextCharFormat()
        selected_fmt.setForeground(QBrush(QColor("#ffffff")))
        selected_fmt.setBackground(QBrush(QColor(theme["accent"])))
        selected_fmt.setFontWeight(QFont.Bold)
        self.calendar.setDateTextFormat(selected, selected_fmt)
        self._last_selected_date = selected

    def refresh(self) -> None:
        self.apply_calendar_format()
        day = self.calendar.selectedDate().toString("yyyy-MM-dd")
        tasks = self.state.store.tasks_for(day)
        done = len([t for t in tasks if t.get("completed")])
        total = len(tasks)
        pct = int(done / total * 100) if total else 0
        self.ratio.setText(f"{pct}%\n{done}/{total}")
        figures = [f for f in self.state.store.data.get("figure_notes", []) if f.get("created_date") == day]
        minutes = self.state.store.study_seconds(day) // 60
        task_text = "\n".join([f"{'✓' if t.get('completed') else '○'} {t.get('title', '')}" for t in tasks]) or "这一天没有任务"
        figure_text = "\n".join([f.get("title", "") for f in figures]) or "这一天没有图谱图片"
        self.detail.setText(f"日期：{day}\n学习时长：{minutes}分钟\n\n任务：\n{task_text}\n\n图谱：\n{figure_text}")
        self.trend.update()


class MainWindow(QMainWindow):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle("科研记录")
        self.resize(1400, 900)
        self.setMinimumSize(980, 680)

        root = BackgroundWidget(self.store)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        self.sidebar = SideBar(self.store)
        self.sidebar.setFixedWidth(250)
        self.sidebar.navigate.connect(self.go)
        self.sidebar.theme_requested.connect(self.edit_theme)
        self.sidebar.appearance_changed.connect(self.apply_theme)
        self.sidebar.logout_requested.connect(self.logout)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        state = UiState(self.store, self.refresh_all)
        for key, page in [
            ("today", TodayPage(state)),
            ("tasks", TasksPage(state)),
            ("figures", FiguresPage(state)),
            ("stats", StatsPage(state)),
        ]:
            page.setAttribute(Qt.WA_TranslucentBackground, True)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setAttribute(Qt.WA_TranslucentBackground, True)
            scroll.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
            scroll.setWidget(page)
            self.pages[key] = page
            self.stack.addWidget(scroll)
        layout.addWidget(self.stack, 1)
        self.apply_theme()
        self.go("today")

    def go(self, key: str) -> None:
        index = list(self.pages).index(key)
        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(key)
        self.refresh_all()

    def edit_theme(self) -> None:
        dlg = ThemeDialog(self.store, self)
        if dlg.exec() == QDialog.Accepted:
            self.apply_theme()
            self.refresh_all()

    def logout(self) -> None:
        if QMessageBox.question(self, "退出登录", "确定退出当前账号并返回登录页吗？") == QMessageBox.Yes:
            QApplication.exit(100)

    def refresh_all(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                page.refresh()

    def apply_theme(self) -> None:
        t = self.store.theme
        self.setStyleSheet(
            f"""
            QWidget#root {{ background: transparent; color: {t['text']}; }}
            QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
                background: transparent;
                color: {t['text']};
            }}
            QWidget#sidebar {{
                background: {rgba(t['sidebar'], t['card_opacity'])};
                border: 1px solid {t['line']};
                border-radius: 18px;
            }}
            QFrame#card {{
                background: {rgba(t['card'], t['card_opacity'])};
                border: 1px solid {t['line']};
                border-radius: 18px;
            }}
            QFrame#innerCard {{
                background: {rgba(t['card'], max(50, t['card_opacity'] - 5))};
                border: 1px solid {t['line']};
                border-radius: 16px;
            }}
            QLabel {{ color: {t['text']}; font-size: 16px; }}
            QLabel#appTitle {{ font-size: 34px; font-weight: 800; }}
            QLabel#sectionTitle {{ font-size: 24px; font-weight: 800; }}
            QLabel#itemTitle {{ font-size: 18px; font-weight: 700; }}
            QLabel#clock {{ font-size: 48px; font-weight: 800; }}
            QLabel#ratio {{ font-size: 38px; font-weight: 800; }}
            QLabel#muted, QLabel.muted {{ color: {t['muted']}; }}
            QLabel#tag {{
                background: {t['accent2']};
                border-radius: 8px;
                padding: 5px 10px;
            }}
            QLineEdit, QTextEdit, QComboBox {{
                background: {rgba(t['input'], t['input_opacity'])};
                border: 1px solid {t['line']};
                border-radius: 14px;
                padding: 9px 12px;
                color: {t['text']};
                font-size: 16px;
            }}
            QPushButton {{
                background: {rgba(t['input'], t['input_opacity'])};
                border: 1px solid {t['line']};
                border-radius: 14px;
                padding: 9px 14px;
                color: {t['text']};
                font-size: 16px;
                font-weight: 700;
            }}
            QPushButton:hover {{ border-color: {t['accent']}; }}
            QPushButton:checked, QPushButton#navButton:checked {{
                background: {t['accent2']};
                border-color: {t['accent2']};
            }}
            QPushButton:default, QPushButton:pressed {{ background: {t['accent']}; color: white; }}
            QPushButton#dangerButton {{ color: #b33a3a; }}
            QCalendarWidget QWidget {{
                background: {rgba(t.get('calendar', t['card']), 98)};
                color: {t['text']};
            }}
            QCalendarWidget QToolButton {{
                background: {rgba(t['input'], 88)};
                color: {t['text']};
                border: 1px solid {t['line']};
                border-radius: 12px;
                margin: 2px;
                padding: 5px 10px;
            }}
            QCalendarWidget QMenu {{
                background: {rgba(t['card'], 98)};
                color: {t['text']};
            }}
            QCalendarWidget QSpinBox {{
                background: {rgba(t['input'], 98)};
                color: {t['text']};
                selection-background-color: {t['accent2']};
                selection-color: {t['text']};
            }}
            QCalendarWidget QAbstractItemView {{
                background: {rgba(t.get('calendar', t['card']), 98)};
                alternate-background-color: {rgba(t.get('calendar', t['card']), 98)};
                color: {t['text']};
                selection-background-color: {t['accent']};
                selection-color: #ffffff;
                outline: 0;
                border: 0;
            }}
            QCalendarWidget QAbstractItemView:item {{
                background: {rgba(t.get('calendar', t['card']), 98)};
                color: {t['text']};
            }}
            QCalendarWidget QAbstractItemView:disabled {{
                color: {t['muted']};
            }}
            """
        )


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def main() -> None:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 11))
    while True:
        login = LoginDialog()
        login.setStyleSheet(
            f"""
            QDialog {{ background: {DEFAULT_THEME['window']}; color: {DEFAULT_THEME['text']}; }}
            QLabel {{ color: {DEFAULT_THEME['text']}; font-size: 16px; }}
            QLabel#appTitle {{ font-size: 34px; font-weight: 800; }}
            QLabel#muted {{ color: {DEFAULT_THEME['muted']}; }}
            QLineEdit {{
                background: #fff8f1;
                border: 1px solid {DEFAULT_THEME['line']};
                border-radius: 10px;
                padding: 9px 12px;
                font-size: 16px;
            }}
            QPushButton {{
                background: {DEFAULT_THEME['accent']};
                color: white;
                border: 0;
                border-radius: 10px;
                padding: 10px 18px;
                font-size: 16px;
                font-weight: 700;
            }}
            """
        )
        if login.exec() != QDialog.Accepted:
            return
        window = MainWindow(Store(login.username))
        window.show()
        code = app.exec()
        window.deleteLater()
        if code != 100:
            sys.exit(code)
