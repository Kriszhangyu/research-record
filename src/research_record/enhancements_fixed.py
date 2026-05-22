from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextCharFormat
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
    enhanced.SummaryCalendar = SummaryCalendar
    enhanced.apply()
