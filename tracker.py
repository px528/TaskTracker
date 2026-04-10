"""
TaskTracker - Background Window Monitor
Polls the active window title every N seconds and records process activity.
Records time segments to SQLite database (only process name and time range).
"""

import sqlite3
import json
import time
import threading
import logging
from datetime import datetime
from pathlib import Path

# Windows-specific import
try:
    import win32gui
    import win32process
    import psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    logging.warning("pywin32/psutil not available. Using mock window detection.")

DB_PATH = Path("tasktracker.db")
CONFIG_PATH = Path("config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("tracker.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def init_db():
    """Initialize the SQLite database schema - simplified to only store process activity.
    Drops the legacy 'segments' table (which stored task_id/task_name) if it still exists.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS process_segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            process_name TEXT NOT NULL,
            start_time  REAL NOT NULL,
            end_time    REAL,
            window_title TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def load_config():
    """Load task configuration from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_active_window_title():
    """Return the title of the currently focused window."""
    if HAS_WIN32:
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            return title
        except Exception as e:
            logger.debug("Error getting window title: %s", e)
            return ""
    else:
        return "Unknown Window"


def get_active_process_name():
    """Return the process name of the currently focused window."""
    if HAS_WIN32:
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return ""
    return ""


def match_task(window_title: str, process_name: str, tasks: list) -> dict | None:
    """
    Match the active window to a configured task (for display purposes only).
    Returns the matching task dict or None if no match found.
    """
    combined = (window_title + " " + process_name).lower()
    others_task = None
    
    for task in tasks:
        if task.get("id") == "others":
            others_task = task
            continue
        
        for keyword in task.get("keywords", []):
            if keyword.lower() in combined:
                return task
    
    return others_task


class TaskTracker:
    def __init__(self):
        self.config = load_config()
        self.tasks = self.config["tasks"]
        self.settings = self.config["settings"]
        self.poll_interval = self.settings.get("poll_interval_seconds", 3)
        self.min_segment = self.settings.get("min_segment_seconds", 5)

        self.current_process = None
        self.current_segment_id = None
        self.current_segment_start = None
        self._running = False
        self._lock = threading.Lock()

        init_db()

    def reload_config(self):
        """Hot-reload configuration."""
        self.config = load_config()
        self.tasks = self.config["tasks"]
        self.settings = self.config["settings"]
        self.poll_interval = self.settings.get("poll_interval_seconds", 3)
        self.min_segment = self.settings.get("min_segment_seconds", 5)
        logger.info("Config reloaded.")

    def _start_segment(self, process_name: str, window_title: str):
        """Open a new time segment for the given process."""
        now = time.time()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO process_segments (process_name, start_time, window_title)
            VALUES (?, ?, ?)
        """, (process_name, now, window_title))
        seg_id = c.lastrowid
        conn.commit()
        conn.close()

        self.current_process = process_name
        self.current_segment_id = seg_id
        self.current_segment_start = now
        logger.info("▶ Started segment #%d for process '%s' | window: %s",
                    seg_id, process_name, window_title[:80])

    def _end_segment(self):
        """Close the current open segment."""
        if self.current_segment_id is None:
            return
        now = time.time()
        duration = now - (self.current_segment_start or now)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        if duration < self.min_segment:
            c.execute("DELETE FROM process_segments WHERE id = ?", (self.current_segment_id,))
            logger.debug("Deleted short segment #%d (%.1fs)", self.current_segment_id, duration)
        else:
            c.execute("UPDATE process_segments SET end_time = ? WHERE id = ?",
                      (now, self.current_segment_id))
            logger.info("■ Ended segment #%d for process '%s' (%.0fs)",
                        self.current_segment_id,
                        self.current_process or "?",
                        duration)
        conn.commit()
        conn.close()

        self.current_process = None
        self.current_segment_id = None
        self.current_segment_start = None

    def _poll(self):
        """Single poll cycle: detect active window and update segments."""
        window_title = get_active_window_title()
        process_name = get_active_process_name()

        if not process_name:
            return

        with self._lock:
            if self.current_process is None:
                # Start new segment
                self._start_segment(process_name, window_title)
            elif process_name != self.current_process:
                # Process changed, end current and start new
                self._end_segment()
                self._start_segment(process_name, window_title)
            else:
                # Same process, update end_time (heartbeat)
                if self.current_segment_id is not None:
                    now = time.time()
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE process_segments SET end_time = ? WHERE id = ?",
                              (now, self.current_segment_id))
                    conn.commit()
                    conn.close()

    def start(self):
        """Start the background polling loop."""
        self._running = True
        logger.info("TaskTracker started. Poll interval: %ds", self.poll_interval)
        while self._running:
            try:
                self._poll()
            except Exception as e:
                logger.error("Poll error: %s", e)
            time.sleep(self.poll_interval)
        # Clean up on stop
        with self._lock:
            self._end_segment()
        logger.info("TaskTracker stopped.")

    def stop(self):
        self._running = False

    def get_status(self) -> dict:
        """Return current tracking status."""
        with self._lock:
            title = get_active_window_title()
            process = get_active_process_name()
            matched = match_task(title, process, self.tasks)
            return {
                "running": self._running,
                "current_process": self.current_process,
                "segment_start": self.current_segment_start,
                "window_title": title,
                "process_name": process,
                "matched_task": matched
            }


# Singleton tracker instance (used by app.py)
_tracker_instance: TaskTracker | None = None
_tracker_thread: threading.Thread | None = None


def get_tracker() -> TaskTracker:
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = TaskTracker()
    return _tracker_instance


def start_tracker_thread() -> threading.Thread:
    global _tracker_thread, _tracker_instance
    _tracker_instance = TaskTracker()
    t = threading.Thread(target=_tracker_instance.start, daemon=True, name="TaskTrackerThread")
    t.start()
    _tracker_thread = t
    return t


if __name__ == "__main__":
    tracker = TaskTracker()
    try:
        tracker.start()
    except KeyboardInterrupt:
        tracker.stop()