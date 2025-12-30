import sys, os, threading, shutil, subprocess, requests, urllib.request, zipfile
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
    status = Signal(str)
    progress = Signal(int)
    speed = Signal(str)

# ---------------- MAIN WINDOW ----------------
class Downloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Downloader Pro")
        self.setMinimumSize(720, 620)
        self.setStyleSheet(DARK_STYLE)

        self.signals = ProgressSignal()

        # CRITICAL: Enhanced FFmpeg detection
        self.ffmpeg_path = self.find_ffmpeg()
        self.node_installed = self.check_node()


        if not self.ffmpeg_path:
            self.auto_download_ffmpeg()
        if not self.node_installed:
            QMessageBox.warning(self, "Warning",
                                "Node.js runtime not found!\nSome YouTube formats may download audio-only.\n"
                                "Install Node.js for full video download support: https://nodejs.org/")

        self.init_ui()
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(lambda v: self.progress.setValue(v))
        self.signals.speed.connect(lambda s: self.speed_label.setText(s))

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here...")
        layout.addWidget(self.url_input)

        self.preview_btn = QPushButton("Load Preview")
        self.preview_btn.clicked.connect(self.load_preview)
        layout.addWidget(self.preview_btn)

        self.thumbnail = QLabel(alignment=Qt.AlignCenter)
        self.thumbnail.setFixedHeight(220)
        layout.addWidget(self.thumbnail)

        opt_layout = QHBoxLayout()
        self.format_box = QComboBox()
        self.format_box.addItems(["MP4", "MP3"])
        self.quality_box = QComboBox()
        self.quality_box.addItems(["Best", "1080p", "720p", "480p"])
        opt_layout.addWidget(self.format_box)
        opt_layout.addWidget(self.quality_box)
        layout.addLayout(opt_layout)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(os.getcwd())
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.pick_folder)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.speed_label = QLabel("Speed: --")
        layout.addWidget(self.speed_label)

        self.status_label = QLabel("Idle")
        layout.addWidget(self.status_label)

        self.download_btn = QPushButton("Download")
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)

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
        if not self.ffmpeg_path:
            QMessageBox.critical(self, "Error", 
                "FFmpeg is required but not found!\n\n"
                "MP4 downloads need FFmpeg to merge video+audio.\n"
                "Please install FFmpeg or use the auto-download option.")
            return
        threading.Thread(target=self.download, daemon=True).start()

    def download(self):
        url = self.url_input.text().strip()
        if not url:
            self.signals.status.emit("Error: No URL provided")
            return

        out_dir = self.path_edit.text()
        quality = self.quality_box.currentText().replace("p", "")

        # Base options - CRITICAL: FFmpeg location must be set FIRST
        opts = {
            "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [self.progress_hook],
            "quiet": False,
            "no_warnings": False,
        }

        # CRITICAL: Always set FFmpeg location at the base level
        if self.ffmpeg_path:
            opts["ffmpeg_location"] = os.path.dirname(self.ffmpeg_path)

        if self.format_box.currentText() == "MP3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }]
        else:
            # MP4 VIDEO DOWNLOAD - CRITICAL FIXES
            # 1. Select proper video+audio streams
            if quality == "Best":
                # Try MP4 first, fallback to any format
                opts["format"] = "(bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio)[protocol!=m3u8]/best"
            else:
                opts["format"] = f"(bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio)[protocol!=m3u8]/best[height<={quality}]"
            
            # 2. Force merge to MP4
            opts["merge_output_format"] = "mp4"
            
            # 3. Ensure postprocessor runs
            opts["postprocessors"] = [{
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4"
            }]
            
            # 4. Force download of both streams
            opts["keepvideo"] = False  # Don't keep separate video file
            
            # 5. Verify FFmpeg is accessible
            if not self.ffmpeg_path or not os.path.exists(self.ffmpeg_path):
                self.signals.status.emit("Error: FFmpeg not accessible")
                QMessageBox.critical(self, "FFmpeg Error",
                    f"FFmpeg is required but not accessible!\n\nPath: {self.ffmpeg_path}")
                return

        self.signals.status.emit("Starting download...")
        self.signals.progress.emit(0)

        # (logging removed: no file writes)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            self.signals.status.emit("✓ Download completed!")
            self.signals.progress.emit(100)
        except Exception as e:
            error_msg = str(e)
            self.signals.status.emit(f"Error: {error_msg}")
            QMessageBox.critical(self, "Download Error", 
                f"Download failed:\n{error_msg}\n\n"
                f"FFmpeg path: {self.ffmpeg_path}")

    def progress_hook(self, d):
        status = d.get("status")
        try:
            if status == "downloading":
                pct = float(d["_percent_str"].replace("%","").strip())
                speed = d.get("_speed_str", "").strip()
                self.signals.progress.emit(int(pct))
                self.signals.speed.emit(f"Speed: {speed}")
            elif status == "finished":
                self.signals.status.emit("Processing & merging...")
                self.signals.progress.emit(95)
        except Exception:
            pass

    # ---------------- ENHANCED FFMPEG DETECTION ----------------
    def find_ffmpeg(self):
        """Enhanced FFmpeg detection with multiple fallback paths"""
        search_paths = []
        
        # 1. PyInstaller bundle path (_MEIPASS)
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
            search_paths.extend([
                os.path.join(base_path, "ffmpeg", "bin", "ffmpeg.exe"),
                os.path.join(base_path, "ffmpeg.exe"),
                os.path.join(base_path, "bin", "ffmpeg.exe"),
            ])
        
        # 2. Local script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_paths.extend([
            os.path.join(script_dir, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(script_dir, "ffmpeg", "ffmpeg-release-essentials", "bin", "ffmpeg.exe"),
            os.path.join(script_dir, "ffmpeg.exe"),
        ])
        
        # 3. Application directory (for installed builds)
        app_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else script_dir
        search_paths.extend([
            os.path.join(app_dir, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(app_dir, "ffmpeg.exe"),
        ])
        
        # Check all paths
        for path in search_paths:
            if os.path.exists(path) and os.path.isfile(path):
                # Verify it's executable
                try:
                    subprocess.run([path, "-version"], 
                                 stdout=subprocess.PIPE, 
                                 stderr=subprocess.PIPE, 
                                 check=True,
                                 timeout=5)
                    return path
                except:
                    continue
        
        # 4. System PATH
        ff = shutil.which("ffmpeg")
        if ff:
            return ff
        
        return None

    def check_node(self):
        try:
            subprocess.run(["node", "-v"], 
                         stdout=subprocess.PIPE, 
                         stderr=subprocess.PIPE, 
                         check=True,
                         timeout=5)
            return True
        except:
            return False

    def auto_download_ffmpeg(self):
        """Download FFmpeg automatically if missing (Windows only)."""
        reply = QMessageBox.question(self, "FFmpeg Not Found",
                                     "FFmpeg is required for MP4 downloads.\n\n"
                                     "Would you like to download it automatically?\n"
                                     "(~80MB download)",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        
        # Determine download location
        if getattr(sys, 'frozen', False):
            download_dir = os.path.dirname(sys.executable)
        else:
            download_dir = os.path.dirname(os.path.abspath(__file__))
        
        zip_path = os.path.join(download_dir, "ffmpeg.zip")
        extract_dir = os.path.join(download_dir, "ffmpeg")
        
        self.signals.status.emit("Downloading FFmpeg... Please wait...")
        
        try:
            # Download
            urllib.request.urlretrieve(url, zip_path)
            
            # Extract
            self.signals.status.emit("Extracting FFmpeg...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Cleanup
            os.remove(zip_path)
            
            # Find the extracted ffmpeg.exe
            for root, dirs, files in os.walk(extract_dir):
                if "ffmpeg.exe" in files:
                    self.ffmpeg_path = os.path.join(root, "ffmpeg.exe")
                    break
            
            if self.ffmpeg_path:
                self.signals.status.emit("✓ FFmpeg installed successfully!")
                QMessageBox.information(self, "Success", 
                    f"FFmpeg downloaded and installed!\n\nLocation: {self.ffmpeg_path}")
            else:
                raise Exception("FFmpeg.exe not found in extracted files")
                
        except Exception as e:
            self.signals.status.emit("FFmpeg download failed")
            QMessageBox.critical(self, "FFmpeg Error", 
                f"Failed to download FFmpeg:\n{e}\n\n"
                f"Please manually download from:\nhttps://ffmpeg.org/download.html")

    def write_debug_log(self):
        """Write debug information to file for troubleshooting"""
        log_path = os.path.join(os.getcwd(), "debug_log.txt")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("=== YouTube Downloader Debug Log ===\n\n")
                f.write(f"Python Version: {sys.version}\n")
                f.write(f"Frozen (PyInstaller): {getattr(sys, 'frozen', False)}\n")
                if getattr(sys, 'frozen', False):
                    f.write(f"_MEIPASS: {sys._MEIPASS}\n")
                    f.write(f"Executable: {sys.executable}\n")
                f.write(f"Script Directory: {os.path.dirname(os.path.abspath(__file__))}\n")
                f.write(f"Current Working Dir: {os.getcwd()}\n\n")
                f.write(f"FFmpeg Path: {self.ffmpeg_path}\n")
                f.write(f"FFmpeg Exists: {os.path.exists(self.ffmpeg_path) if self.ffmpeg_path else 'N/A'}\n")
                
                # Test FFmpeg execution
                if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
                    try:
                        result = subprocess.run(
                            [self.ffmpeg_path, "-version"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        f.write(f"FFmpeg Executable: {'YES' if result.returncode == 0 else 'NO'}\n")
                        f.write(f"FFmpeg Version: {result.stdout.split('\\n')[0] if result.returncode == 0 else 'Error'}\n")
                    except Exception as e:
                        f.write(f"FFmpeg Test Failed: {e}\n")
                
                f.write(f"Node.js Installed: {self.node_installed}\n\n")
                f.write(f"yt-dlp Version: {yt_dlp.version.__version__}\n")
        except Exception as e:
            # Removed debug log writes; do not print or write logs
            pass

# ---------------- RUN ----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Downloader()
    window.show()
    sys.exit(app.exec())