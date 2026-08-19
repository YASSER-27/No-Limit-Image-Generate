import sys
import os
import json
import time
import hashlib
import base64
import requests
import asyncio
import threading
import subprocess
from datetime import datetime
from PySide6.QtCore import Qt, QThread, Signal, QSize, QMimeData, QUrl, QPoint, QPropertyAnimation, QEasingCurve, QRect, QTimer, QByteArray, QBuffer, QIODevice, QLockFile, QSettings
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QComboBox, QScrollArea, QFrame, QLabel,
    QGridLayout, QSizePolicy, QSizeGrip, QGraphicsDropShadowEffect, QMenu, QDialog, QGraphicsBlurEffect, QFileDialog
)
from PySide6.QtGui import QPixmap, QIcon, QFont, QColor, QDrag, QAction, QCursor, QKeyEvent, QRadialGradient, QPainter, QBrush, QImage, QImageReader
import websockets

# Constants
SIGNER_URL = "https://prompt-signer.freegen.app"
GENERATOR_URL = "https://image-generator.freegen.app"
WEBSOCKET_URL = "wss://websocket-bridge.freegen.app/ws"

SAVE_DIR = os.path.join(os.path.expanduser("~"), ".genapp")
PROMPTS_FILE = os.path.join(SAVE_DIR, "prompts.jsonl")
COOLDOWN_FILE = os.path.join(SAVE_DIR, "cooldown.json")

if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

class APIWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, prompt, ratio, image_base64=None):
        super().__init__()
        self.prompt = prompt
        self.ratio = ratio
        self.image_base64 = image_base64
        self._is_running = True

    def run(self):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Origin": "https://freegen.app",
                "Referer": "https://freegen.app/",
                "Accept": "application/json"
            }
            
            # Send prompt (and image_data if present) to signer
            signer_body = {"prompt": self.prompt}
            if self.image_base64:
                signer_body["image_data"] = self.image_base64
            
            response = requests.post(SIGNER_URL, json=signer_body, headers=headers, timeout=10)
            if response.status_code != 200: raise Exception(f"Signer Error")
            signer_data = response.json()
            ts, sig = signer_data.get("ts"), signer_data.get("sig")

            body = {"prompt": self.prompt, "ts": ts, "sig": sig, "ratio_id": self.ratio}
            if self.image_base64:
                body["image_data"] = self.image_base64
            
            response = requests.post(GENERATOR_URL, json=body, headers=headers, timeout=10)
            if response.status_code != 200: raise Exception(f"Generator Error")
            
            gen_data = response.json()
            job_id = gen_data.get("job_id")
            if not job_id:
                if "image_data_url" in gen_data:
                    self.finished.emit({"url": gen_data["image_data_url"], "prompt": self.prompt, "ratio": self.ratio})
                    return
                raise Exception("No job_id")

            asyncio.run(self.wait_for_ws_result(job_id))
        except Exception as e:
            self.error.emit(str(e))

    async def wait_for_ws_result(self, job_id):
        ts = int(time.time())
        msg = f"{job_id}{ts}"
        h = hashlib.sha256(msg.encode()).hexdigest()
        auth = base64.b64encode(h.encode()).decode()[:20] + ":" + str(ts)

        try:
            async with websockets.connect(WEBSOCKET_URL, open_timeout=10) as ws:
                await ws.send(json.dumps({"type": "subscribe", "job_id": job_id, "auth": auth}))
                while self._is_running:
                    try:
                        # Add a 90-second timeout to prevent hanging forever if the server doesn't respond
                        msg_text = await asyncio.wait_for(ws.recv(), timeout=90)
                        data = json.loads(msg_text)
                        if data.get("type") == "result":
                            self.finished.emit({"url": data.get("image_data"), "prompt": self.prompt, "ratio": self.ratio})
                            break
                    except asyncio.TimeoutError:
                        self.error.emit("Generation timeout: The server is taking too long. Please try again.")
                        break
                    except Exception as e:
                        self.error.emit(f"WebSocket Error: {str(e)}")
                        break
        except Exception as e:
            self.error.emit(f"Connection Error: {str(e)}")

    def stop(self):
        self._is_running = False

class ImageLoaderThread(QThread):
    image_loaded = Signal(object, object)
    
    def __init__(self):
        super().__init__()
        self.queue = []
        self.is_running = True
        
    def add_tasks(self, cards):
        self.queue.extend(cards)
        if not self.isRunning():
            self.start()
            
    def run(self):
        while self.queue and self.is_running:
            card = self.queue.pop(0)
            img = QImage(card.source)
            if not img.isNull():
                self.image_loaded.emit(card, img)


