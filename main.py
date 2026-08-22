import sys
import os
import time
import re
import ctypes
import math
import threading
import keyboard
import json
from ctypes import wintypes
from queue import Queue, Empty
from pathlib import Path

import numpy as np
import cv2

# Set DPI awareness FIRST before PyQt application initialization
user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2) # Per-monitor DPI aware
except Exception:
    pass

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QMessageBox, 
                             QSystemTrayIcon, QMenu, QSlider, QPushButton, 
                             QVBoxLayout, QHBoxLayout, QFrame, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QPainter, QPen, QBrush, QMouseEvent

import mss
from rapidocr_onnxruntime import RapidOCR
from deep_translator import GoogleTranslator

# Configuration Defaults (saved/loaded dynamically)
CACHE_FILE = Path.home() / ".capcut_translator_cache.json"
SETTINGS_FILE = Path.home() / ".capcut_translator_settings.json"

def contains_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def get_cursor_pos():
    """Returns cursor position in PHYSICAL screen coordinates."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def get_capcut_rect():
    """Returns CapCut window rect in PHYSICAL screen coordinates."""
    hwnds = []
    def callback(hwnd, extra):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if "剪映" in title or "CapCut" in title:
                    hwnds.append(hwnd)
        return True
    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(CMPFUNC(callback), 0)
    if hwnds:
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnds[0], ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 0 and h > 0:
            return {'left': rect.left, 'top': rect.top, 'width': w, 'height': h}
    return None

def apply_win32_clickthrough(hwnd):
    """Forces Windows OS to pass all mouse clicks straight through this window."""
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception as e:
        print(f"Clickthrough error: {e}")


class TranslatorThread(threading.Thread):
    def __init__(self, cache, on_new_translation_callback):
        super().__init__(daemon=True)
        self.cache = cache
        self.on_new_translation_callback = on_new_translation_callback
        self.queue = Queue()
        self.running = True
        self.in_progress = set()
        self.translator = GoogleTranslator(source='zh-CN', target='en')

    def run(self):
        while self.running:
            try:
                texts = self.queue.get(timeout=0.2)
                if texts:
                    try:
                        results = self.translator.translate_batch(texts)
                        cache_updated = False
                        for i, orig in enumerate(texts):
                            if i < len(results) and results[i]:
                                trans = results[i].strip()
                                self.cache[orig] = trans
                                cache_updated = True
                        if cache_updated:
                            self.on_new_translation_callback()
                    except Exception as e:
                        print(f"Translation Error: {e}")
                    finally:
                        for orig in texts:
                            self.in_progress.discard(orig)
            except Empty:
                pass

    def submit(self, texts):
        to_submit = [t for t in texts if t not in self.in_progress]
        if to_submit:
            for t in to_submit:
                self.in_progress.add(t)
            self.queue.put(to_submit)

    def stop(self):
        self.running = False


class OcrWorker(QThread):
    result_signal = pyqtSignal(list)
    status_signal = pyqtSignal(bool)
    speed_signal = pyqtSignal(int)  # Emits OCR inference time in ms

    def __init__(self, dpr):
        super().__init__()
        self.dpr = dpr
        self.running = True
        self.enabled = False
        self.ocr_engine = RapidOCR()
        
        # Adjustable parameters
        self.lens_radius_phys = 60
        self.ocr_scale = 0.5
        
        self.translation_cache = self.load_cache()
        self.translator_thread = TranslatorThread(self.translation_cache, self.save_cache)
        self.translator_thread.start()

    def load_cache(self):
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.translation_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def toggle(self):
        self.enabled = not self.enabled
        self.status_signal.emit(self.enabled)
        if not self.enabled:
            self.result_signal.emit([])

    def run(self):
        with mss.mss() as sct:
            while self.running:
                if not self.enabled:
                    time.sleep(0.05)
                    continue

                try:
                    capcut_rect = get_capcut_rect()
                    if not capcut_rect:
                        self.result_signal.emit([])
                        time.sleep(0.2)
                        continue

                    cx, cy = get_cursor_pos()
                    cr = capcut_rect
                    
                    if not (cr['left'] <= cx <= cr['left'] + cr['width'] and
                            cr['top'] <= cy <= cr['top'] + cy_height_fix(cr)):
                        self.result_signal.emit([])
                        time.sleep(0.03)
                        continue

                    # Dynamic physics math based on current lens radius setting
                    lens_left = max(cr['left'], cx - self.lens_radius_phys)
                    lens_top = max(cr['top'], cy - self.lens_radius_phys)
                    lens_right = min(cr['left'] + cr['width'], cx + self.lens_radius_phys)
                    lens_bottom = min(cr['top'] + cr['height'], cy + self.lens_radius_phys)
                    lens_w = lens_right - lens_left
                    lens_h = lens_bottom - lens_top

                    if lens_w <= 0 or lens_h <= 0:
                        self.result_signal.emit([])
                        time.sleep(0.03)
                        continue

                    phys_area = {
                        'left': int(lens_left),
                        'top': int(lens_top),
                        'width': int(lens_w),
                        'height': int(lens_h)
                    }

                    start_time = time.time()
                    sct_img = sct.grab(phys_area)
                    img = np.frombuffer(sct_img.rgb, dtype=np.uint8).reshape(
                        sct_img.height, sct_img.width, 3
                    )

                    # --- ACCURACY PIPELINE ---
                    # Step 1: UPSCALE 2x instead of downscaling — more pixels = better OCR
                    #         RapidOCR is fast enough on a small lens crop even at 2x
                    upscaled = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

                    # Step 2: Convert to grayscale and apply CLAHE to boost local contrast
                    #         This makes faint or anti-aliased Chinese characters crisp
                    gray = cv2.cvtColor(upscaled, cv2.COLOR_RGB2GRAY)
                    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
                    enhanced = clahe.apply(gray)

                    # Step 3: Merge back to 3-channel for RapidOCR
                    enhanced_3ch = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)

                    ocr_result, _ = self.ocr_engine(enhanced_3ch)

                    inference_time = int((time.time() - start_time) * 1000)
                    self.speed_signal.emit(inference_time)

                    boxes = []
                    new_texts = []

                    if ocr_result:
                        for points, text, confidence in ocr_result:
                            # Filter low-confidence OCR results (garbage characters)
                            if confidence < 0.55:
                                continue

                            original = text.strip()
                            # Clean up common OCR artifacts before translating
                            original = original.replace('，', ',').replace('。', '.').strip()
                            if not original or not contains_chinese(original):
                                continue

                            # Scale back: upscaled 2x, so divide by 2 to get physical pixels
                            x_phys = min(p[0] for p in points) / 2.0
                            y_phys = min(p[1] for p in points) / 2.0
                            x2_phys = max(p[0] for p in points) / 2.0
                            y2_phys = max(p[1] for p in points) / 2.0

                            abs_x_phys = lens_left + x_phys
                            abs_y_phys = lens_top + y_phys
                            w_phys = x2_phys - x_phys
                            h_phys = y2_phys - y_phys

                            abs_x_log = abs_x_phys / self.dpr
                            abs_y_log = abs_y_phys / self.dpr
                            w_log = w_phys / self.dpr
                            h_log = h_phys / self.dpr

                            cached = self.translation_cache.get(original)
                            if cached:
                                boxes.append({
                                    "original": original,
                                    "translated": cached,
                                    "x_log": abs_x_log, "y_log": abs_y_log,
                                    "w_log": w_log, "h_log": h_log,
                                    "x_phys": abs_x_phys, "y_phys": abs_y_phys,
                                    "w_phys": w_phys, "h_phys": h_phys
                                })
                            else:
                                new_texts.append(original)

                    if new_texts:
                        self.translator_thread.submit(new_texts)

                    self.result_signal.emit(boxes)

                except Exception as e:
                    print(f"OCR Error: {e}")
                    time.sleep(0.02)

    def stop(self):
        self.running = False
        self.translator_thread.stop()

def cy_height_fix(cr):
    return cr['height']


class OverlayWindow(QWidget):
    def __init__(self, dpr, screen_size):
        super().__init__()
        self.dpr = dpr
        self.screen_size = screen_size
        self.active_labels = {}
        self.current_cursor_phys = (0, 0)
        self.is_enabled = False
        
        # User dynamic settings
        self.lens_radius_phys = 60
        self.font_size = 9

        self.initUI()

        self.worker = OcrWorker(dpr)
        self.worker.result_signal.connect(self.update_translations)
        self.worker.status_signal.connect(self.set_active_status)
        self.worker.start()

        # 60 FPS update loop
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self.on_ui_tick)
        self.ui_timer.start()

        keyboard.add_hotkey('alt+t', self.worker.toggle)

    def initUI(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(0, 0, self.screen_size.width(), self.screen_size.height())

        hwnd = int(self.winId())
        apply_win32_clickthrough(hwnd)

    def on_ui_tick(self):
        self.current_cursor_phys = get_cursor_pos()
        cx_p, cy_p = self.current_cursor_phys

        # Instant pruning outside radius
        to_delete = []
        for key, (label, bx_p, by_p, bw_p, bh_p) in list(self.active_labels.items()):
            box_cx_p = bx_p + bw_p / 2
            box_cy_p = by_p + bh_p / 2
            dist = math.hypot(box_cx_p - cx_p, box_cy_p - cy_p)
            
            if dist > self.lens_radius_phys or not self.is_enabled:
                label.deleteLater()
                to_delete.append(key)

        for key in to_delete:
            del self.active_labels[key]

        self.update()

    def paintEvent(self, event):
        if not self.is_enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx_phys, cy_phys = self.current_cursor_phys
        cx_log = int(cx_phys / self.dpr)
        cy_log = int(cy_phys / self.dpr)
        radius_log = int(self.lens_radius_phys / self.dpr)

        # 1. Lens translucent mask
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(10, 15, 25, 45)))
        painter.drawEllipse(QPoint(cx_log, cy_log), radius_log, radius_log)

        # 2. Outer cyber ring (Neon Cyan)
        pen = QPen(QColor(0, 240, 255, 200), 2, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPoint(cx_log, cy_log), radius_log, radius_log)

        # 3. Reticle point
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 240, 255, 180)))
        painter.drawEllipse(QPoint(cx_log, cy_log), 3, 3)

    def set_active_status(self, enabled):
        self.is_enabled = enabled
        if not enabled:
            for label, _, _, _, _ in self.active_labels.values():
                label.deleteLater()
            self.active_labels.clear()
        self.update()

    def update_translations(self, boxes):
        if not self.is_enabled:
            return

        cx_p, cy_p = self.current_cursor_phys
        new_keys = set()

        for box in boxes:
            text = box.get("translated", "")
            if not text:
                continue

            bx_p, by_p = box["x_phys"], box["y_phys"]
            bw_p, bh_p = box["w_phys"], box["h_phys"]
            box_cx_p = bx_p + bw_p / 2
            box_cy_p = by_p + bh_p / 2
            
            if math.hypot(box_cx_p - cx_p, box_cy_p - cy_p) > self.lens_radius_phys:
                continue

            bx_l, by_l = int(box["x_log"]), int(box["y_log"])
            bw_l, bh_l = int(box["w_log"]), int(box["h_log"])

            key = f"{bx_l}_{by_l}_{text}"
            new_keys.add(key)

            if key not in self.active_labels:
                label = QLabel(text, self)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                label.setWordWrap(True)
                label.setStyleSheet(f"""
                    QLabel {{
                        background-color: rgba(18, 20, 28, 235);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 240, 255, 150);
                        padding: 3px 6px;
                        border-radius: 4px;
                        font-weight: bold;
                    }}
                """)
                label.setFont(QFont("Segoe UI", self.font_size, QFont.Weight.Bold))
                
                label.adjustSize()
                lbl_w = label.width()
                lbl_h = label.height()
                
                new_x = bx_l + (bw_l - lbl_w) // 2
                new_y = by_l + (bh_l - lbl_h) // 2
                
                label.setGeometry(new_x, new_y, lbl_w, lbl_h)
                label.show()
                self.active_labels[key] = (label, bx_p, by_p, bw_p, bh_p)

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait()
        keyboard.unhook_all()
        event.accept()


class ConsoleWindow(QWidget):
    """Bespoke cyber-themed glassmorphism Control Panel for Translation Lens."""
    def __init__(self, overlay_window):
        super().__init__()
        self.overlay = overlay_window
        self.worker = overlay_window.worker
        
        self.drag_position = QPoint()
        self.initUI()
        
        # Listen to status toggles
        self.worker.status_signal.connect(self.update_toggle_btn)
        self.worker.speed_signal.connect(self.update_speed_stat)
        
        # Load user settings if any
        self.load_settings()

    def initUI(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 480)

        # Add drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)

        # Main styling stylesheet
        self.setStyleSheet("""
            QWidget#MainFrame {
                background-color: #0F1016;
                border: 1px solid rgba(0, 240, 255, 120);
                border-radius: 16px;
            }
            QLabel {
                color: #A3A6B4;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel#Title {
                color: #00F0FF;
                font-size: 16px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QLabel#Subtitle {
                color: #515465;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton#ToggleBtn {
                background-color: rgba(20, 24, 35, 220);
                color: #A3A6B4;
                border: 1px solid rgba(163, 166, 180, 50);
                border-radius: 12px;
                font-size: 13px;
                font-weight: bold;
                padding: 12px;
            }
            QPushButton#ToggleBtn[active="true"] {
                background-color: rgba(0, 240, 255, 20);
                color: #00F0FF;
                border: 1px solid #00F0FF;
            }
            QPushButton#CloseBtn {
                background-color: transparent;
                color: #515465;
                border: none;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton#CloseBtn:hover {
                color: #FF5A5A;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #1A1C24;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #00F0FF;
                border: 1px solid #00F0FF;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QFrame#Separator {
                background-color: rgba(0, 240, 255, 30);
            }
        """)

        # Main Layout Structure
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.frame = QFrame(self)
        self.frame.setObjectName("MainFrame")
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(20, 15, 20, 20)

        # --- HEADER ROW ---
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("CYBER LENS", self)
        title.setObjectName("Title")
        subtitle = QLabel("CAPCUT LIVE TRANSLATION CONSOLE", self)
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        
        close_btn = QPushButton("✕", self)
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        frame_layout.addLayout(header)

        # Separator Line
        sep = QFrame(self)
        sep.setObjectName("Separator")
        sep.setFixedHeight(1)
        frame_layout.addWidget(sep)
        frame_layout.addSpacing(15)

        # --- LARGE TOGGLE BUTTON ---
        self.toggle_btn = QPushButton("ACTIVATE TRANSLATION LENS", self)
        self.toggle_btn.setObjectName("ToggleBtn")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setProperty("active", "false")
        self.toggle_btn.clicked.connect(self.worker.toggle)
        frame_layout.addWidget(self.toggle_btn)
        frame_layout.addSpacing(20)

        # --- SLIDERS ZONE ---
        # 1. Lens Radius
        radius_layout = QVBoxLayout()
        radius_head = QHBoxLayout()
        radius_lbl = QLabel("Lens Diameter (Range)", self)
        radius_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        self.radius_val_lbl = QLabel("60px", self)
        self.radius_val_lbl.setStyleSheet("color: #00F0FF; font-weight: bold;")
        radius_head.addWidget(radius_lbl)
        radius_head.addWidget(self.radius_val_lbl)
        radius_layout.addLayout(radius_head)
        
        self.radius_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.radius_slider.setMinimum(30)
        self.radius_slider.setMaximum(150)
        self.radius_slider.setValue(60)
        self.radius_slider.valueChanged.connect(self.on_radius_changed)
        radius_layout.addWidget(self.radius_slider)
        frame_layout.addLayout(radius_layout)
        frame_layout.addSpacing(15)

        # 2. Font Size
        font_layout = QVBoxLayout()
        font_head = QHBoxLayout()
        font_lbl = QLabel("English Font Size", self)
        font_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        self.font_val_lbl = QLabel("9pt", self)
        self.font_val_lbl.setStyleSheet("color: #00F0FF; font-weight: bold;")
        font_head.addWidget(font_lbl)
        font_head.addWidget(self.font_val_lbl)
        font_layout.addLayout(font_head)
        
        self.font_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.font_slider.setMinimum(6)
        self.font_slider.setMaximum(16)
        self.font_slider.setValue(9)
        self.font_slider.valueChanged.connect(self.on_font_changed)
        font_layout.addWidget(self.font_slider)
        frame_layout.addLayout(font_layout)
        frame_layout.addSpacing(20)

        # --- STATS / QUICK HELP PANEL ---
        stats_frame = QFrame(self)
        stats_frame.setStyleSheet("""
            QFrame {
                background-color: #161722;
                border: 1px solid rgba(0, 240, 255, 20);
                border-radius: 10px;
            }
            QLabel {
                font-size: 11px;
            }
        """)
        stats_layout = QVBoxLayout(stats_frame)
        stats_layout.setContentsMargins(15, 12, 15, 12)
        
        shortcut_lbl = QLabel("💡 <b>Global Shortcut:</b> Press <b>Alt + T</b> to toggle anywhere", self)
        shortcut_lbl.setStyleSheet("color: #00F0FF;")
        stats_layout.addWidget(shortcut_lbl)
        
        self.speed_lbl = QLabel("⚡ <b>Inference Speed:</b> Idle", self)
        stats_layout.addWidget(self.speed_lbl)
        
        frame_layout.addWidget(stats_frame)
        main_layout.addWidget(self.frame)

    # --- SETTINGS MANAGEMENT ---
    def load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                    radius = settings.get("lens_radius", 60)
                    font_size = settings.get("font_size", 9)
                    
                    self.radius_slider.setValue(radius)
                    self.font_slider.setValue(font_size)
                    self.on_radius_changed(radius)
                    self.on_font_changed(font_size)
        except Exception:
            pass

    def save_settings(self):
        try:
            settings = {
                "lens_radius": self.radius_slider.value(),
                "font_size": self.font_slider.value()
            }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # --- EVENT HANDLERS ---
    def on_radius_changed(self, value):
        self.radius_val_lbl.setText(f"{value}px")
        # Update overlay variables instantly
        self.overlay.lens_radius_phys = value
        self.worker.lens_radius_phys = value
        self.save_settings()

    def on_font_changed(self, value):
        self.font_val_lbl.setText(f"{value}pt")
        # Update overlay font size instantly
        self.overlay.font_size = value
        self.save_settings()

    def update_toggle_btn(self, enabled):
        if enabled:
            self.toggle_btn.setText("LENS ACTIVE (Alt+T)")
            self.toggle_btn.setProperty("active", "true")
        else:
            self.toggle_btn.setText("ACTIVATE TRANSLATION LENS")
            self.toggle_btn.setProperty("active", "false")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)

    def update_speed_stat(self, ms):
        self.speed_lbl.setText(f"⚡ <b>Inference Speed:</b> {ms}ms (DML GPU Accelerated)")

    # Custom Drag Movement for Frameless Window
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frame_pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def frame_pos(self):
        return self.pos()


class TranslationApp:
    def __init__(self, app_instance):
        self.app = app_instance
        self.app.setQuitOnLastWindowClosed(False)
        
        # Visual Icon Config
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#00F0FF"))
        self.icon = QIcon(pixmap)
        
        # System Tray Icon Setup
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon)
        self.tray.setToolTip("CapCut Cyber Lens")
        self.tray.setVisible(True)
        
        self.menu = QMenu()
        show_action = self.menu.addAction("Show Control Console")
        show_action.triggered.connect(self.show_console)
        self.menu.addSeparator()
        quit_action = self.menu.addAction("Exit Translator")
        quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(self.menu)

        screen = self.app.primaryScreen()
        dpr = screen.devicePixelRatio()
        
        self.overlay = OverlayWindow(dpr, screen.size())
        self.overlay.show()

        # Load Console Window
        self.console = ConsoleWindow(self.overlay)
        self.console.show()

        self.tray.activated.connect(self.on_tray_activated)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_console()

    def show_console(self):
        self.console.show()
        self.console.activateWindow()

    def quit_app(self):
        self.console.close()
        self.overlay.close()
        self.app.quit()


if __name__ == '__main__':
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    app = QApplication(sys.argv)
    translator_app = TranslationApp(app)
    sys.exit(app.exec())
