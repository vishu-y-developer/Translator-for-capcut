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

from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMessageBox, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QPainter, QPen, QBrush

import mss
from rapidocr_onnxruntime import RapidOCR
from deep_translator import GoogleTranslator

FONT_SIZE = 9
OCR_SCALE = 0.5        # Downscale crop for speed
LENS_RADIUS_PHYS = 60  # Reduced radius even more for a tighter, highly-focused lens

# Persistent cache path in the user's home directory
CACHE_FILE = Path.home() / ".capcut_translator_cache.json"

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
        # Exclude from screen capture so OCR never sees its own overlay
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
        self.in_progress = set()  # Tracks text currently being translated to prevent API flooding
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
                        # Remove from in-progress so it can be retried if it failed
                        for orig in texts:
                            self.in_progress.discard(orig)
            except Empty:
                pass

    def submit(self, texts):
        # Only submit texts that are not currently translating
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

    def __init__(self, dpr):
        super().__init__()
        self.dpr = dpr
        self.running = True
        self.enabled = False
        self.ocr_engine = RapidOCR()
        
        # Load persistent translation cache
        self.translation_cache = self.load_cache()
        
        self.translator_thread = TranslatorThread(self.translation_cache, self.save_cache)
        self.translator_thread.start()

    def load_cache(self):
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading cache: {e}")
        return {}

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.translation_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving cache: {e}")

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
                    capcut_rect = get_capcut_rect() # Physical coords
                    if not capcut_rect:
                        self.result_signal.emit([])
                        time.sleep(0.2)
                        continue

                    cx, cy = get_cursor_pos() # Physical coords
                    cr = capcut_rect
                    
                    if not (cr['left'] <= cx <= cr['left'] + cr['width'] and
                            cr['top'] <= cy <= cr['top'] + cr['height']):
                        self.result_signal.emit([])
                        time.sleep(0.03)
                        continue

                    # Physical crop bounds
                    lens_left = max(cr['left'], cx - LENS_RADIUS_PHYS)
                    lens_top = max(cr['top'], cy - LENS_RADIUS_PHYS)
                    lens_right = min(cr['left'] + cr['width'], cx + LENS_RADIUS_PHYS)
                    lens_bottom = min(cr['top'] + cr['height'], cy + LENS_RADIUS_PHYS)
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

                    sct_img = sct.grab(phys_area)
                    img = np.frombuffer(sct_img.rgb, dtype=np.uint8).reshape(
                        sct_img.height, sct_img.width, 3
                    )

                    small = cv2.resize(img, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_AREA)
                    ocr_result, _ = self.ocr_engine(small)

                    boxes = []
                    new_texts = []

                    if ocr_result:
                        for points, text, confidence in ocr_result:
                            original = text.strip()
                            if not original or not contains_chinese(original):
                                continue

                            # Coordinates in physical screen space
                            x_phys = min(p[0] for p in points) / OCR_SCALE
                            y_phys = min(p[1] for p in points) / OCR_SCALE
                            x2_phys = max(p[0] for p in points) / OCR_SCALE
                            y2_phys = max(p[1] for p in points) / OCR_SCALE

                            abs_x_phys = lens_left + x_phys
                            abs_y_phys = lens_top + y_phys
                            w_phys = x2_phys - x_phys
                            h_phys = y2_phys - y_phys

                            # CONVERT TO LOGICAL COORDINATES FOR PYQT!
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