class CustomTitleBar(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(38)
        self.setObjectName("TitleBar")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)
        layout.setSpacing(8)
        
        # Left Spacer for balance
        dummy_left = QWidget()
        dummy_left.setFixedSize(55, 13)
        layout.addWidget(dummy_left)
        
        layout.addStretch()
        
        self.title = QLabel("image Gen")
        self.title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 13px;")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)
        
        layout.addStretch()
        
        self.btn_min = QPushButton("")
        self.btn_min.setFixedSize(13, 13)
        self.btn_min.setObjectName("MacMinBtn")
        self.btn_min.clicked.connect(self.parent.showMinimized)
        
        self.btn_max = QPushButton("")
        self.btn_max.setFixedSize(13, 13)
        self.btn_max.setObjectName("MacMaxBtn")
        self.btn_max.clicked.connect(self.parent.toggle_max_normal)
        
        self.btn_close = QPushButton("")
        self.btn_close.setFixedSize(13, 13)
        self.btn_close.setObjectName("MacCloseBtn")
        self.btn_close.clicked.connect(self.parent.close)
        
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.parent.dragPos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.parent.move(self.parent.pos() + event.globalPosition().toPoint() - self.parent.dragPos)
            self.parent.dragPos = event.globalPosition().toPoint()

class PromptArea(QTextEdit):
    submitted = Signal()
    history_nav = Signal(int)
    image_pasted = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Type your prompt...")
        self.setFixedHeight(85)
        self.setObjectName("PromptInput")
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
        elif event.key() == Qt.Key_Up:
            self.history_nav.emit(-1)
        elif event.key() == Qt.Key_Down:
            self.history_nav.emit(1)
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if image:
                self.image_pasted.emit(image)
                return
        # Force pasted text to be pure plain text (removes HTML formatting/backgrounds)
        if source.hasText():
            self.insertPlainText(source.text())
        else:
            super().insertFromMimeData(source)

