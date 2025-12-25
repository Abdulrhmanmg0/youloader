import sys, os, threading, shutil, subprocess, requests
from io import BytesIO
from PIL import Image

import yt_dlp
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QComboBox, QFileDialog, QVBoxLayout, QHBoxLayout,
    QProgressBar, QMessageBox
)
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt, Signal, QObject

# ---------------- SUPPRESS QT WARNINGS ----------------
os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false"

# ---------------- THEME ----------------
DARK_STYLE = """
QWidget { background-color: #121212; color: #ffffff; font-size: 14px; }
QLineEdit, QComboBox { background-color: #1f1f1f; border: 1px solid #333; padding: 6px; }
QPushButton { background-color: #1f6feb; border-radius: 6px; padding: 8px; }
QPushButton:hover { background-color: #388bfd; }
QProgressBar { background-color: #2a2a2a; border-radius: 6px; }
QProgressBar::chunk { background-color: #3fb950; }
"""

# ---------------- SIGNALS ----------------
class ProgressSignal(QObject):
    status = Signal(str)     # Status messages
    progress = Signal(int)   # Progress bar %
    speed = Signal(str)      # Download speed

# ---------------- MAIN WINDOW ----------------
class Downloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Downloader Pro")
        self.setMinimumSize(720, 620)
        self.setStyleSheet(DARK_STYLE)

        # Signals container
        self.signals = ProgressSignal()

        # FFmpeg & Node detection
        self.ffmpeg_path = self.find_ffmpeg()
        self.node_installed = self.check_node()

        if not self.ffmpeg_path:
            QMessageBox.warning(self, "Warning",
                                "FFmpeg not found! MP4 downloads may fail.\n"
                                "Include bundled version in 'ffmpeg/bin/ffmpeg.exe' or install system-wide.")
        if not self.node_installed:
            QMessageBox.warning(self, "Warning",
                                "Node.js runtime not found!\nSome YouTube formats may not download correctly.")

        # Build UI
        self.init_ui()

        # Connect signals to UI
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(lambda v: self.progress.setValue(v))
        self.signals.speed.connect(lambda s: self.speed_label.setText(s))

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # URL input
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here...")
        layout.addWidget(self.url_input)

        # Load preview
        self.preview_btn = QPushButton("Load Preview")
        self.preview_btn.clicked.connect(self.load_preview)
        layout.addWidget(self.preview_btn)

        # Thumbnail
        self.thumbnail = QLabel(alignment=Qt.AlignCenter)
        self.thumbnail.setFixedHeight(220)
        layout.addWidget(self.thumbnail)

        # Format & quality
        opt_layout = QHBoxLayout()
        self.format_box = QComboBox()
        self.format_box.addItems(["MP4", "MP3"])
        self.quality_box = QComboBox()
        self.quality_box.addItems(["Best", "1080", "720", "480"])
        opt_layout.addWidget(self.format_box)
        opt_layout.addWidget(self.quality_box)
        layout.addLayout(opt_layout)

        # Folder selector
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(os.getcwd())
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.pick_folder)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        # Progress bar & speed
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.speed_label = QLabel("Speed: --")
        layout.addWidget(self.speed_label)

        # Status label
        self.status_label = QLabel("Idle")
        layout.addWidget(self.status_label)

        # Download button
        self.download_btn = QPushButton("Download")
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)

    # ---------------- FUNCTIONS ----------------
    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if folder:
            self.path_edit.setText(folder)

    def load_preview(self):
        url = self.url_input.text().strip()
        if not url:
            return
        try:
            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                img_data = requests.get(info["thumbnail"]).content
                img = Image.open(BytesIO(img_data)).resize((420, 240))
                qimg = QImage(img.tobytes(), img.width, img.height, QImage.Format_RGB888)
                self.thumbnail.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            QMessageBox.warning(self, "Preview Error", str(e))

    def start_download(self):
        if not self.ffmpeg_path and self.format_box.currentText() == "MP4":
            QMessageBox.critical(self, "Error", "FFmpeg is required for MP4 downloads!")
            return
        threading.Thread(target=self.download, daemon=True).start()

    def download(self):
        url = self.url_input.text()
        out_dir = self.path_edit.text()
        opts = {
            "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [self.progress_hook],
            "quiet": True,
        }

        if self.format_box.currentText() == "MP3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }]
        else:
            opts["format"] = "bestvideo+bestaudio/best"
            if self.ffmpeg_path:
                opts["ffmpeg_location"] = self.ffmpeg_path

        # Signal start
        self.signals.status.emit("Downloading...")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            self.signals.status.emit(f"Error: {e}")
            QMessageBox.critical(self, "Download Error", str(e))
            return

        # Completed successfully
        self.signals.status.emit("Completed")
        self.signals.progress.emit(100)
        self.signals.speed.emit("Speed: --")

    # ---------------- PROGRESS HOOK ----------------
    def progress_hook(self, d):
        status = d.get("status")
        try:
            if status == "downloading":
                pct = float(d["_percent_str"].replace("%",""))
                speed = d.get("_speed_str","")
                self.signals.progress.emit(int(pct))
                self.signals.speed.emit(f"Speed: {speed}")
                self.signals.status.emit("Downloading...")
            elif status == "finished":
                self.signals.status.emit("Processing finalizing...")
        except Exception:
            pass

    # ---------------- FFmpeg & Node Detection ----------------
    def find_ffmpeg(self):
        # PyInstaller bundle path
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(__file__)
        bundled = os.path.join(base_path, "ffmpeg", "bin", "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled
        ff = shutil.which("ffmpeg")
        if ff:
            return ff
        return None

    def check_node(self):
        try:
            subprocess.run(["node", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return True
        except:
            return False

# ---------------- RUN ----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Downloader()
    window.show()
    sys.exit(app.exec())
