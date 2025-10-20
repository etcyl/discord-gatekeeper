import os
import json
import psutil
import getpass
import configparser
from collections import deque
import sys

import subprocess


# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------
CFG_PATH = os.path.join(os.path.dirname(__file__), "../config/status_server.cfg")

config = configparser.ConfigParser()
if os.path.exists(CFG_PATH):
    config.read(CFG_PATH)
else:
    print(f"[WARN] Missing config at {CFG_PATH}, using defaults.")

LOG_DIR = config.get("Paths", "log_dir", fallback=os.path.expanduser("~/Documents/discord_bot/guildGateKeeper"))
LOG_FILES = [f.strip() for f in config.get("Files", "log_files", fallback="guild_bot.log,guild_audit.log").split(",")]
MAX_LOG_LINES = config.getint("Limits", "max_log_lines", fallback=None)
MAX_LEAVERS = config.getint("Limits", "max_leavers", fallback=1000)

# ---------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------
def tail(filepath, n=3000):
    """Return last n lines of a file efficiently."""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            buffer = bytearray()
            lines_found = 0
            block_size = 1024
            while size > 0 and lines_found < n:
                read_size = min(block_size, size)
                size -= read_size
                f.seek(size)
                buffer[:0] = f.read(read_size)
                lines_found = buffer.count(b"\n")
            lines = buffer.splitlines()[-n:]
            return [line.decode("utf-8", errors="ignore") for line in lines]
    except Exception as e:
        print(f"[WARN] tail() failed on {filepath}: {e}")
        return []


def read_log_lines(log_path, max_lines=None):
    """Safely read all or last N log lines with UTF-8 fallback, newest first."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            if max_lines:
                lines = lines[-max_lines:]
            return list(reversed(lines))  # newest first
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[WARN] Could not read {log_path}: {e}")
        return []

# ---------------------------------------------------------------------
# LOG PARSING
# ---------------------------------------------------------------------
def parse_log_events():
    """
    Parse all logs for guild members, visitors, and leavers.
    Returns:
      (member_joins, visitor_joins, leavers, member_count, visitor_count)
    """
    member_joins, visitor_joins, leavers = [], [], []
    member_names, visitor_names = set(), set()

    for log_file in LOG_FILES:
        path = os.path.join(LOG_DIR, log_file)
        for line in read_log_lines(path, max_lines=MAX_LOG_LINES):
            if "{" not in line:
                continue
            try:
                json_part = line.split(" | INFO | ")[-1].strip()
                data = json.loads(json_part)
            except json.JSONDecodeError:
                continue

            event = data.get("event", "")
            display = data.get("display", "Unknown")
            user = data.get("user_name", "")
            track = data.get("track", "")
            ts = line.split(" | INFO | ")[0].strip()

            # --- Joins ---
            if event in ("onboard_promoted", "track_selected", "member_join", "member_join_initialized"):
                if track == "member":
                    member_joins.append({
                        "time": ts,
                        "display": display,
                        "user": user,
                        "status": "Guild Member" if event == "onboard_promoted" else "Newcomer"
                    })
                    member_names.add(user)
                elif track == "visitor":
                    visitor_joins.append({
                        "time": ts,
                        "display": display,
                        "user": user,
                        "status": "Visitor"
                    })
                    visitor_names.add(user)

            # --- Leavers ---
            elif event == "member_remove":
                leavers.append({
                    "time": ts,
                    "display": display,
                    "user": user,
                    "removed_alts": data.get("removed_alts", False),
                    "removed_verified": data.get("removed_verified", False)
                })

    # Sort newest → oldest
    member_joins.sort(key=lambda x: x["time"], reverse=True)
    visitor_joins.sort(key=lambda x: x["time"], reverse=True)
    leavers.sort(key=lambda x: x["time"], reverse=True)

    save_parsed_data(member_joins, visitor_joins, leavers)
    return member_joins, visitor_joins, leavers, len(member_names), len(visitor_names)


def get_logs():
    """Return dictionary of logs for debugging."""
    logs = {}
    for fname in LOG_FILES:
        path = os.path.join(LOG_DIR, fname)
        logs[fname] = tail(path)
    return logs

# ---------------------------------------------------------------------
# BOT STATUS HELPERS
# ---------------------------------------------------------------------
from time import time

_last_check = {"time": 0, "status": False}

def is_bot_running_cached(ttl=15):
    """Cache the result for `ttl` seconds."""
    now = time()
    if now - _last_check["time"] > ttl:
        _last_check["status"] = is_bot_running_via_script()
        _last_check["time"] = now
    return _last_check["status"]

def is_bot_running(process_name=None):
    return is_bot_running_cached()


def is_bot_running_via_script():
    """
    Run check_bot_status.py as a subprocess and parse its final line.
    Returns True if 'Result: True' is detected, False otherwise.
    """
    try:
        script_path = os.path.expanduser("~/Documents/discord_bot/check_bot_status.py")
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout.strip()
        print("[DEBUG] check_bot_status.py output:\n", output)

        # Look for the final 'Result:' line
        for line in output.splitlines():
            if line.strip().startswith("Result:"):
                return "True" in line

        return False
    except Exception as e:
        print(f"[WARN] Failed to run check_bot_status.py: {e}")
        return False

def stream_bot_status(process_name=None, *_):
    """Return bot status string for SSE or API."""
    return "Running" if is_bot_running(process_name) else "Offline"

def save_parsed_data(member_joins, visitor_joins, leavers):
    """Save parsed event data to a readable JSON file for external inspection."""
    try:
        output_path = os.path.join(LOG_DIR, "parsed_guild_data.json")
        data = {
            "member_joins": list(member_joins),
            "visitor_joins": list(visitor_joins),
            "leavers": list(leavers),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Saved parsed data to {output_path}")
    except Exception as e:
        print(f"[WARN] Could not save parsed data: {e}")

def get_bot_uptime():
    """Return bot uptime as human-readable string, if found."""
    try:
        for proc in psutil.process_iter(attrs=["pid", "cmdline", "create_time"]):
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "bot.py" in cmdline or "guildGateKeeper" in cmdline:
                import time
                elapsed = time.time() - proc.info["create_time"]
                hrs, rem = divmod(int(elapsed), 3600)
                mins, secs = divmod(rem, 60)
                return f"{hrs}h {mins}m {secs}s"
    except Exception as e:
        print(f"[WARN] Uptime check failed: {e}")
    return "N/A"