class ImageCard(QFrame):
    request_regen = Signal(str)
    request_gen_with_image = Signal(object, str)
    request_delete = Signal(object)
    request_preview = Signal(object, bool)
    
    def __init__(self, source, ratio_str="1:1", prompt=""):
        super().__init__()
        self.source = source
        self.prompt = prompt
        self.ratio_str = ratio_str
        self.pixmap = None
        self.is_hidden = False
        self.setObjectName("ImageCard")
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_menu)
        
        self.press_timer = QTimer(self)
        self.press_timer.setSingleShot(True)
        self.press_timer.timeout.connect(self.long_press_action)
        
        self.target_width = 340
        self.ratio = 1.0
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.lbl = QLabel("")
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setScaledContents(True)
        self.lbl.setObjectName("ImageLabel")
        layout.addWidget(self.lbl)
        
        self.blur_effect = QGraphicsBlurEffect(self)
        self.blur_effect.setBlurRadius(0)
        self.lbl.setGraphicsEffect(self.blur_effect)

        # Ultra-fast header read
        if os.path.exists(self.source):
            reader = QImageReader(self.source)
            size = reader.size()
            if size.isValid() and size.height() > 0:
                self.ratio = size.width() / size.height()
        else:
            self.ratio = self.get_ratio_val()
            
        self.apply_ratio_size()

        if self.source.startswith("data:image"):
            self.load_base64()
        elif not os.path.exists(self.source):
            threading.Thread(target=self.fetch, daemon=True).start()

    def get_ratio_val(self):
        if self.ratio_str == "16:9": return 16/9
        elif self.ratio_str == "4:3": return 4/3
        elif self.ratio_str == "3:4": return 3/4
        elif self.ratio_str == "9:16": return 9/16
        return 1.0

    def update_width(self, new_width):
        self.target_width = new_width
        self.setFixedWidth(self.target_width)
        self.apply_ratio_size()

    def apply_ratio_size(self):
        target_h = int(self.target_width / self.ratio)
        self.setFixedHeight(target_h)

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap
        if not pixmap.isNull():
            self.ratio = pixmap.width() / pixmap.height()
        self.apply_ratio_size()
        self.lbl.setPixmap(self.pixmap)

    def load_base64(self):
        try:
            header, encoded = self.source.split(",", 1)
            data = base64.b64decode(encoded)
            self.pixmap = QPixmap()
            self.pixmap.loadFromData(data)
            if not self.pixmap.isNull():
                self.ratio = self.pixmap.width() / self.pixmap.height()
            self.apply_ratio_size()
            self.lbl.setPixmap(self.pixmap)
            
            filename = f"img_{int(time.time()*1000)}.jpg"
            path = os.path.join(SAVE_DIR, filename)
            with open(path, "wb") as f: f.write(data)
            self.source = path
            self.save_prompt_to_jsonl(filename, self.prompt)
        except: pass

    def fetch(self):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(self.source, headers=headers, timeout=10)
            if r.status_code == 200:
                self.pixmap = QPixmap()
                self.pixmap.loadFromData(r.content)
                if not self.pixmap.isNull():
                    self.ratio = self.pixmap.width() / self.pixmap.height()
                self.apply_ratio_size()
                self.lbl.setPixmap(self.pixmap)
                
                filename = f"img_{int(time.time()*1000)}.jpg"
                path = os.path.join(SAVE_DIR, filename)
                with open(path, "wb") as f: f.write(r.content)
                self.source = path
                self.save_prompt_to_jsonl(filename, self.prompt)
        except: pass

    def save_prompt_to_jsonl(self, filename, prompt):
        if not prompt: return
        with open(PROMPTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"file": filename, "prompt": prompt}) + "\n")

    def trigger_glow(self):
        if self.is_hidden: return
        self.glow_anim = QPropertyAnimation(self.blur_effect, b"blurRadius", self)
        self.glow_anim.setDuration(1000)
        self.glow_anim.setStartValue(10)
        self.glow_anim.setEndValue(0)
        self.glow_anim.setEasingCurve(QEasingCurve.OutQuad)
        self.glow_anim.start()

    def set_hide(self, hide):
        self.is_hidden = hide
        self.blur_effect.setBlurRadius(40 if hide else 0)

    def toggle_hide(self):
        self.set_hide(not self.is_hidden)

    def download_img(self):
        if self.pixmap:
            path = os.path.join(os.path.expanduser("~"), "Downloads", f"GenApp_{int(time.time())}.jpg")
            self.pixmap.save(path, "JPG")

    def open_folder(self):
        if os.path.exists(SAVE_DIR):
            if sys.platform == "win32": os.startfile(SAVE_DIR)
            else: subprocess.run(["xdg-open", SAVE_DIR])

    def enterEvent(self, event):
        if not self.is_hidden:
            self.lbl.setStyleSheet("border: 1px solid #0078D4; background-color: #FFFFFF;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.lbl.setStyleSheet("border: none; background-color: transparent;")
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_hidden:
            self.press_timer.start(400)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.press_timer.stop()
        self.request_preview.emit(None, False)
        super().mouseReleaseEvent(event)

    def long_press_action(self):
        if self.pixmap:
            self.request_preview.emit(self.pixmap, True)

    def show_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #252525; color: #FFFFFF; border: 1px solid #333333; padding: 5px; border-radius: 4px; }
            QMenu::item:selected { background-color: #404040; }
        """)
        down_act = QAction("Download to Downloads", self)
        down_act.triggered.connect(self.download_img)
        open_act = QAction("Open Folder", self)
        open_act.triggered.connect(self.open_folder)
        hide_txt = "Unhide Image" if self.is_hidden else "Hide Image"
        hide_act = QAction(hide_txt, self)
        hide_act.triggered.connect(self.toggle_hide)
        regen_act = QAction("Generate Again", self)
        regen_act.triggered.connect(lambda: self.request_regen.emit(self.prompt))
        
        gen_img_act = QAction("Generate with Image", self)
        gen_img_act.triggered.connect(lambda: self.request_gen_with_image.emit(self.pixmap, self.prompt))
        
        del_act = QAction("Delete", self)
        del_act.triggered.connect(lambda: self.request_delete.emit(self))
        menu.addAction(down_act)
        menu.addAction(open_act)
        menu.addAction(hide_act)
        menu.addAction(regen_act)
        menu.addAction(gen_img_act)
        menu.addSeparator()
        menu.addAction(del_act)
        menu.exec(QCursor.pos())

class FreeGenApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1100, 660) # Default size
        self.dragPos = QPoint()
        self.active_workers = []
        self.prompt_history = []
        self.history_idx = -1
        self.cooldown = 0
        self.is_all_hidden = False
        self.upload_image_base64 = None
        self.overlay = None
        self.all_items = []
        self.history_items = []
        self.loaded_count = 0
        self.chunk_size = 99999
        self.col_heights = []
        
        self.settings = QSettings("MatrixNote", "ImageGen")
        
        self.loader_thread = ImageLoaderThread()
        self.loader_thread.image_loaded.connect(self.on_image_loaded)
        
        icon_path = "icon.ico"
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.setup()
        self.style()
        self.load_history()
        self.check_startup_cooldown()
        # Fix for the 'small images' startup bug
        QTimer.singleShot(200, lambda: self.update_column_count(12 if self.isMaximized() else 4))
        QTimer.singleShot(500, lambda: self.update_column_count(12 if self.isMaximized() else 4))

    def setup(self):
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        self.main_layout = QVBoxLayout(central)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.main_layout.addWidget(CustomTitleBar(self))

        self.ui_container = QWidget()
        self.ui_container_layout = QVBoxLayout(self.ui_container)
        self.ui_container_layout.setContentsMargins(10, 10, 10, 0)
        self.ui_container_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.StyledPanel)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(45)
        # self.scroll.verticalScrollBar().valueChanged.connect(self.on_scroll) # Disabled lazy loading
        self.scroll.setObjectName("MainScroll")
        
        self.grid_widget = QWidget()
        self.grid_widget.setObjectName("GridWidget")
        self.grid_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        
        self.masonry_layout = QHBoxLayout(self.grid_widget)
        self.masonry_layout.setSpacing(0)
        self.masonry_layout.setContentsMargins(0, 0, 0, 0)
        self.masonry_layout.setAlignment(Qt.AlignTop | Qt.AlignCenter)
        
        # Initial columns (Will auto-update in resizeEvent)
        self.cols = []
        for _ in range(4):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.setContentsMargins(0, 0, 0, 0)
            col.setAlignment(Qt.AlignTop)
            self.masonry_layout.addLayout(col)
            self.cols.append(col)
        
        self.scroll.setWidget(self.grid_widget)
        self.ui_container_layout.addWidget(self.scroll, 1)

        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("BottomBar")
        bar_layout = QHBoxLayout(self.bottom_bar)
        bar_layout.setContentsMargins(0, 10, 0, 15)
        bar_layout.setSpacing(12)

        # Hide button removed as requested
        
        self.image_frame = QPushButton()
        self.image_frame.setFixedSize(75, 75)
        self.image_frame.setObjectName("ImageFrame")
        self.image_frame.clicked.connect(self.select_image)
        
        self.image_frame_layout = QVBoxLayout(self.image_frame)
        self.image_frame_layout.setContentsMargins(5, 5, 5, 5)
        
        self.img_preview = QLabel()
        self.img_preview.setAlignment(Qt.AlignCenter)
        self.img_preview.setScaledContents(False)
        self.image_frame_layout.addWidget(self.img_preview)

        self.clear_img_btn = QPushButton("×", self.image_frame)
        self.clear_img_btn.setFixedSize(20, 20)
        self.clear_img_btn.move(55, 0)
        self.clear_img_btn.setObjectName("ClearImgBtn")
        self.clear_img_btn.clicked.connect(self.clear_image_selection)
        self.clear_img_btn.hide()
        
        bar_layout.addWidget(self.image_frame)

        self.input = PromptArea()
        self.input.submitted.connect(self.generate)
        self.input.history_nav.connect(self.navigate_history)
        self.input.image_pasted.connect(self.handle_pasted_image)
        bar_layout.addWidget(self.input, 1)

        right_controls = QVBoxLayout()
        right_controls.setSpacing(5)

        self.btn = QPushButton("Generate")
        self.btn.setFixedWidth(100)
        self.btn.setFixedHeight(45)
        self.btn.clicked.connect(self.generate)
        right_controls.addWidget(self.btn)

        self.ratio = QComboBox()
        self.ratio.addItems(["1:1", "4:3", "3:4", "16:9", "9:16"])
        self.ratio.setFixedWidth(100)
        self.ratio.setFixedHeight(30)
        right_controls.addWidget(self.ratio)

        bar_layout.addLayout(right_controls)
        self.ui_container_layout.addWidget(self.bottom_bar)
        self.main_layout.addWidget(self.ui_container, 1)

        self.grip = QSizeGrip(self)
        self.main_layout.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)

        self.main_layout.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)

        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.timeout.connect(self.update_cooldown)

    def update_column_count(self, num_cols):
        """Re-distributes images into the specified number of columns dynamically."""
        if not hasattr(self, 'cols') or len(self.cols) == num_cols:
            return
            
        # Delete old column layouts (items are not destroyed, just removed from layout)
        while self.masonry_layout.count():
            item = self.masonry_layout.takeAt(0)
            layout = item.layout()
            if layout:
                while layout.count():
                    layout.takeAt(0) # Detach widget from old column
                layout.deleteLater()
                
        self.cols = []
        self.col_heights = [0] * num_cols
        for _ in range(num_cols):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.setContentsMargins(0, 0, 0, 0)
            col.setAlignment(Qt.AlignTop)
            self.masonry_layout.addLayout(col)
            self.cols.append(col)
            
        # Re-add all cards (all_items is sorted Newest first)
        for card in self.all_items:
            self.add_to_masonry(card, at_top=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            # Safely check if overlay exists and is not None
            if getattr(self, 'overlay', None) is not None:
                try:
                    if self.overlay.parent(): 
                        self.overlay.setGeometry(self.scroll.geometry())
                except:
                    pass
            
            # Dynamic Columns: 12 columns if truly maximized, 4 columns in normal mode
            num_cols = 12 if (self.isMaximized() and self.width() > 1000) else 4
            self.update_column_count(num_cols)
            
            # CRITICAL FIX FOR INITIAL STARTUP LAYOUT BUG:
            # We use self.width() instead of self.scroll.width() because on startup
            # layouts are not resolved yet and scroll.width() returns inaccurate small values.
            if hasattr(self, 'cols') and self.cols:
                # 20px container margins + 2px borders + 14px scrollbar + 4px safety
                available_w = self.width() - 40
                
                new_w = max(100, available_w // num_cols)
                for card in self.all_items:
                    card.update_width(new_w)

    def toggle_max_normal(self):
        if self.isMaximized(): self.showNormal()
        else: self.showMaximized()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_F9:
            if self.isMinimized() or not self.isVisible() or not self.isActiveWindow():
                self.showNormal()
                self.activateWindow()
            else:
                self.showMinimized()
        elif event.key() == Qt.Key_H and not self.input.hasFocus():
            if hasattr(self, 'hide_all_btn'):
                self.hide_all_btn.click()
        super().keyPressEvent(event)

    def style(self):
        self.setStyleSheet("""
            #Central { 
                background-color: #181818; 
                border-radius: 12px;
                border: 1px solid #333333;
            }
            QMainWindow { background-color: transparent; }
            
            /* Title Bar */
            #TitleBar { 
                background-color: #252525; 
                border-bottom: 1px solid #333333; 
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }
            
            /* Mac Traffic Lights */
            #MacCloseBtn { background-color: #FF5F56; border-radius: 6px; border: 1px solid #E0443E; }
            #MacCloseBtn:hover { background-color: #FF3B30; }
            #MacMinBtn { background-color: #FFBD2E; border-radius: 6px; border: 1px solid #DEA123; }
            #MacMinBtn:hover { background-color: #E8A81C; }
            #MacMaxBtn { background-color: #27C93F; border-radius: 6px; border: 1px solid #1AAB29; }
            #MacMaxBtn:hover { background-color: #1BA028; }
            
            #MainScroll { 
                background-color: #1E1E1E; 
                border: 1px solid #333333; 
                border-radius: 8px; 
            }
            #GridWidget { background-color: #1E1E1E; }
            
            /* Custom Styled Scrollbar */
            QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }
            QScrollBar::handle:vertical { background: #555555; min-height: 30px; border-radius: 3px; margin: 0px; }
            QScrollBar::handle:vertical:hover { background: #777777; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            
            /* Bottom Bar */
            #BottomBar { background-color: transparent; margin: 5px 0px 5px 0px; }
            
            #PromptInput {
                background-color: #252525;
                color: #FFFFFF;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
            }
            #PromptInput:focus { border-color: #0078D4; background-color: #1A1A1A; }

            QComboBox {
                background-color: #252525;
                color: #FFFFFF;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QComboBox:hover { background-color: #303030; border-color: #444444; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8E8E93;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #252525;
                color: #FFFFFF;
                selection-background-color: #0078D4;
                selection-color: #FFFFFF;
                border: 1px solid #333333;
                border-radius: 6px;
                outline: 0px;
            }
            
            QPushButton {
                background-color: #333333;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #444444; }
            QPushButton:disabled { background-color: #222222; color: #555555; }
            
            #HideAllBtn {
                background-color: #252525;
                color: #FFFFFF;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #333333;
            }
            #HideAllBtn:hover { background-color: #303030; }
            #HideAllBtn:checked {
                background-color: #FFFFFF;
                color: #1C1C1E;
                border: 1px solid #FFFFFF;
            }

            #ImageFrame {
                background-color: #252525;
                border: 2px dashed #444444;
                border-radius: 8px;
            }
            #ImageFrame:hover { border-color: #0078D4; background-color: #2a2a2a; }
            
            #ClearImgBtn {
                background-color: rgba(0, 0, 0, 180);
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: bold;
                line-height: 20px;
            }
            #ClearImgBtn:hover { background-color: #FF5F56; }

            #ImageLabel { background-color: #222222; }
        """)

    def load_history(self):
        if not os.path.exists(SAVE_DIR): return
        prompts_map = {}
        if os.path.exists(PROMPTS_FILE):
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        prompts_map[entry["file"]] = entry["prompt"]
                    except: pass
        files = [f for f in os.listdir(SAVE_DIR) if f.lower().endswith((".jpg", ".png", ".webp"))]
        # Sort by filename descending (since filenames start with 'img_' + timestamp)
        files.sort(reverse=True)
        
        self.history_items = [{"file": f, "prompt": prompts_map.get(f, "")} for f in files]
        self.load_next_chunk()

    def load_next_chunk(self):
        if self.loaded_count >= len(self.history_items): return
        
        end_idx = min(self.loaded_count + self.chunk_size, len(self.history_items))
        chunk = self.history_items[self.loaded_count:end_idx]
        
        new_cards = []
        for item in chunk:
            path = os.path.join(SAVE_DIR, item["file"])
            card = self.create_card(path, "1:1", prompt=item["prompt"])
            self.all_items.append(card)
            self.add_to_masonry(card, at_top=False)
            new_cards.append(card)
            
        self.loaded_count = end_idx
        self.loader_thread.add_tasks(new_cards)
        
    def on_scroll(self, value):
        max_val = self.scroll.verticalScrollBar().maximum()
        if max_val > 0 and value >= max_val - 1000:
            self.load_next_chunk()

    def on_image_loaded(self, card, qimage):
        pixmap = QPixmap.fromImage(qimage)
        card.set_pixmap(pixmap)

    def navigate_history(self, delta):
        if not self.prompt_history: return
        self.history_idx += delta
        if self.history_idx < 0: self.history_idx = 0
        if self.history_idx >= len(self.prompt_history):
            self.history_idx = len(self.prompt_history)
            self.input.clear()
            return
        self.input.setPlainText(self.prompt_history[self.history_idx])

    def toggle_all_hidden(self, checked):
        self.is_all_hidden = checked
        self.hide_all_btn.setText("Show" if checked else "Hide")
        for card in self.all_items:
            card.set_hide(checked)

    def clear_image_selection(self, event=None):
        self.upload_image_base64 = None
        self.img_preview.clear()
        self.clear_img_btn.hide()
        self.image_frame.setStyleSheet("") # Reset to dashed style

    def select_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if file_path:
            pixmap = QPixmap(file_path)
            if pixmap.isNull(): return
            
            # Resize to max 512px like the web app for better performance
            if pixmap.width() > 512 or pixmap.height() > 512:
                pixmap = pixmap.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
            # Convert to base64 JPG
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            pixmap.save(buf, "JPG", 80)
            base64_data = ba.toBase64().data().decode()
            self.upload_image_base64 = f"data:image/jpeg;base64,{base64_data}"
            
            # Update UI: Use KeepAspectRatio to prevent squeezing
            self.img_preview.setPixmap(pixmap.scaled(65, 65, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.clear_img_btn.show()
            self.image_frame.setStyleSheet("border: 1px solid #444444; border-style: solid;") # Solid border when image present

    def handle_pasted_image(self, image_data):
        if not image_data: return
        if isinstance(image_data, QImage): pixmap = QPixmap.fromImage(image_data)
        elif isinstance(image_data, QPixmap): pixmap = image_data
        else: return
        
        if pixmap.isNull(): return
        
        if pixmap.width() > 512 or pixmap.height() > 512:
            pixmap = pixmap.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        pixmap.save(buf, "JPG", 80)
        base64_data = ba.toBase64().data().decode()
        self.upload_image_base64 = f"data:image/jpeg;base64,{base64_data}"
        
        self.img_preview.setPixmap(pixmap.scaled(65, 65, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.clear_img_btn.show()
        self.image_frame.setStyleSheet("border: 1px solid #444444; border-style: solid;")

    def is_safe_prompt(self, prompt):
        # Normalize: lower case and remove common separators to catch "n a k e d" or "ع ا ر ي ة"
        p = prompt.lower()
        clean_p = p.replace(" ", "").replace("-", "").replace("_", "").replace(".", "").replace(",", "")
        
        # 1. Strict Blocks (NSFW / Pornography)
        # We check both the original prompt and the "clean" version
        strict_forbidden = [
            "nude", "naked", "sex", "porn", "nsfw", "pussy", "dick", "vagina", "penis", "boobs", "breast", "asshole", "anal",
            "عاري", "عارية", "جنس", "بورن", "اباحي", "إباحي", "سكس", "ثدي", "مؤخرة", "كس", "قضيب", "عورات", "منزوعة", "بدون ثياب"
        ]
        
        for word in strict_forbidden:
            if word in p or word in clean_p:
                return False
            
        # 2. Child Safety Blocks (Extremely Strict)
        age_keywords = [
            "child", "kid", "baby", "toddler", "boy", "girl", "young", "teen", "student", "minor", "little", "son", "daughter",
            "طفل", "طفلة", "صغير", "صغيرة", "رضيع", "مراهق", "مراهقة", "طالب", "تلميذ", "قاصر", "بنت", "ولد"
        ]
        age_numbers = [str(i) for i in range(1, 19)] # 1 to 18
        age_keywords.extend(age_numbers)
        # Adding words like "years old", "yo"
        age_keywords.extend(["yearsold", "y.o", "yearold"])
        
        sensitive_context = [
            "undressed", "noclo", "withoutclo", "bikini", "underwear", "lingerie", "naked", "nude", "bra",
            "ass", "butt", "rear", "bottom", "thigh", "backview", "fullbody", "visible", "pose", "style",
            "بدون ملابس", "بدون ثياب", "ملابس داخلية", "بكيني", "عاري", "عارية", "منزوعة", "ستراب", "حمالة",
            "مؤخرة", "ارداف", "أرداف", "فخذ", "ظهر", "جسم كامل", "واضح", "من الخلف"
        ]
        
        # Check against clean_p for collapsed versions as well
        collapsed_context = [ctx.replace(" ", "") for ctx in sensitive_context]
        
        has_age = any(age in p or age in clean_p for age in age_keywords)
        # If age is mentioned, we block ANY of the sensitive context words
        has_context = any(ctx in p or ctx in clean_p for ctx in sensitive_context) or \
                      any(c_ctx in clean_p for c_ctx in collapsed_context)
        
        if has_age and has_context:
            return False
            
        return True

    def generate(self):
        txt = self.input.toPlainText().strip()
        if (not txt and not self.upload_image_base64) or self.cooldown > 0: return
        
        # Safety Check
        if not self.is_safe_prompt(txt):
            self.btn.setText("Safety Error")
            self.btn.setStyleSheet("background-color: #E0443E; color: white;")
            QTimer.singleShot(2000, lambda: self.btn.setText("Generate"))
            QTimer.singleShot(2000, lambda: self.btn.setStyleSheet(""))
            return
            
        if not txt: txt = "." # Fallback for image-only generation
        
        if not self.prompt_history or self.prompt_history[-1] != txt:
            self.prompt_history.append(txt)
        self.history_idx = len(self.prompt_history)
        
        # Hardcoded to 1 to prevent tampering
        batch_size = 1
        self.btn.setEnabled(False)
        
        for _ in range(batch_size):
            worker = APIWorker(txt, self.ratio.currentText(), self.upload_image_base64)
            worker.finished.connect(self.done)
            worker.error.connect(self.fail)
            # Use default argument in lambda to capture the current worker instance
            worker.finished.connect(lambda w=worker: self.active_workers.remove(w) if w in self.active_workers else None)
            worker.error.connect(lambda w=worker: self.active_workers.remove(w) if w in self.active_workers else None)
            self.active_workers.append(worker)
            worker.start()

    def update_cooldown(self):
        self.cooldown -= 1
        if self.cooldown <= 0:
            self.cooldown_timer.stop()
            self.btn.setEnabled(True)
            self.btn.setText("Generate")
        else:
            self.btn.setText(f"Wait ({self.cooldown})")

    def save_cooldown_state(self):
        try:
            self.settings.setValue("end_time", time.time() + self.cooldown)
        except: pass

    def check_startup_cooldown(self):
        try:
            val = self.settings.value("end_time")
            if val is None: return
            remaining = int(float(val) - time.time())
            if remaining > 0:
                self.cooldown = min(remaining, 15) # Cap at 15 to prevent huge jumps from clock bugs
                self.btn.setEnabled(False)
                self.btn.setText(f"Wait ({self.cooldown})")
                self.cooldown_timer.start(1000)
        except: pass

    def done(self, data):
        item = self.create_card(data["url"], data["ratio"], data["prompt"])
        if self.is_all_hidden: item.set_hide(True)
        self.all_items.insert(0, item)
        self.add_to_masonry(item, at_top=True)
        
        QTimer.singleShot(100, lambda: self.scroll.verticalScrollBar().setValue(0))
        QTimer.singleShot(300, item.trigger_glow)
        
        self.cooldown = 15
        self.btn.setText(f"Wait ({self.cooldown})")
        self.save_cooldown_state()
        self.cooldown_timer.start(1000)

    def create_card(self, source, ratio_str, prompt=""):
        card = ImageCard(source, ratio_str, prompt)
        card.request_regen.connect(self.regen_prompt)
        card.request_gen_with_image.connect(self.set_image_and_prompt)
        card.request_delete.connect(self.delete_card)
        card.request_preview.connect(self.toggle_preview)
        
        # Ensure it starts at the correct responsive size using reliable window width
        if hasattr(self, 'cols') and self.cols:
            num_cols = len(self.cols)
            # Use at least 1100 (default width) to avoid 'small images' on startup
            available_w = max(self.width(), 1100) - 40
            new_w = max(100, available_w // num_cols)
            card.update_width(new_w)
        
        return card

    def toggle_preview(self, pixmap, show):
        if not show:
            if hasattr(self, 'overlay') and self.overlay:
                self.overlay.hide()
                self.overlay.deleteLater()
                self.overlay = None
            return

        if show and pixmap:
            # 1. Destroy ANY existing overlay (The Nuclear Fix for Ghosting)
            if hasattr(self, 'overlay') and self.overlay:
                self.overlay.hide()
                self.overlay.deleteLater()
            
            # 2. Grab screen while clean
            screen = QApplication.primaryScreen()
            screen_geo = screen.geometry()
            screenshot = screen.grabWindow(0)
            
            # 3. Create a brand NEW, fresh overlay window
            self.overlay = QFrame()
            self.overlay.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.overlay.setAttribute(Qt.WA_TranslucentBackground)
            self.overlay.setStyleSheet("background: transparent;") # Prevent black flash
            self.overlay.setGeometry(screen_geo)
            
            # 4. Build UI from scratch for this preview
            self.overlay_bg = QLabel(self.overlay)
            self.overlay_bg.setScaledContents(True)
            self.overlay_bg.setGeometry(0, 0, screen_geo.width(), screen_geo.height())
            self.overlay_bg.setPixmap(screenshot)
            
            self.overlay_blur = QGraphicsBlurEffect()
            self.overlay_blur.setBlurRadius(40) # Premium frosted look
            self.overlay_bg.setGraphicsEffect(self.overlay_blur)
            
            layout = QVBoxLayout(self.overlay)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignCenter)
            
            self.preview_lbl = QLabel()
            self.preview_lbl.setAlignment(Qt.AlignCenter)
            target_w = screen_geo.width() - 100
            target_h = screen_geo.height() - 100
            self.preview_lbl.setPixmap(pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            
            self.preview_shadow = QGraphicsDropShadowEffect(blurRadius=60, color=QColor(0,0,0,80), offset=QPoint(0,10))
            self.preview_lbl.setGraphicsEffect(self.preview_shadow)
            
            layout.addWidget(self.preview_lbl)
            self.overlay_bg.show()
            self.overlay_bg.lower()
            
            # 5. Show the clean, new window
            self.overlay.show()
            self.overlay.activateWindow()
            QApplication.processEvents()

    def regen_prompt(self, p):
        self.input.setPlainText(p)

    def set_image_and_prompt(self, pixmap, prompt):
        """Loads a generated image and its prompt back into the UI for further generation."""
        self.input.setPlainText(prompt)
        if pixmap:
            # Update the UI frame preview (matching select_image style)
            self.img_preview.setPixmap(pixmap.scaled(65, 65, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.clear_img_btn.show()
            self.image_frame.setStyleSheet("border: 1px solid #444444; border-style: solid;")
            
            # Convert to base64 JPG with required data URL prefix
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            # Scale to 512 max for consistent performance
            if pixmap.width() > 512 or pixmap.height() > 512:
                p = pixmap.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                p = pixmap
            p.save(buf, "JPG", 80)
            base64_data = ba.toBase64().data().decode()
            self.upload_image_base64 = f"data:image/jpeg;base64,{base64_data}"

    def delete_card(self, card):
        if card in self.all_items:
            self.all_items.remove(card)
        for i, col in enumerate(self.cols):
            if col.indexOf(card) != -1:
                col.removeWidget(card)
                if hasattr(self, 'col_heights') and i < len(self.col_heights):
                    self.col_heights[i] -= card.height()
                card.deleteLater()
                break
        if os.path.exists(card.source):
            try: os.remove(card.source)
            except: pass

    def add_to_masonry(self, item, at_top=True):
        if not hasattr(self, 'col_heights') or len(self.col_heights) != len(self.cols):
            self.col_heights = [0] * len(self.cols)

        min_idx = 0
        min_height = self.col_heights[0]
        for i in range(1, len(self.cols)):
            if self.col_heights[i] < min_height:
                min_height = self.col_heights[i]
                min_idx = i
                
        shortest_col = self.cols[min_idx]
        if at_top: shortest_col.insertWidget(0, item)
        else: shortest_col.addWidget(item)
        self.col_heights[min_idx] += item.height()

    def fail(self, e):
        self.btn.setEnabled(True)
        print(f"Error: {e}")

    def closeEvent(self, event):
        try:
            # Hide and close overlay if it exists
            if hasattr(self, 'overlay'):
                self.overlay.hide()
                self.overlay.close()
            
            # Stop any running loader threads
            if hasattr(self, 'loader_thread'):
                self.loader_thread.is_running = False
                
            # Stop all active generation workers
            for worker in self.active_workers:
                try:
                    worker.stop()
                except:
                    pass
        except:
            pass
            
        # Forced exit to prevent the "worker.wait()" hang and traceback errors
        # This ensures all threads are terminated immediately.
        os._exit(0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Single instance check
    lock_path = os.path.join(SAVE_DIR, "app.lock")
    lock_file = QLockFile(lock_path)
    if not lock_file.tryLock(100):
        # Already running
        sys.exit(0)
        
    window = FreeGenApp()
    window.show()
    sys.exit(app.exec())
