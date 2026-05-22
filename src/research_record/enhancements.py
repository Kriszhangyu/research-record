from __future__ import annotations

import shutil
import tempfile
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from PySide6.QtCore import QDate, QDateTime, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCharFormat, QBrush
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget

from . import app as core

PRIORITIES = {"紧急重要": "#ef6f6c", "紧急不重要": "#f3b34c", "重要不紧急": "#68b984", "不紧急不重要": "#a98be8"}
LEGACY_PRIORITY = {"urgent_important": "紧急重要", "urgent_not_important": "紧急不重要", "important_not_urgent": "重要不紧急", "not_urgent_not_important": "不紧急不重要"}


def priority_label(value: str | None) -> str:
    value = value or "紧急重要"
    return LEGACY_PRIORITY.get(value, value if value in PRIORITIES else "紧急重要")


def scholar_url(title: str) -> str:
    return f"https://scholar.google.com/scholar?q={quote_plus(title.strip())}"


def cnki_url(title: str) -> str:
    return f"https://kns.cnki.net/kns8s/defaultresult/index?kw={quote_plus(title.strip())}"


def source_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return value if value.startswith(("http://", "https://")) else f"https://doi.org/{value}"


def text_color(color: str) -> str:
    return core.readable_text_color(color)


def ensure_columns(store, table: str, additions: dict[str, str]) -> None:
    columns = {row["name"] for row in store.conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in additions.items():
        if name not in columns:
            store.conn.execute(ddl)


def ensure_schema(store) -> None:
    store.conn.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, content TEXT NOT NULL, priority TEXT NOT NULL, due_at TEXT,
        completed INTEGER NOT NULL DEFAULT 0, task_date TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, doi_or_link TEXT,
        google_scholar_url TEXT, cnki_url TEXT, source_url TEXT, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS daily_notes (
        id TEXT PRIMARY KEY, note_date TEXT NOT NULL UNIQUE, content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """)
    ensure_columns(store, "image_records", {"article_id": "ALTER TABLE image_records ADD COLUMN article_id INTEGER", "image_title": "ALTER TABLE image_records ADD COLUMN image_title TEXT"})
    ensure_columns(store, "study_sessions", {"study_date": "ALTER TABLE study_sessions ADD COLUMN study_date TEXT", "minutes": "ALTER TABLE study_sessions ADD COLUMN minutes INTEGER", "started_at": "ALTER TABLE study_sessions ADD COLUMN started_at TEXT", "ended_at": "ALTER TABLE study_sessions ADD COLUMN ended_at TEXT"})
    store.conn.execute("UPDATE study_sessions SET study_date = day WHERE (study_date IS NULL OR study_date = '') AND day IS NOT NULL")
    store.conn.execute("UPDATE study_sessions SET minutes = CAST(seconds / 60 AS INTEGER) WHERE minutes IS NULL AND seconds IS NOT NULL")
    for old, new in LEGACY_PRIORITY.items():
        store.conn.execute("UPDATE tasks SET priority = ? WHERE priority = ?", (new, old))
    store.conn.execute("""
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
    """)
    store.conn.execute("INSERT OR IGNORE INTO daily_notes (id, note_date, content, created_at, updated_at) SELECT 'note-' || day, day, body, updated_at, updated_at FROM daily_reflections")
    for row in store.conn.execute("SELECT * FROM image_records WHERE article_id IS NULL OR article_id = 0").fetchall():
        existing = store.conn.execute("SELECT id FROM articles WHERE title = ? AND COALESCE(doi_or_link, '') = COALESCE(?, '') LIMIT 1", (row["title"], row["doi"] or "")).fetchone()
        article_id = existing["id"] if existing else store.conn.execute(
            "INSERT INTO articles (title, doi_or_link, google_scholar_url, cnki_url, source_url, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row["title"], row["doi"] or "", row["scholar_url"] or "", row["cnki_url"] or "", row["doi_url"] or "", "", row["created_at"], row["updated_at"]),
        ).lastrowid
        store.conn.execute("UPDATE image_records SET article_id = ?, image_title = COALESCE(image_title, title) WHERE id = ?", (article_id, row["id"]))
    store.conn.commit()


def patch_store() -> None:
    original_init = core.Store.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        ensure_schema(self)
        self.data = self.load_data()

    def load_data(self):
        ensure_schema(self)
        tasks = {}
        for row in self.conn.execute("SELECT * FROM tasks ORDER BY created_at"):
            tasks.setdefault(row["task_date"], []).append({"id": row["id"], "content": row["content"], "title": row["content"], "completed": bool(row["completed"]), "priority": priority_label(row["priority"]), "quadrant": priority_label(row["priority"]), "due_at": row["due_at"], "due": row["due_at"]})
        notes = {row["note_date"]: row["content"] for row in self.conn.execute("SELECT * FROM daily_notes")}
        sessions = [{"id": row["id"], "date": row["study_date"] or row["day"], "seconds": int(row["seconds"] or 0), "minutes": int(row["minutes"] or 0), "created_at": row["created_at"]} for row in self.conn.execute("SELECT * FROM study_sessions")]
        articles = {}
        for row in self.conn.execute("""
            SELECT image_records.*, tags.name AS tag_name, tags.color AS tag_color,
                   articles.title AS article_title, articles.doi_or_link, articles.google_scholar_url,
                   articles.cnki_url AS article_cnki_url, articles.source_url, articles.note AS article_note,
                   articles.created_at AS article_created_at
            FROM image_records JOIN tags ON image_records.tag_id = tags.id
            LEFT JOIN articles ON image_records.article_id = articles.id
            ORDER BY COALESCE(articles.created_at, image_records.created_at) DESC, image_records.id DESC
        """):
            article_id = int(row["article_id"] or 0)
            if article_id not in articles:
                title = row["article_title"] or row["title"] or "未命名图谱"
                doi = row["doi_or_link"] or row["doi"] or ""
                articles[article_id] = {"id": str(article_id), "title": title, "doi": doi, "doi_url": row["source_url"] or row["doi_url"] or source_url(doi), "scholar_url": row["google_scholar_url"] or row["scholar_url"] or scholar_url(title), "cnki_url": row["article_cnki_url"] or row["cnki_url"] or cnki_url(title), "body": row["article_note"] or "", "tags": [], "created_date": (row["article_created_at"] or row["created_at"])[:10], "created_at": row["article_created_at"] or row["created_at"], "images": []}
            articles[article_id]["images"].append({"id": str(row["id"]), "kind": "file", "value": str(core.DATA_DIR / row["image_path"]), "relative": row["image_path"], "tag": row["tag_name"], "tag_color": row["tag_color"], "note": row["note"] or "", "title": row["image_title"] or row["title"] or "", "created_at": row["created_at"]})
            if row["tag_name"] not in articles[article_id]["tags"]:
                articles[article_id]["tags"].append(row["tag_name"])
        return {"study_sessions": sessions, "daily_tasks": tasks, "daily_reflections": notes, "figure_notes": list(articles.values()), "active_session": None}

    def add_task(self, content, priority, due_at=None):
        content = content.strip()
        if content:
            self.conn.execute("INSERT INTO tasks (id, content, priority, due_at, completed, task_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(time.time_ns()), content, priority_label(priority), due_at, 0, core.today_key(), core.now_iso(), core.now_iso()))
            self.save()

    def set_task_done(self, task_id, done):
        self.conn.execute("UPDATE tasks SET completed = ?, updated_at = ? WHERE id = ?", (1 if done else 0, core.now_iso(), task_id)); self.save()

    def delete_task(self, task_id):
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,)); self.save()

    def add_session(self, seconds):
        if seconds > 0:
            ended = core.now_iso(); minutes = max(1, seconds // 60)
            self.conn.execute("INSERT INTO study_sessions (id, study_date, minutes, started_at, ended_at, day, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(time.time_ns()), core.today_key(), minutes, ended, ended, core.today_key(), seconds, ended)); self.save()

    def study_seconds(self, day):
        return int(self.conn.execute("SELECT COALESCE(SUM(seconds), 0) AS total FROM study_sessions WHERE COALESCE(study_date, day) = ?", (day,)).fetchone()["total"] or 0)

    def task_count(self, day):
        return int(self.conn.execute("SELECT COUNT(*) AS total FROM tasks WHERE task_date = ?", (day,)).fetchone()["total"] or 0)

    def image_count(self, day):
        return int(self.conn.execute("SELECT COUNT(*) AS total FROM image_records WHERE substr(created_at, 1, 10) = ?", (day,)).fetchone()["total"] or 0)

    def save_reflection(self, text):
        self.conn.execute("INSERT INTO daily_notes (id, note_date, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(note_date) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at", (f"note-{core.today_key()}", core.today_key(), text, core.now_iso(), core.now_iso())); self.save()

    def add_figure(self, payload, images):
        title = payload.get("title", "未命名图谱").strip() or "未命名图谱"; doi = payload.get("doi_or_link", payload.get("doi", "")).strip(); stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        article_id = self.conn.execute("INSERT INTO articles (title, doi_or_link, google_scholar_url, cnki_url, source_url, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (title, doi, payload.get("google_scholar_url") or scholar_url(title), payload.get("cnki_url") or cnki_url(title), payload.get("source_url") or source_url(doi), payload.get("note", payload.get("body", "")), core.now_iso(), core.now_iso())).lastrowid
        for index, item in enumerate(images, 1):
            tag = item.get("tag", "").strip() or "未分类"; tag_id, _ = self.get_or_create_tag(tag); folder = core.IMAGE_DIR / core.safe_folder_name(tag); folder.mkdir(parents=True, exist_ok=True); src = Path(item.get("path", ""))
            if src.exists():
                suffix = src.suffix.lower() if src.suffix.lower() in [".png", ".jpg", ".jpeg"] else ".png"; dest = folder / f"{stamp}_{core.safe_folder_name(tag)}_{index:03d}{suffix}"; n = index
                while dest.exists(): n += 1; dest = folder / f"{stamp}_{core.safe_folder_name(tag)}_{n:03d}{suffix}"
                shutil.copy2(src, dest); rel_path = str(dest.relative_to(core.DATA_DIR)).replace("\\", "/")
            else:
                rel_path = self.relative_image_path(str(src), tag)
            self.conn.execute("INSERT INTO image_records (article_id, title, tag_id, image_path, image_title, note, doi, doi_url, scholar_url, cnki_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (article_id, title, tag_id, rel_path, item.get("title", ""), item.get("note", ""), doi, source_url(doi), scholar_url(title), cnki_url(title), core.now_iso(), core.now_iso()))
        self.save()

    core.Store.__init__ = init; core.Store.load_data = load_data; core.Store.add_task = add_task; core.Store.set_task_done = set_task_done; core.Store.delete_task = delete_task; core.Store.add_session = add_session; core.Store.study_seconds = study_seconds; core.Store.task_count = task_count; core.Store.image_count = image_count; core.Store.save_reflection = save_reflection; core.Store.add_figure = add_figure


class FigureInputPage(QWidget):
    def __init__(self, state):
        super().__init__(); self.state = state; self.pending_images = []; self.active_tag = None; self.setAcceptDrops(True); self.setFocusPolicy(Qt.StrongFocus); shortcut = QShortcut(QKeySequence.Paste, self); shortcut.setContext(Qt.WidgetWithChildrenShortcut); shortcut.activated.connect(self.paste_image)
        layout = QGridLayout(self); layout.setColumnStretch(0, 1); layout.setColumnStretch(1, 1); form = core.Card("添加图谱文章"); self.title = QLineEdit(); self.title.setPlaceholderText("论文题目"); self.doi = QLineEdit(); self.doi.setPlaceholderText("DOI 或链接，可不填"); self.article_note = QTextEdit(); self.article_note.setMinimumHeight(88); self.article_note.setPlaceholderText("文章备注"); self.image_tag = QLineEdit(); self.image_tag.setPlaceholderText("当前图片标签，例如 框架图"); self.image_note = QTextEdit(); self.image_note.setMinimumHeight(92); self.image_note.setPlaceholderText("当前图片备注")
        f = QFormLayout(); f.addRow("论文题目", self.title); f.addRow("DOI / Links", self.doi); f.addRow("文章备注", self.article_note); f.addRow("图片标签", self.image_tag); f.addRow("图片备注", self.image_note); form.layout.addLayout(f)
        links = QHBoxLayout()
        for text, fn in [("Google Scholar", self.open_scholar), ("知网", self.open_cnki), ("DOI / 原文链接", self.open_source)]: b = QPushButton(text); b.clicked.connect(fn); links.addWidget(b)
        form.layout.addLayout(links); pick = QPushButton("选择图片"); pick.clicked.connect(self.pick_images); form.layout.addWidget(pick); self.image_label = QLabel("当前文章已添加 0 张图片"); self.image_label.setObjectName("muted"); form.layout.addWidget(self.image_label); self.pending_list = QVBoxLayout(); form.layout.addLayout(self.pending_list)
        next_btn = QPushButton("添加下一张图片"); next_btn.clicked.connect(self.prepare_next_image); finish = QPushButton("完成当前文章，开始下一篇"); finish.clicked.connect(self.finish_article); clear = QPushButton("清空当前文章"); clear.clicked.connect(self.clear_form); form.layout.addWidget(next_btn); form.layout.addWidget(finish); form.layout.addWidget(clear)
        lib = core.Card("图谱库"); self.search = QLineEdit(); self.search.setPlaceholderText("搜索题目、标签、图片备注、文章备注、DOI / Links"); self.search.textChanged.connect(self.refresh); self.tag_filters = QHBoxLayout(); self.list = QVBoxLayout(); lib.layout.addWidget(self.search); lib.layout.addLayout(self.tag_filters); lib.layout.addLayout(self.list); layout.addWidget(form, 0, 0); layout.addWidget(lib, 0, 1); self.refresh()

    def pick_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图谱图片", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)"); self.add_image_paths(paths)
    def add_image_paths(self, paths):
        tag = self.image_tag.text().strip() or "未分类"; note = self.image_note.toPlainText().strip()
        for path in paths:
            if Path(path).exists(): self.pending_images.append({"path": path, "tag": tag, "note": note, "title": Path(path).stem})
        self.refresh_pending()
    def refresh_pending(self):
        core.clear_layout(self.pending_list); self.image_label.setText(f"当前文章已添加 {len(self.pending_images)} 张图片")
        for i, item in enumerate(self.pending_images):
            row = QHBoxLayout(); thumb = QLabel(); thumb.setFixedSize(88, 66); thumb.setAlignment(Qt.AlignCenter); pix = QPixmap(item["path"]); thumb.setText("图片缺失") if pix.isNull() else thumb.setPixmap(pix.scaled(88, 66, Qt.KeepAspectRatio, Qt.SmoothTransformation)); _, color = self.state.store.get_or_create_tag(item["tag"]); tag = QLabel(item["tag"]); tag.setStyleSheet(f"background:{color}; color:{text_color(color)}; border-radius:6px; padding:4px 8px;"); note = QLabel(item.get("note") or "无图片备注"); note.setObjectName("muted"); note.setWordWrap(True); rm = QPushButton("移除"); rm.clicked.connect(lambda _=False, idx=i: self.remove_pending(idx)); row.addWidget(thumb); row.addWidget(tag); row.addWidget(note, 1); row.addWidget(rm); self.pending_list.addLayout(row)
    def remove_pending(self, index):
        if 0 <= index < len(self.pending_images): self.pending_images.pop(index); self.refresh_pending()
    def prepare_next_image(self): self.image_tag.clear(); self.image_note.clear(); self.image_label.setText(f"当前文章已添加 {len(self.pending_images)} 张图片，可继续选择、拖拽或粘贴下一张。")
    def finish_article(self):
        if not self.title.text().strip(): QMessageBox.warning(self, "缺少题目", "请先填写论文题目。"); return
        if not self.pending_images: QMessageBox.warning(self, "缺少图片", "请至少添加一张图片。"); return
        title = self.title.text().strip(); doi = self.doi.text().strip(); self.state.store.add_figure({"title": title, "doi_or_link": doi, "source_url": source_url(doi), "google_scholar_url": scholar_url(title), "cnki_url": cnki_url(title), "note": self.article_note.toPlainText().strip()}, self.pending_images); self.clear_form(); self.state.refresh()
    def clear_form(self): self.title.clear(); self.doi.clear(); self.article_note.clear(); self.image_tag.clear(); self.image_note.clear(); self.pending_images = []; self.refresh_pending()
    def open_scholar(self):
        if self.title.text().strip(): webbrowser.open(scholar_url(self.title.text()))
        else: QMessageBox.information(self, "缺少题目", "请先填写论文题目。")
    def open_cnki(self):
        if self.title.text().strip(): webbrowser.open(cnki_url(self.title.text()))
        else: QMessageBox.information(self, "缺少题目", "请先填写论文题目。")
    def open_source(self):
        url = source_url(self.doi.text()); webbrowser.open(url) if url else QMessageBox.information(self, "缺少 DOI 或链接", "请先填写 DOI 或原文链接。")
    def paste_image(self):
        if QApplication.clipboard().mimeData().hasImage():
            path = Path(tempfile.gettempdir()) / f"research_record_clipboard_{time.time_ns()}.png"; QApplication.clipboard().image().save(str(path), "PNG"); self.add_image_paths([str(path)])
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event): self.add_image_paths([url.toLocalFile() for url in event.mimeData().urls() if Path(url.toLocalFile()).suffix.lower() in [".png", ".jpg", ".jpeg", ".bmp"])
    def refresh(self):
        self.state.store.data = self.state.store.load_data(); self.refresh_tags(); core.clear_layout(self.list); needle = self.search.text().strip().lower()
        for note in self.state.store.data.get("figure_notes", []):
            image_text = " ".join([f"{img.get('tag','')} {img.get('note','')} {img.get('title','')}" for img in note.get("images", [])]); hay = " ".join([note.get("title", ""), note.get("body", ""), note.get("doi", ""), " ".join(note.get("tags", [])), image_text]).lower()
            if needle and needle not in hay: continue
            if self.active_tag and self.active_tag not in [img.get("tag") for img in note.get("images", [])]: continue
            self.list.addWidget(ArticleCard(note, self.state.store, self.state.refresh))
        self.list.addStretch()
    def refresh_tags(self):
        core.clear_layout(self.tag_filters); all_btn = QPushButton("全部"); all_btn.setCheckable(True); all_btn.setChecked(self.active_tag is None); all_btn.clicked.connect(lambda: self.set_tag(None)); self.tag_filters.addWidget(all_btn)
        for tag in self.state.store.tags():
            btn = QPushButton(tag["name"]); btn.setCheckable(True); btn.setChecked(self.active_tag == tag["name"]); btn.setStyleSheet(f"background:{tag['color']}; color:{text_color(tag['color'])}; border-color:{tag['color']};"); btn.clicked.connect(lambda _=False, name=tag["name"]: self.set_tag(name)); self.tag_filters.addWidget(btn)
        self.tag_filters.addStretch()
    def set_tag(self, tag): self.active_tag = tag; self.refresh()


class ArticleCard(core.FigureCard):
    def __init__(self, note, store, on_changed):
        QWidget.__init__(self); self.note = note; self.store = store; self.on_changed = on_changed; self.setObjectName("innerCard"); layout = QVBoxLayout(self); title = QLabel(note.get("title", "未命名图谱")); title.setWordWrap(True); title.setObjectName("itemTitle"); layout.addWidget(title)
        if note.get("doi"): d = QLabel(note.get("doi", "")); d.setObjectName("muted"); d.setWordWrap(True); layout.addWidget(d)
        if note.get("body"): b = QLabel(note.get("body", "")); b.setObjectName("muted"); b.setWordWrap(True); layout.addWidget(b)
        grid = QGridLayout(); grid.setSpacing(8)
        for i, img in enumerate(note.get("images", [])):
            cell = QVBoxLayout(); thumb = QLabel(); thumb.setFixedSize(120, 90); thumb.setAlignment(Qt.AlignCenter); pix = QPixmap(img.get("value", "")); thumb.setText("图片缺失") if pix.isNull() else thumb.setPixmap(pix.scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)); thumb.mousePressEvent = lambda event, path=img.get("value", ""): self.show_preview(path); tag = QLabel(img.get("tag", "未分类")); color = img.get("tag_color", "#f5b183"); tag.setAlignment(Qt.AlignCenter); tag.setStyleSheet(f"background:{color}; color:{text_color(color)}; border-radius:6px; padding:3px 6px;"); cell.addWidget(thumb); cell.addWidget(tag)
            if img.get("note"): n = QLabel(img["note"]); n.setObjectName("muted"); n.setWordWrap(True); cell.addWidget(n)
            holder = QWidget(); holder.setLayout(cell); grid.addWidget(holder, i // 3, i % 3)
        layout.addLayout(grid); links = QHBoxLayout()
        for text, key in [("Scholar", "scholar_url"), ("知网", "cnki_url"), ("DOI / 原文", "doi_url")]:
            if note.get(key): btn = QPushButton(text); btn.clicked.connect(lambda _=False, url=note[key]: webbrowser.open(url)); links.addWidget(btn)
        edit = QPushButton("修改首图标签"); edit.clicked.connect(self.edit_tag); links.addWidget(edit); links.addStretch(); layout.addLayout(links)
    def edit_tag(self):
        imgs = self.note.get("images", [])
        if imgs:
            new, ok = QInputDialog.getText(self, "修改标签", "新的标签名称：", text=imgs[0].get("tag", "未分类"))
            if ok and new.strip(): self.store.update_image_tag(imgs[0].get("id", ""), new.strip()); self.on_changed()


class SummaryCalendar(core.QCalendarWidget):
    def __init__(self, store): super().__init__(); self.store = store; self.setGridVisible(True)
    def paintCell(self, painter, rect, qdate):
        theme = self.store.theme; painter.save()
        if qdate == self.selectedDate(): painter.fillRect(rect, QColor("#dbeafe"))
        super().paintCell(painter, rect, qdate); day = qdate.toString("yyyy-MM-dd"); painter.setPen(QPen(QColor(theme["muted"]), 1)); font = QFont(painter.font()); font.setPointSize(max(7, font.pointSize() - 3)); painter.setFont(font); painter.drawText(rect.adjusted(2, rect.height() // 2, -2, -2), Qt.AlignCenter, f"({self.store.study_seconds(day)//60}分, {self.store.task_count(day)}项)")
        if qdate == QDate.currentDate(): painter.setPen(QPen(QColor(theme["accent"]), 2)); painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.restore()


class TaskBarChart(QWidget):
    def __init__(self, store): super().__init__(); self.store = store; self.setMinimumHeight(180)
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing); theme = self.store.theme; rect = self.rect().adjusted(28, 18, -24, -34); painter.setPen(QPen(QColor(theme["line"]), 1)); painter.drawRect(rect); days = [(date.today() - timedelta(days=6 - i)).isoformat() for i in range(7)]; values = [self.store.task_count(d) for d in days]; max_value = max(values + [1]); bar_w = max(12, int(rect.width()/14)); painter.setBrush(QColor(theme["accent2"])); painter.setPen(Qt.NoPen)
        for i, value in enumerate(values):
            x = rect.left() + int((rect.width()*i/6) - bar_w/2); h = int(rect.height()*value/max_value); y = rect.bottom() - h; painter.drawRoundedRect(x, y, bar_w, max(2, h), 5, 5); painter.setPen(QPen(QColor(theme["text"]), 1)); painter.drawText(x-2, y-6, str(value)); painter.drawText(x-12, rect.bottom()+22, days[i][5:]); painter.setPen(Qt.NoPen)


class StatsPage(QWidget):
    def __init__(self, state):
        super().__init__(); self.state = state; layout = QVBoxLayout(self); top = QGridLayout(); self.calendar = SummaryCalendar(self.state.store); self.calendar.selectionChanged.connect(self.refresh); c = core.Card("日历视图"); c.layout.addWidget(self.calendar); top.addWidget(c, 0, 0); self.ratio = QLabel(); self.ratio.setAlignment(Qt.AlignCenter); self.ratio.setObjectName("ratio"); r = core.Card("任务完成比例"); r.layout.addWidget(self.ratio, 1); top.addWidget(r, 0, 1); self.detail = QLabel(); self.detail.setWordWrap(True); d = core.Card("选中日期详情"); d.layout.addWidget(self.detail, 1); top.addWidget(d, 0, 2); layout.addLayout(top); t = core.Card("学习时长"); self.trend = core.TrendChart(self.state.store); t.layout.addWidget(self.trend); layout.addWidget(t); bc = core.Card("任务数量"); self.task_chart = TaskBarChart(self.state.store); bc.layout.addWidget(self.task_chart); layout.addWidget(bc); self.refresh()
    def refresh(self):
        day = self.calendar.selectedDate().toString("yyyy-MM-dd"); tasks = self.state.store.tasks_for(day); done = len([t for t in tasks if t.get("completed")]); total = len(tasks); self.ratio.setText(f"{int(done/total*100) if total else 0}%\n{done}/{total}"); minutes = self.state.store.study_seconds(day)//60; images = self.state.store.image_count(day); has_note = bool(self.state.store.data.get("daily_reflections", {}).get(day, "").strip()); figs = [f for f in self.state.store.data.get("figure_notes", []) if f.get("created_date") == day]; task_text = "\n".join([f"{'✓' if t.get('completed') else '○'} {t.get('content') or t.get('title','')}" for t in tasks]) or "这一天没有任务"; fig_text = "\n".join([f.get("title", "") for f in figs]) or "这一天没有图谱图片"; self.detail.setText(f"日期：{day}\n学习时长：{minutes}分钟\n任务总数：{total}\n已完成任务：{done}\n图谱图片数量：{images}\n每日心得：{'有' if has_note else '无'}\n\n任务：\n{task_text}\n\n图谱：\n{fig_text}"); fmt = QTextCharFormat(); fmt.setForeground(QBrush(QColor(self.state.store.theme["text"]))); fmt.setBackground(QBrush(QColor("#dbeafe"))); fmt.setFontWeight(QFont.Bold); self.calendar.setDateTextFormat(self.calendar.selectedDate(), fmt); self.trend.update(); self.task_chart.update(); self.calendar.viewport().update()


def apply():
    if getattr(core, "_enhancements_applied", False): return
    patch_store(); core.FiguresPage = FigureInputPage; core.FigureCard = ArticleCard; core.StatsPage = StatsPage; core._enhancements_applied = True
