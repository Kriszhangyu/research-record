from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QCalendarWidget

from . import enhancements as enhanced


class SummaryCalendar(QCalendarWidget):
    def __init__(self, store) -> None:
        super().__init__()
        self.store = store
        self.setGridVisible(True)

    def paintCell(self, painter: QPainter, rect, qdate: QDate) -> None:
        theme = self.store.theme
        selected = qdate == self.selectedDate()
        painter.save()
        if selected:
            painter.fillRect(rect, QColor("#dbeafe"))
        super().paintCell(painter, rect, qdate)
        day = qdate.toString("yyyy-MM-dd")
        minutes = self.store.study_seconds(day) // 60
        tasks = self.store.task_count(day)
        painter.setPen(QPen(QColor(theme["muted"]), 1))
        font = QFont(painter.font())
        font.setPointSize(max(7, font.pointSize() - 3))
        painter.setFont(font)
        painter.drawText(rect.adjusted(2, rect.height() // 2, -2, -2), Qt.AlignCenter, f"({minutes}分, {tasks}项)")
        painter.restore()


def apply() -> None:
    def local_login(config: dict, username: str, password: str) -> tuple[bool, str]:
        username = (username or "").strip() or config.get("last_user") or enhanced.core.USER
        conn = enhanced.core.sqlite3.connect(enhanced.core.DB_PATH)
        conn.row_factory = enhanced.core.sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, created_at, updated_at) VALUES (?, ?, ?)",
                (username, enhanced.core.now_iso(), enhanced.core.now_iso()),
            )
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        current_hash = row["password_hash"] or ""
        is_current_format = len(current_hash) == 64 and all(ch in "0123456789abcdef" for ch in current_hash.lower())
        if not password:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES ('last_user', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (username, enhanced.core.now_iso()),
            )
            conn.commit()
            conn.close()
            return True, ""
        if not current_hash or not is_current_format:
            salt = enhanced.core.secrets.token_hex(16)
            conn.execute(
                "UPDATE users SET salt = ?, password_hash = ?, updated_at = ? WHERE username = ?",
                (salt, enhanced.core.password_hash(password, salt), enhanced.core.now_iso(), username),
            )
            conn.commit()
            conn.close()
            return True, ""
        if current_hash == enhanced.core.password_hash(password, row["salt"] or ""):
            conn.commit()
            conn.close()
            return True, ""
        conn.close()
        return False, "密码不正确。密码可留空直接进入本地软件。"

    def try_login(self) -> None:
        username = self.username_input.text().strip() or self.config.get("last_user") or enhanced.core.USER
        self.username_input.setText(username)
        ok, message = enhanced.core.ensure_login_user(self.config, username, self.password_input.text())
        if not ok:
            QMessageBox.warning(self, "无法登录", message)
            return
        self.username = username
        enhanced.core.remember_login(self.config, self.username, self.password_input.text(), self.remember.isChecked())
        self.accept()

    enhanced.core.ensure_login_user = local_login
    enhanced.core.LoginDialog.try_login = try_login
    enhanced.SummaryCalendar = SummaryCalendar
    enhanced.apply()