class OverlayWindow(QWidget):
    def __init__(self, dpr, screen_size):
        super().__init__()
        self.dpr = dpr
        self.screen_size = screen_size # Logical size
        self.active_labels = {}
        self.current_cursor_phys = (0, 0)
        self.is_enabled = False
        
        self.status_label = None
        self.status_timer = QTimer()
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(self.hide_status)
        
        self.initUI()

        self.worker = OcrWorker(dpr)
        self.worker.result_signal.connect(self.update_translations)
        self.worker.status_signal.connect(self.show_status)
        self.worker.start()

        # 60 FPS Cursor tracking & fast distance pruning
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self.on_ui_tick)
        self.ui_timer.start()

        keyboard.add_hotkey('ctrl+t', self.worker.toggle)

    def initUI(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        self.setGeometry(0, 0, self.screen_size.width(), self.screen_size.height())

        # Apply Win32 WS_EX_TRANSPARENT for 100% mouse click-through
        hwnd = int(self.winId())
        apply_win32_clickthrough(hwnd)

        # Status notification banner
        self.status_label = QLabel(self)
        self.status_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedWidth(320)
        self.status_label.setFixedHeight(45)
        self.status_label.move((self.screen_size.width() - 320) // 2, 30)
        self.status_label.hide()

    def on_ui_tick(self):
        self.current_cursor_phys = get_cursor_pos() # Physical coords
        cx_p, cy_p = self.current_cursor_phys

        # Instant 16ms Prune based on PHYSICAL distance
        to_delete = []
        for key, (label, bx_p, by_p, bw_p, bh_p) in list(self.active_labels.items()):
            box_cx_p = bx_p + bw_p / 2
            box_cy_p = by_p + bh_p / 2
            dist = math.hypot(box_cx_p - cx_p, box_cy_p - cy_p)
            
            if dist > LENS_RADIUS_PHYS or not self.is_enabled:
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

        # Convert cursor physical coords to logical for painter
        cx_phys, cy_phys = self.current_cursor_phys
        cx_log = int(cx_phys / self.dpr)
        cy_log = int(cy_phys / self.dpr)
        radius_log = int(LENS_RADIUS_PHYS / self.dpr)

        # Subtle dark tint
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(15, 20, 30, 40)))
        painter.drawEllipse(QPoint(cx_log, cy_log), radius_log, radius_log)

        # Outer neon cyan ring
        pen = QPen(QColor(0, 210, 255, 220), 2, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPoint(cx_log, cy_log), radius_log, radius_log)

        # Center reticle dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 210, 255, 180)))
        painter.drawEllipse(QPoint(cx_log, cy_log), 3, 3)

    def show_status(self, enabled):
        self.is_enabled = enabled
        if enabled:
            self.status_label.setText("🔍 Translation Lens: ON")
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(0, 180, 80, 230);
                    color: white;
                    border-radius: 12px;
                    padding: 6px;
                }
            """)
        else:
            self.status_label.setText("Translation Lens: OFF")
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(210, 40, 40, 230);
                    color: white;
                    border-radius: 12px;
                    padding: 6px;
                }
            """)
            for label, _, _, _, _ in self.active_labels.values():
                label.deleteLater()
            self.active_labels.clear()

        self.status_label.show()
        self.status_label.raise_()
        self.status_timer.start(1500)
        self.update()

    def hide_status(self):
        self.status_label.hide()

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
            
            if math.hypot(box_cx_p - cx_p, box_cy_p - cy_p) > LENS_RADIUS_PHYS:
                continue

            bx_l, by_l = int(box["x_log"]), int(box["y_log"])
            bw_l, bh_l = int(box["w_log"]), int(box["h_log"])

            key = f"{bx_l}_{by_l}_{text}"
            new_keys.add(key)

            if key not in self.active_labels:
                label = QLabel(text, self)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                label.setWordWrap(True)
                label.setStyleSheet("""
                    QLabel {
                        background-color: rgba(20, 24, 35, 235);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 210, 255, 180);
                        padding: 2px 4px;
                        border-radius: 4px;
                        font-weight: bold;
                    }
                """)
                label.setFont(QFont("Arial", FONT_SIZE))
                
                # Dynamic auto-resizing so translation NEVER cuts off!
                label.adjustSize()
                lbl_w = label.width()
                lbl_h = label.height()
                
                # Center the expanded English label over the original Chinese box
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

class TranslationApp:
    def __init__(self, app_instance):
        self.app = app_instance
        self.app.setQuitOnLastWindowClosed(False)
        
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("cyan"))
        self.icon = QIcon(pixmap)
        
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon)
        self.tray.setToolTip("CapCut Live Translation Lens")
        self.tray.setVisible(True)
        
        self.menu = QMenu()
        self.quit_action = self.menu.addAction("Exit Translator")
        self.quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(self.menu)

        self.show_instructions()

        screen = self.app.primaryScreen()
        dpr = screen.devicePixelRatio()
        self.overlay = OverlayWindow(dpr, screen.size())
        self.overlay.show()

    def show_instructions(self):
        msg = QMessageBox()
        msg.setWindowTitle("CapCut Auto-Sized Click Lens")
        msg.setText(
            "<b>Auto-Sized Lens Active!</b><br><br>"
            "1. Press <b>Ctrl+T</b> to toggle the lens ON/OFF.<br>"
            "2. Hover over Chinese text in CapCut.<br>"
            "3. English text now dynamically adjusts size so it <b>never gets cut off!</b><br>"
            "4. Highly focused lens diameter (60px radius) for precision.<br>"
            "5. Click 100% cleanly through the lens into CapCut.<br><br>"
            "To exit: right-click cyan tray icon → <b>Exit</b>"
        )
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowIcon(self.icon)
        msg.exec()

    def quit_app(self):
        self.overlay.close()
        self.app.quit()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    translator_app = TranslationApp(app)
    sys.exit(app.exec())
