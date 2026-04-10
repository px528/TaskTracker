"""
TaskTracker - Flask Web Server
Serves the dashboard and provides REST API for time segment data.
Task id/name are derived from config.json at query time; only process_name
and timestamps are stored in the database.
"""

import atexit
import signal
import sqlite3
import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, abort

from tracker import (
    DB_PATH, CONFIG_PATH, TaskTracker,
    start_tracker_thread, get_tracker, load_config
)

app = Flask(__name__, static_folder="static")

# ── Start background tracker thread ──────────────────────────────────────────
tracker_thread = start_tracker_thread()


# ── Graceful shutdown: end current segment when process exits ─────────────────

def _shutdown_handler():
    """Called on process exit — writes end_time for the active segment."""
    from tracker import _tracker_instance
    if _tracker_instance is None:
        return
    with _tracker_instance._lock:
        _tracker_instance._end_segment()


atexit.register(_shutdown_handler)


def _signal_handler(signum, frame):
    """Handle SIGTERM so atexit handlers are triggered on kill."""
    import sys
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)


# ── Helpers ───────────────────────────────────────────────────────────────────

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ts_to_iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).isoformat()


def match_task_from_config(process_name: str, window_title: str, tasks: list) -> dict | None:
    """
    Derive the task for a recorded segment by matching process_name / window_title
    against config keywords.  Also handles manual segments where process_name
    was stored as a task_id directly (e.g. from /api/switch).
    """
    # Direct task-id match (used by manual switch)
    for task in tasks:
        if task.get("id") == process_name:
            return task

    # Keyword substring match (same logic as tracker.match_task)
    combined = ((window_title or "") + " " + (process_name or "")).lower()
    others_task = None
    for task in tasks:
        if task.get("id") == "others":
            others_task = task
            continue
        for keyword in task.get("keywords", []):
            if keyword.lower() in combined:
                return task
    return others_task


# ── Static files ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ── API: Status ───────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    tracker = get_tracker()
    status = tracker.get_status()
    config = load_config()
    # Derive the currently-tracked task from config (display only)
    matched = status.get("matched_task")
    return jsonify({
        "running": status["running"],
        "current_task": matched,          # task dict or None
        "segment_start": ts_to_iso(status["segment_start"]),
        "window_title": status["window_title"],
        "process_name": status["process_name"],
        "matched_task": matched,
        "server_time": datetime.now().isoformat()
    })


# ── API: Tasks ────────────────────────────────────────────────────────────────

@app.route("/api/tasks")
def api_tasks():
    # Read tasks from config.json which contains keywords
    config = load_config()
    return jsonify(config.get("tasks", []))


@app.route("/api/tasks", methods=["POST"])
def api_add_task():
    data = request.get_json()
    required = {"id", "name", "color", "keywords"}
    if not required.issubset(data.keys()):
        abort(400, "Missing fields")

    # Update config.json
    config = load_config()
    # Remove existing task with same id
    config["tasks"] = [t for t in config["tasks"] if t["id"] != data["id"]]
    config["tasks"].append({
        "id": data["id"],
        "name": data["name"],
        "color": data["color"],
        "keywords": data["keywords"]
    })
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # Reload tracker config
    get_tracker().reload_config()

    return jsonify({"ok": True})


@app.route("/api/tasks/reorder", methods=["POST"])
def api_reorder_tasks():
    """Reorder tasks based on the provided order."""
    data = request.get_json()
    task_order = data.get("task_order", [])
    
    if not task_order:
        abort(400, "task_order is required")
    
    # Load current config
    config = load_config()
    
    # Create a mapping of task id to task object (this automatically deduplicates)
    task_map = {t["id"]: t for t in config["tasks"]}
    
    # Remove duplicates from task_order while preserving order
    seen = set()
    unique_task_order = []
    for task_id in task_order:
        if task_id not in seen:
            seen.add(task_id)
            unique_task_order.append(task_id)
    
    # Reorder tasks based on the provided order
    reordered_tasks = []
    for task_id in unique_task_order:
        if task_id in task_map:
            reordered_tasks.append(task_map[task_id])
    
    # Add any tasks that weren't in the order (shouldn't happen, but just in case)
    for task_id, task in task_map.items():
        if task_id not in unique_task_order:
            reordered_tasks.append(task)
    
    # Update config with new order
    config["tasks"] = reordered_tasks
    
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    # Reload tracker config
    get_tracker().reload_config()
    
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def api_delete_task(task_id):
    # Tasks only exist in config.json; nothing to delete from the database.
    config = load_config()
    config["tasks"] = [t for t in config["tasks"] if t["id"] != task_id]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    get_tracker().reload_config()
    return jsonify({"ok": True})


# ── API: Segments ─────────────────────────────────────────────────────────────

@app.route("/api/segments")
def api_segments():
    """
    Return segments filtered by date range.
    task_id and task_name are derived from config.json at query time.
    Query params:
      from  - ISO date string or unix timestamp (default: today 00:00)
      to    - ISO date string or unix timestamp (default: now)
    """
    now = time.time()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    from_ts = float(request.args.get("from", today_start))
    to_ts = float(request.args.get("to", now))

    config = load_config()
    tasks = config.get("tasks", [])

    conn = db_conn()
    rows = conn.execute("""
        SELECT id, process_name, start_time, end_time, window_title
        FROM process_segments
        WHERE start_time >= ? AND start_time <= ?
        ORDER BY start_time
    """, (from_ts, to_ts)).fetchall()
    conn.close()

    # Get current active segment id to distinguish live vs orphaned NULL end_time
    tracker = get_tracker()
    current_seg_id = tracker.current_segment_id

    rows = list(rows)
    result = []
    for i, r in enumerate(rows):
        task = match_task_from_config(r["process_name"], r["window_title"] or "", tasks)
        task_id = task["id"] if task else "others"
        task_name = task["name"] if task else "Others"

        if r["end_time"]:
            # Normal completed segment
            end_ts = r["end_time"]
        elif r["id"] == current_seg_id:
            # Currently active segment — use current time
            end_ts = now
        else:
            # Orphaned segment (program crashed without writing end_time).
            # Use the next segment's start_time as a best-effort end_time.
            # First check the next row in the current result set.
            if i + 1 < len(rows):
                end_ts = rows[i + 1]["start_time"]
            else:
                # The orphaned segment is the last in the query range;
                # look for any later segment in the full database.
                conn2 = db_conn()
                next_row = conn2.execute("""
                    SELECT start_time FROM process_segments
                    WHERE start_time > ? AND start_time <= ?
                    ORDER BY start_time LIMIT 1
                """, (r["start_time"], to_ts)).fetchone()
                conn2.close()
                end_ts = next_row["start_time"] if next_row else now

        result.append({
            "id": r["id"],
            "process_name": r["process_name"],
            "task_id": task_id,
            "task_name": task_name,
            "start_time": r["start_time"],
            "end_time": end_ts,
            "start_iso": ts_to_iso(r["start_time"]),
            "end_iso": ts_to_iso(end_ts),
            "duration_seconds": end_ts - r["start_time"],
            "window_title": r["window_title"]
        })
    return jsonify(result)


# ── API: Manual task switch ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TaskTracker Dashboard")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)