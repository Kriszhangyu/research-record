from __future__ import annotations

import json
import shutil
import tempfile
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCharFormat, QBrush
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from . import app as core

PRIORITIES = {
    "紧急重要": "#ef6f6c",
    "紧急不重要": "#f3b34c",
    "重要不紧急": "#68b984",
    "不紧急不重要": "#a98be8",
}
LEGACY_PRIORITY = {
    "urgent_important": "紧急重要",
    "urgent_not_important": "紧急不重要",
    "important_not_urgent": "重要不紧急",
    "not_urgent_not_important": "不紧急不重要",
}


def priority_label(value: str | None) -> str:
    value = value or "紧急重要"
    return LEGACY_PRIORITY.get(value, value if value in PRIORITIES else "紧急重要")


def readable_text_color(hex_color: str) -> str:
    return core.readable_text_color(hex_color)


def safe_folder_name(value: str) -> str:
    return core.safe_folder_name(value)


def source_url_for(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://doi.org/{value}"


def scholar_url_for(title: str) -> str:
    return f"https://scholar.google.com/scholar?q={quote_plus(title.strip())}"


def cnki_url_for(title: str) -> str:
    return f"https://kns.cnki.net/kns8s/defaultresult/index?kw={quote_plus(title.strip())}"


def crossref_doi_for_title(title: str) -> str | None:
    query = quote(title.strip())
    if not query:
        return None
    request = Request(
        f"https://api.crossref.org/works?query.title={query}&rows=1",
        headers={"User-Agent": "research-record/1.0 (mailto:research-record@example.local)"},
    )
    with urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
    items = data.get("message", {}).get("items", [])
    if not items:
        return None
    doi = (items[0].get("DOI") or "").strip()
    return doi or None


def ensure_columns(store: core.Store, table: str, additions: dict[str, str]) -> None:
    columns = {row["name"] for row in store.conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in additions.items():
        if name not in columns:
            store.conn.execute(ddl)


def ensure_schema(store: core.Store) -> None:
    store.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            priority TEXT NOT NULL,
            due_at TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            task_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            doi_or_link TEXT,
            google_scholar_url TEXT,
            cnki_url TEXT,
            source_url TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_notes (
            id TEXT PRIMARY KEY,
            note_date TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    ensure_columns(store, "image_records", {"article_id": "ALTER TABLE image_records ADD COLUMN article_id INTEGER", "image_title": "ALTER TABLE image_records ADD COLUMN image_title TEXT"})
    ensure_columns(store, "study_sessions", {"study_date": "ALTER TABLE study_sessions ADD COLUMN study_date TEXT", "minutes": "ALTER TABLE study_sessions ADD COLUMN minutes INTEGER", "started_at": "ALTER TABLE study_sessions ADD COLUMN started_at TEXT", "ended_at": "ALTER TABLE study_sessions ADD COLUMN ended_at TEXT"})
    store.conn.execute("UPDATE study_sessions SET study_date = day WHERE (study_date IS NULL OR study_date = '') AND day IS NOT NULL")
    store.conn.execute("UPDATE study_sessions SET minutes = CAST(seconds / 60 AS INTEGER) WHERE minutes IS NULL AND seconds IS NOT NULL")
    for old, new in LEGACY_PRIORITY.items():
        store.conn.execute("UPDATE tasks SET priority = ? WHERE priority = ?", (new, old))
    store.conn.execute(
        """
        INSERT OR IGNORE INTO tasks (id, content, priority, due_at, completed, task_date, created_at, updated_at)
        SELECT id, COALESCE(NULLIF(content, ''), title, ''),
               CASE COALESCE(NULLIF(priority, ''), quadrant, 'urgent_important')
                    WHEN 'urgent_important' THEN '紧急重要'
                    WHEN 'urgent_not_important' THEN '紧急不重要'
                    WHEN 'important_not_urgent' THEN '重要不紧急'
                    WHEN 'not_urgent_not_important' THEN '不紧急不重要'
                    ELSE COALESCE(NULLIF(priority, ''), quadrant, '紧急重要') END,
               COALESCE(NULLIF(due_at, ''), due), completed, day, created_at, updated_at
        FROM daily_tasks
        """
    )
    store.conn.execute("INSERT OR IGNORE INTO daily_notes (id, note_date, content, created_at, updated_at) SELECT 'note-' || day, day, body, updated_at, updated_at FROM daily_reflections")
    rows = store.conn.execute("SELECT * FROM image_records WHERE article_id IS NULL OR article_id = 0").fetchall()
    for row in rows:
        article = store.conn.execute("SELECT id FROM articles WHERE title = ? AND COALESCE(doi_or_link, '') = COALESCE(?, '') ORDER BY id LIMIT 1", (row["title"], row["doi"] or "")).fetchone()
        if article:
            article_id = article["id"]
        else:
            cursor = store.conn.execute(
                "INSERT INTO articles (title, doi_or_link, google_scholar_url, cnki_url, source_url, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["title"], row["doi"] or "", row["scholar_url"] or "", row["cnki_url"] or "", row["doi_url"] or "", "", row["created_at"], row["updated_at"]),
            )
            article_id = cursor.lastrowid
        store.conn.execute("UPDATE image_records SET article_id = ?, image_title = COALESCE(image_title, title) WHERE id = ?", (article_id, row["id"]))
    store.conn.commit()


def task_due_to_label(value: str | None) -> str:
    return core.task_due_to_label(value)


def is_task_overdue(task: dict) -> bool:
    if task.get("completed"):
        return False
    due = task.get("due_at") or task.get("due")
    if not due:
        return False
    try:
        return datetime.fromisoformat(due) < datetime.now()
    except ValueError:
        return False


class EnhancedTaskRow(QWidget):
    changed = Signal()

    def __init__(self, store: core.Store, task: dict, compact: bool = False) -> None:
        super().__init__()
        self.store = store
        self.task = task
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        done = QPushButton("✓" if task.get("completed") else "○")
        done.setFixedWidth(38)
        done.clicked.connect(self.toggle_done)
        priority = priority_label(task.get("priority") or task.get("quadrant"))
        flag = QLabel("⚑")
        flag.setFixedWidth(24)
        flag.setAlignment(Qt.AlignCenter)
        flag.setToolTip(priority)
        flag.setStyleSheet(f"color:{PRIORITIES[priority]}; font-size:18px; font-weight:800;")
        due_text = task_due_to_label(task.get("due_at") or task.get("due"))
        suffix = f"  截止：{due_text}" if due_text else ""
        overdue = is_task_overdue(task)
        if overdue:
            suffix += "  已逾期"
        text = QLabel(f"{task.get('content') or task.get('title', '')}  [{priority}]{suffix}")
        text.setWordWrap(True)
        text.setObjectName("muted" if task.get("completed") else "normalText")
        if overdue:
            text.setStyleSheet("color:#d84a3a; font-weight:700;")
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self.delete)
        layout.addWidget(done)
        layout.addWidget(flag)
        layout.addWidget(text, 1)
        if not compact:
            layout.addWidget(delete)

    def toggle_done(self) -> None:
        self.store.set_task_done(self.task["id"], not self.task.get("completed", False))
        self.changed.emit()

    def delete(self) -> None:
        title = self.task.get("content") or self.task.get("title", "")
        if QMessageBox.question(self, "删除任务", f"确定删除任务“{title}”吗？") == QMessageBox.Yes:
            self.store.delete_task(self.task["id"])
            self.changed.emit()


class EnhancedFiguresPage(QWidget):
    def __init__(self, state: core.UiState) -> None:
        super().__init__()
        self.state = state
        self.pending_images: list[dict] = []
        self.current_article_id: int | None = None
        self.active_tag: str | None = None
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        paste = QShortcut(QKeySequence.Paste, self)
        paste.setContext(Qt.WidgetWithChildrenShortcut)
        paste.activated.connect(self.paste_image)
        layout = QGridLayout(self)
        layout.setSpacing(12)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        form = core.Card("添加图谱文章")
        self.title = QLineEdit()
        self.title.setPlaceholderText("论文题目")
        self.doi = QLineEdit()
        self.doi.setPlaceholderText("DOI 或链接，可不填")
        self.article_note = QTextEdit()
        self.article_note.setMinimumHeight(88)
        self.article_note.setPlaceholderText("文章备注")
        self.image_tag = QLineEdit()
        self.image_tag.setPlaceholderText("当前图片标签，例如 框架图")
        self.image_note = QTextEdit()
        self.image_note.setMinimumHeight(92)
        self.image_note.setPlaceholderText("当前图片备注")
        self.image_label = QLabel("当前文章已添加 0 张图片")
        self.image_label.setObjectName("muted")
        form_layout = QFormLayout()
        form_layout.addRow("论文题目", self.title)
        form_layout.addRow("DOI / Links", self.doi)
        form_layout.addRow("文章备注", self.article_note)
        form_layout.addRow("图片标签", self.image_tag)
        form_layout.addRow("图片备注", self.image_note)
        form.layout.addLayout(form_layout)
        link_row = QHBoxLayout()
        search = QPushButton("检索并跳转")
        search.clicked.connect(self.search_and_open_source)
        link_row.addWidget(search)
        link_row.addStretch()
        form.layout.addLayout(link_row)
        pick = QPushButton("选择图片")
        pick.clicked.connect(self.pick_images)
        add_next = QPushButton("添加下一张图片")
        add_next.clicked.connect(self.prepare_next_image)
        finish = QPushButton("完成当前文章，开始下一篇")
        finish.clicked.connect(self.finish_article)
        clear = QPushButton("清空当前文章")
        clear.clicked.connect(self.clear_form)
        form.layout.addWidget(pick)
        form.layout.addWidget(self.image_label)
        self.pending_list = QVBoxLayout()
        form.layout.addLayout(self.pending_list)
        form.layout.addWidget(add_next)
        form.layout.addWidget(finish)
        form.layout.addWidget(clear)
        library = core.Card("图谱库")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题目、标签、图片备注、文章备注、DOI / Links")
        self.search.textChanged.connect(self.refresh)
        self.tag_filters = QHBoxLayout()
        self.list = QVBoxLayout()
        library.layout.addWidget(self.search)
        library.layout.addLayout(self.tag_filters)
        library.layout.addLayout(self.list)
        layout.addWidget(form, 0, 0)
        layout.addWidget(library, 0, 1)
        self.refresh()

    def pick_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图谱图片", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        self.add_image_paths(paths)

    def add_image_paths(self, paths: list[str]) -> None:
        tag = self.image_tag.text().strip() or "未分类"
        note = self.image_note.toPlainText().strip()
        for path in paths:
            if Path(path).exists():
                self.pending_images.append({"path": path, "tag": tag, "note": note, "title": Path(path).stem})
        self.refresh_pending_images()

    def refresh_pending_images(self) -> None:
        core.clear_layout(self.pending_list)
        self.image_label.setText(f"当前文章已添加 {len(self.pending_images)} 张图片")
        for index, item in enumerate(self.pending_images):
            row = QHBoxLayout()
            thumb = QLabel()
            thumb.setFixedSize(88, 66)
            thumb.setAlignment(Qt.AlignCenter)
            pix = QPixmap(item["path"])
            if pix.isNull():
                thumb.setText("图片缺失")
            else:
                thumb.setPixmap(pix.scaled(88, 66, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            _, color = self.state.store.get_or_create_tag(item["tag"])
            tag = QLabel(item["tag"])
            tag.setStyleSheet(f"background:{color}; color:{readable_text_color(color)}; border-radius:6px; padding:4px 8px;")
            note = QLabel(item.get("note") or "无图片备注")
            note.setObjectName("muted")
            note.setWordWrap(True)
            remove = QPushButton("移除")
            remove.clicked.connect(lambda _=False, i=index: self.remove_pending_image(i))
            row.addWidget(thumb)
            row.addWidget(tag)
            row.addWidget(note, 1)
            row.addWidget(remove)
            self.pending_list.addLayout(row)

    def remove_pending_image(self, index: int) -> None:
        if 0 <= index < len(self.pending_images):
            self.pending_images.pop(index)
            self.refresh_pending_images()

    def prepare_next_image(self) -> None:
        self.image_tag.clear()
        self.image_note.clear()
        self.image_label.setText(f"当前文章已添加 {len(self.pending_images)} 张图片，可继续选择、拖拽或粘贴下一张。")

    def finish_article(self) -> None:
        if not self.title.text().strip():
            QMessageBox.warning(self, "缺少题目", "请先填写论文题目。")
            return
        if not self.pending_images:
            QMessageBox.warning(self, "缺少图片", "请至少添加一张图片。")
            return
        doi = self.doi.text().strip()
        self.current_article_id = self.state.store.add_figure({"title": self.title.text().strip(), "doi_or_link": doi, "source_url": source_url_for(doi), "google_scholar_url": "", "cnki_url": "", "note": self.article_note.toPlainText().strip()}, self.pending_images, self.current_article_id)
        self.clear_form()
        self.state.refresh()

    def clear_form(self) -> None:
        self.title.clear()
        self.doi.clear()
        self.article_note.clear()
        self.image_tag.clear()
        self.image_note.clear()
        self.current_article_id = None
        self.pending_images = []
        self.refresh_pending_images()

    def current_article_payload(self, source_url: str = "") -> dict:
        doi = self.doi.text().strip()
        return {"title": self.title.text().strip() or "未命名图谱", "doi_or_link": doi, "source_url": source_url or source_url_for(doi), "google_scholar_url": "", "cnki_url": "", "note": self.article_note.toPlainText().strip()}

    def persist_current_article(self, source_url: str = "") -> None:
        payload = self.current_article_payload(source_url)
        if self.current_article_id:
            self.current_article_id = self.state.store.update_article(self.current_article_id, payload)
        else:
            self.current_article_id = self.state.store.add_article(payload)
        self.state.refresh()

    def search_and_open_source(self) -> None:
        raw = self.doi.text().strip()
        title = self.title.text().strip()
        if raw:
            url = source_url_for(raw)
            self.persist_current_article(url)
            webbrowser.open(url)
            return
        if not title:
            QMessageBox.information(self, "缺少 DOI / 链接", "请先输入论文题目，或手动填写 DOI / 链接")
            return
        try:
            doi = crossref_doi_for_title(title)
        except URLError:
            QMessageBox.warning(self, "网络失败", "网络连接失败，无法自动检索 DOI")
            self.persist_current_article("")
            return
        except Exception:
            QMessageBox.warning(self, "网络失败", "网络连接失败，无法自动检索 DOI")
            self.persist_current_article("")
            return
        if not doi:
            QMessageBox.information(self, "未检索到 DOI", "未检索到 DOI，请手动输入 DOI 或文章链接")
            self.persist_current_article("")
            return
        self.doi.setText(doi)
        url = source_url_for(doi)
        self.persist_current_article(url)
        webbrowser.open(url)

    def paste_image(self) -> None:
        mime = QApplication.clipboard().mimeData()
        if mime.hasImage():
            image = QApplication.clipboard().image()
            path = Path(tempfile.gettempdir()) / f"research_record_clipboard_{time.time_ns()}.png"
            image.save(str(path), "PNG")
            self.add_image_paths([str(path)])

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Paste):
            self.paste_image()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in [".png", ".jpg", ".jpeg", ".bmp"]:
                paths.append(str(path))
        self.add_image_paths(paths)

    def refresh(self) -> None:
        self.state.store.data = self.state.store.load_data()
        self.refresh_tag_filters()
        core.clear_layout(self.list)
        needle = self.search.text().strip().lower()
        for note in self.state.store.data.get("figure_notes", []):
            image_text = " ".join([f"{img.get('tag', '')} {img.get('note', '')} {img.get('title', '')}" for img in note.get("images", [])])
            text = " ".join([note.get("title", ""), note.get("body", ""), note.get("doi", ""), " ".join(note.get("tags", [])), image_text]).lower()
            if needle and needle not in text:
                continue
            if self.active_tag and self.active_tag not in [img.get("tag") for img in note.get("images", [])]:
                continue
            self.list.addWidget(EnhancedFigureCard(note, self.state.store, self.state.refresh))
        self.list.addStretch()

    def refresh_tag_filters(self) -> None:
        core.clear_layout(self.tag_filters)
        all_btn = QPushButton("全部")
        all_btn.setCheckable(True)
        all_btn.setChecked(self.active_tag is None)
        all_btn.clicked.connect(lambda: self.set_tag_filter(None))
        self.tag_filters.addWidget(all_btn)
        for tag in self.state.store.tags():
            btn = QPushButton(tag["name"])
            btn.setCheckable(True)
            btn.setChecked(self.active_tag == tag["name"])
            btn.setStyleSheet(f"background:{tag['color']}; color:{readable_text_color(tag['color'])}; border-color:{tag['color']};")
            btn.clicked.connect(lambda _=False, name=tag["name"]: self.set_tag_filter(name))
            self.tag_filters.addWidget(btn)
        self.tag_filters.addStretch()

    def set_tag_filter(self, tag_name: str | None) -> None:
        self.active_tag = tag_name
        self.refresh()


class EnhancedFigureCard(core.FigureCard):
    def __init__(self, note: dict, store: core.Store, on_changed) -> None:
        QWidget.__init__(self)
        self.note = note
        self.store = store
        self.on_changed = on_changed
        self.setObjectName("innerCard")
        layout = QVBoxLayout(self)
        title = QLabel(note.get("title", "未命名图谱"))
        title.setWordWrap(True)
        title.setObjectName("itemTitle")
        layout.addWidget(title)
        if note.get("doi"):
            doi = QLabel(note.get("doi", ""))
            doi.setObjectName("muted")
            doi.setWordWrap(True)
            layout.addWidget(doi)
        if note.get("body"):
            body = QLabel(note.get("body", ""))
            body.setObjectName("muted")
            body.setWordWrap(True)
            layout.addWidget(body)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, img in enumerate(note.get("images", [])):
            cell = QVBoxLayout()
            thumb = QLabel()
            thumb.setFixedSize(120, 90)
            thumb.setAlignment(Qt.AlignCenter)
            pix = QPixmap(img.get("value", ""))
            if pix.isNull():
                thumb.setText("图片缺失")
            else:
                thumb.setPixmap(pix.scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            tag = QLabel(img.get("tag", "未分类"))
            color = img.get("tag_color", "#f5b183")
            tag.setAlignment(Qt.AlignCenter)
            tag.setStyleSheet(f"background:{color}; color:{readable_text_color(color)}; border-radius:6px; padding:3px 6px;")
            cell.addWidget(thumb)
            cell.addWidget(tag)
            if img.get("note"):
                note_label = QLabel(img["note"])
                note_label.setObjectName("muted")
                note_label.setWordWrap(True)
                cell.addWidget(note_label)
            holder = QWidget()
            holder.setLayout(cell)
            grid.addWidget(holder, index // 3, index % 3)
        layout.addLayout(grid)
        links = QHBoxLayout()
        if note.get("doi_url"):
            btn = QPushButton("DOI / 原文")
            btn.clicked.connect(lambda _=False, url=note["doi_url"]: webbrowser.open(url))
            links.addWidget(btn)
        edit_tag = QPushButton("修改首图标签")
        edit_tag.clicked.connect(self.edit_tag)
        links.addWidget(edit_tag)
        links.addStretch()
        layout.addLayout(links)

    def edit_tag(self) -> None:
        images = self.note.get("images", [])
        if not images:
            return
        current = images[0].get("tag", "未分类")
        tag_name, ok = QInputDialog.getText(self, "修改标签", "新的标签名称：", text=current)
        if ok and tag_name.strip():
            self.store.update_image_tag(images[0].get("id", ""), tag_name.strip())
            self.on_changed()


class SummaryCalendar(QWidget):
    def __init__(self, store: core.Store) -> None:
        from PySide6.QtWidgets import QCalendarWidget
        QCalendarWidget.__init__(self)
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


class TaskBarChart(QWidget):
    def __init__(self, store: core.Store) -> None:
        super().__init__()
        self.store = store
        self.setMinimumHeight(180)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        theme = self.store.theme
        rect = self.rect().adjusted(28, 18, -24, -34)
        painter.setPen(QPen(QColor(theme["line"]), 1))
        painter.drawRect(rect)
        days = [(date.today() - timedelta(days=6 - i)).isoformat() for i in range(7)]
        values = [self.store.task_count(d) for d in days]
        max_value = max(values + [1])
        bar_width = max(12, int(rect.width() / 14))
        painter.setBrush(QColor(theme["accent2"]))
        painter.setPen(Qt.NoPen)
        for i, value in enumerate(values):
            x = rect.left() + int((rect.width() * i / 6) - bar_width / 2)
            height = int(rect.height() * value / max_value)
            y = rect.bottom() - height
            painter.drawRoundedRect(x, y, bar_width, max(2, height), 5, 5)
            painter.setPen(QPen(QColor(theme["text"]), 1))
            painter.drawText(x - 2, y - 6, str(value))
            painter.drawText(x - 12, rect.bottom() + 22, days[i][5:])
            painter.setPen(Qt.NoPen)


class EnhancedStatsPage(QWidget):
    def __init__(self, state: core.UiState) -> None:
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        top = QGridLayout()
        top.setSpacing(12)
        self.calendar = SummaryCalendar(self.state.store)
        self.calendar.selectionChanged.connect(self.refresh)
        calendar_card = core.Card("日历视图")
        calendar_card.layout.addWidget(self.calendar)
        top.addWidget(calendar_card, 0, 0)
        self.ratio = QLabel()
        self.ratio.setAlignment(Qt.AlignCenter)
        self.ratio.setObjectName("ratio")
        ratio_card = core.Card("任务完成比例")
        ratio_card.layout.addWidget(self.ratio, 1)
        top.addWidget(ratio_card, 0, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        detail_card = core.Card("选中日期详情")
        detail_card.layout.addWidget(self.detail, 1)
        top.addWidget(detail_card, 0, 2)
        layout.addLayout(top)
        trend_card = core.Card("学习时长")
        self.trend = core.TrendChart(self.state.store)
        trend_card.layout.addWidget(self.trend)
        layout.addWidget(trend_card)
        task_card = core.Card("任务数量")
        self.task_chart = TaskBarChart(self.state.store)
        task_card.layout.addWidget(self.task_chart)
        layout.addWidget(task_card)
        self.refresh()

    def refresh(self) -> None:
        day = self.calendar.selectedDate().toString("yyyy-MM-dd")
        tasks = self.state.store.tasks_for(day)
        done = len([t for t in tasks if t.get("completed")])
        total = len(tasks)
        pct = int(done / total * 100) if total else 0
        self.ratio.setText(f"{pct}%\n{done}/{total}")
        minutes = self.state.store.study_seconds(day) // 60
        image_total = self.state.store.image_count(day)
        has_note = bool(self.state.store.data.get("daily_reflections", {}).get(day, "").strip())
        figures = [f for f in self.state.store.data.get("figure_notes", []) if f.get("created_date") == day]
        task_text = "\n".join([f"{'✓' if t.get('completed') else '○'} {t.get('content') or t.get('title', '')}" for t in tasks]) or "这一天没有任务"
        figure_text = "\n".join([f.get("title", "") for f in figures]) or "这一天没有图谱图片"
        self.detail.setText(f"日期：{day}\n学习时长：{minutes}分钟\n任务总数：{total}\n已完成任务：{done}\n图谱图片数量：{image_total}\n每日心得：{'有' if has_note else '无'}\n\n任务：\n{task_text}\n\n图谱：\n{figure_text}")
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(QColor(self.state.store.theme["text"])))
        fmt.setBackground(QBrush(QColor("#dbeafe")))
        fmt.setFontWeight(QFont.Bold)
        self.calendar.setDateTextFormat(self.calendar.selectedDate(), fmt)
        self.trend.update()
        self.task_chart.update()
        self.calendar.viewport().update()


def patch_store() -> None:
    original_init = core.Store.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        ensure_schema(self)
        self.data = self.load_data()

    def load_data(self) -> dict:
        ensure_schema(self)
        tasks: dict[str, list[dict]] = {}
        for row in self.conn.execute("SELECT * FROM tasks ORDER BY created_at"):
            tasks.setdefault(row["task_date"], []).append({"id": row["id"], "content": row["content"], "title": row["content"], "completed": bool(row["completed"]), "priority": priority_label(row["priority"]), "quadrant": priority_label(row["priority"]), "due_at": row["due_at"], "due": row["due_at"]})
        reflections = {row["note_date"]: row["content"] for row in self.conn.execute("SELECT * FROM daily_notes")}
        sessions = [{"id": row["id"], "date": row["study_date"] or row["day"], "seconds": int(row["seconds"] or 0), "minutes": int(row["minutes"] or 0), "created_at": row["created_at"]} for row in self.conn.execute("SELECT * FROM study_sessions")]
        articles: dict[int, dict] = {}
        for row in self.conn.execute("""
            SELECT image_records.*, tags.name AS tag_name, tags.color AS tag_color,
                   articles.title AS article_title, articles.doi_or_link,
                   articles.google_scholar_url, articles.cnki_url AS article_cnki_url,
                   articles.source_url, articles.note AS article_note,
                   articles.created_at AS article_created_at
            FROM image_records JOIN tags ON image_records.tag_id = tags.id
            LEFT JOIN articles ON image_records.article_id = articles.id
            ORDER BY COALESCE(articles.created_at, image_records.created_at) DESC, image_records.id DESC
        """):
            article_id = int(row["article_id"] or 0)
            if article_id not in articles:
                title = row["article_title"] or row["title"] or "未命名图谱"
                doi = row["doi_or_link"] or row["doi"] or ""
                articles[article_id] = {"id": str(article_id), "title": title, "doi": doi, "doi_url": row["source_url"] or row["doi_url"] or source_url_for(doi), "scholar_url": "", "cnki_url": "", "tags": [], "body": row["article_note"] or "", "created_date": (row["article_created_at"] or row["created_at"])[:10], "created_at": row["article_created_at"] or row["created_at"], "images": []}
            articles[article_id]["images"].append({"id": str(row["id"]), "kind": "file", "value": str(core.DATA_DIR / row["image_path"]), "relative": row["image_path"], "tag": row["tag_name"], "tag_color": row["tag_color"], "note": row["note"] or "", "title": row["image_title"] or row["title"] or "", "created_at": row["created_at"]})
            if row["tag_name"] not in articles[article_id]["tags"]:
                articles[article_id]["tags"].append(row["tag_name"])
        return {"study_sessions": sessions, "daily_tasks": tasks, "daily_reflections": reflections, "figure_notes": list(articles.values()), "active_session": None}

    def add_task(self, content: str, priority: str, due_at: str | None = None) -> None:
        content = content.strip()
        if not content:
            return
        due_value = due_at or datetime.combine(date.today(), datetime.max.time()).replace(microsecond=0).isoformat()
        self.conn.execute("INSERT INTO tasks (id, content, priority, due_at, completed, task_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(time.time_ns()), content, priority_label(priority), due_value, 0, core.today_key(), core.now_iso(), core.now_iso()))
        self.save()

    def set_task_done(self, task_id: str, done: bool) -> None:
        self.conn.execute("UPDATE tasks SET completed = ?, updated_at = ? WHERE id = ?", (1 if done else 0, core.now_iso(), task_id))
        self.save()

    def delete_task(self, task_id: str) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.save()

    def add_session(self, seconds: int) -> None:
        if seconds <= 0:
            return
        minutes = max(1, seconds // 60)
        ended = core.now_iso()
        self.conn.execute("INSERT INTO study_sessions (id, study_date, minutes, started_at, ended_at, day, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(time.time_ns()), core.today_key(), minutes, ended, ended, core.today_key(), seconds, ended))
        self.save()

    def study_seconds(self, day: str) -> int:
        row = self.conn.execute("SELECT COALESCE(SUM(seconds), 0) AS total FROM study_sessions WHERE COALESCE(study_date, day) = ?", (day,)).fetchone()
        return int(row["total"] or 0)

    def task_count(self, day: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS total FROM tasks WHERE task_date = ?", (day,)).fetchone()["total"] or 0)

    def image_count(self, day: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS total FROM image_records WHERE substr(created_at, 1, 10) = ?", (day,)).fetchone()["total"] or 0)

    def save_reflection(self, text: str) -> None:
        self.conn.execute("INSERT INTO daily_notes (id, note_date, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(note_date) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at", (f"note-{core.today_key()}", core.today_key(), text, core.now_iso(), core.now_iso()))
        self.save()

    def add_article(self, payload: dict, save: bool = True) -> int:
        title = payload.get("title", "").strip() or "未命名图谱"
        doi = payload.get("doi_or_link", payload.get("doi", "")).strip()
        cursor = self.conn.execute("INSERT INTO articles (title, doi_or_link, google_scholar_url, cnki_url, source_url, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (title, doi, payload.get("google_scholar_url") or "", payload.get("cnki_url") or "", payload.get("source_url") or source_url_for(doi), payload.get("note", payload.get("body", "")), core.now_iso(), core.now_iso()))
        if save:
            self.save()
        return int(cursor.lastrowid)

    def update_article(self, article_id: int, payload: dict, save: bool = True) -> int:
        if article_id <= 0:
            return self.add_article(payload, save=save)
        title = payload.get("title", "").strip() or "未命名图谱"
        doi = payload.get("doi_or_link", payload.get("doi", "")).strip()
        self.conn.execute("UPDATE articles SET title = ?, doi_or_link = ?, google_scholar_url = ?, cnki_url = ?, source_url = ?, note = ?, updated_at = ? WHERE id = ?", (title, doi, payload.get("google_scholar_url") or "", payload.get("cnki_url") or "", payload.get("source_url") or source_url_for(doi), payload.get("note", payload.get("body", "")), core.now_iso(), article_id))
        if save:
            self.save()
        return article_id

    def save_image_file(self, src: str, tag_name: str, stamp: str, index: int) -> str:
        self.get_or_create_tag(tag_name)
        folder = core.IMAGE_DIR / safe_folder_name(tag_name)
        folder.mkdir(parents=True, exist_ok=True)
        source = Path(src)
        if not source.exists():
            return self.relative_image_path(src, tag_name)
        dest = folder / f"{stamp}_{safe_folder_name(tag_name)}_{index:03d}.png"
        counter = index
        while dest.exists():
            counter += 1
            dest = folder / f"{stamp}_{safe_folder_name(tag_name)}_{counter:03d}.png"
        pix = QPixmap(str(source))
        if not pix.isNull():
            pix.save(str(dest), "PNG")
        else:
            shutil.copy2(source, dest)
        return str(dest.relative_to(core.DATA_DIR)).replace("\\", "/")

    def add_figure(self, payload: dict, images: list[dict], article_id: int | None = None) -> int:
        if article_id:
            article_id = self.update_article(article_id, payload, save=False)
        else:
            article_id = self.add_article(payload, save=False)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for index, item in enumerate(images, start=1):
            tag_name = item.get("tag", "").strip() or "未分类"
            rel_path = self.save_image_file(item.get("path", ""), tag_name, stamp, index)
            tag_id, _ = self.get_or_create_tag(tag_name)
            self.conn.execute("INSERT INTO image_records (article_id, title, tag_id, image_path, image_title, note, doi, doi_url, scholar_url, cnki_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (article_id, payload.get("title", "未命名图谱"), tag_id, rel_path, item.get("title", ""), item.get("note", ""), payload.get("doi_or_link", payload.get("doi", "")), payload.get("source_url", ""), "", "", core.now_iso(), core.now_iso()))
        self.save()
        return article_id

    core.Store.__init__ = init
    core.Store.load_data = load_data
    core.Store.add_task = add_task
    core.Store.set_task_done = set_task_done
    core.Store.delete_task = delete_task
    core.Store.add_session = add_session
    core.Store.study_seconds = study_seconds
    core.Store.task_count = task_count
    core.Store.image_count = image_count
    core.Store.save_reflection = save_reflection
    core.Store.add_article = add_article
    core.Store.update_article = update_article
    core.Store.save_image_file = save_image_file
    core.Store.add_figure = add_figure


def patch_task_pages() -> None:
    def today_add(self):
        self.state.store.add_task(self.quick_task.text(), self.quick_priority.currentData(), self.quick_due.dateTime().toPython().replace(microsecond=0).isoformat())
        self.quick_task.clear()
        self.quick_due.setDateTime(QDateTime.currentDateTime())
        self.state.refresh()

    def tasks_add(self):
        self.state.store.add_task(self.title.text(), self.quadrant.currentData(), self.due.dateTime().toPython().replace(microsecond=0).isoformat())
        self.title.clear()
        self.due.setDateTime(QDateTime.currentDateTime())
        self.state.refresh()

    def tasks_refresh(self):
        core.clear_layout(self.grid)
        by_priority = {key: [] for key in PRIORITIES}
        for task in self.state.store.all_today_tasks():
            by_priority.setdefault(priority_label(task.get("priority") or task.get("quadrant")), []).append(task)
        for index, (label, color) in enumerate(PRIORITIES.items()):
            card = core.Card(label)
            card.setStyleSheet(f"QFrame#card {{ border-top: 5px solid {color}; }}")
            for task in by_priority.get(label, []):
                row = EnhancedTaskRow(self.state.store, task)
                row.changed.connect(self.state.refresh)
                card.layout.addWidget(row)
            card.layout.addStretch()
            self.grid.addWidget(card, index // 2, index % 2)

    core.TaskRow = EnhancedTaskRow
    core.TodayPage.add_task = today_add
    core.TasksPage.add_task = tasks_add
    core.TasksPage.refresh = tasks_refresh


def apply() -> None:
    if getattr(core, "_enhancements_applied", False):
        return
    patch_store()
    patch_task_pages()
    core.FiguresPage = EnhancedFiguresPage
    core.FigureCard = EnhancedFigureCard
    core.StatsPage = EnhancedStatsPage
    core._enhancements_applied = True
