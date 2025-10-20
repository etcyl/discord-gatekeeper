from flask import Flask, jsonify, render_template_string
import os
import json
from datetime import datetime
from collections import deque

app = Flask(__name__)

# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------
LOG_DIR = "/home/fleet/Documents/discord_bot/guildGateKeeper"
LOG_FILES = [
    "discord_bot.log",
    "guild_audit.log",
    "guild_bot.log",
    "class_role_audit.log",
]
MAX_LOG_LINES = 3000  # for each file
MAX_LEAVERS = 50      # number of recent leavers to display


# ---------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------
def tail(filepath, n=MAX_LOG_LINES):
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
    except Exception:
        return []


def get_logs():
    """Return dictionary of recent lines per log file."""
    logs = {}
    for fname in LOG_FILES:
        path = os.path.join(LOG_DIR, fname)
        logs[fname] = tail(path)
    return logs


def parse_recent_leavers():
    """Parse guild_audit.log for member_remove events (last MAX_LEAVERS)."""
    path = os.path.join(LOG_DIR, "guild_audit.log")
    leavers = deque(maxlen=MAX_LEAVERS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if '"event":"member_remove"' in line:
                    try:
                        data = json.loads(line.split(" | ", 2)[-1])
                        user = data.get("display") or data.get("user_name") or f"ID {data.get('user_id')}"
                        removed_verified = data.get("removed_verified", False)
                        removed_alts = data.get("removed_alts", False)
                        ts = line.split(" | ")[0].strip()
                        leavers.append({
                            "user": user,
                            "removed_verified": removed_verified,
                            "removed_alts": removed_alts,
                            "timestamp": ts
                        })
                        if len(leavers) >= MAX_LEAVERS:
                            break
                    except Exception:
                        continue
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error parsing leavers: {e}")
    return list(leavers)


# ---------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------
@app.route("/")
def index():
    logs = get_logs()
    leavers = parse_recent_leavers()

    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Guild Gatekeeper Dashboard</title>
        <style>
            body {
                background-color: #0d1117;
                color: #c9d1d9;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 0;
            }
            h1 {
                text-align: center;
                padding: 20px;
                color: #58a6ff;
            }
            .container {
                width: 90%;
                margin: auto;
                padding-bottom: 40px;
            }
            .card {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 10px;
                margin: 20px 0;
                padding: 20px;
                box-shadow: 0 0 10px rgba(0,0,0,0.3);
            }
            .card h2 {
                color: #58a6ff;
                font-size: 20px;
                margin-top: 0;
            }
            .toggle {
                background-color: #238636;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                cursor: pointer;
                font-size: 14px;
                transition: background-color 0.2s;
            }
            .toggle:hover {
                background-color: #2ea043;
            }
            .hidden {
                display: none;
            }
            pre {
                background: #0d1117;
                border-radius: 8px;
                padding: 12px;
                overflow-x: auto;
                font-size: 13px;
                max-height: 400px;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            th, td {
                padding: 8px 10px;
                border-bottom: 1px solid #30363d;
                text-align: left;
            }
            th {
                background-color: #21262d;
                color: #58a6ff;
            }
            tr:hover {
                background-color: #21262d;
            }
            .status-true {
                color: #3fb950;
                font-weight: bold;
            }
            .status-false {
                color: #f85149;
                font-weight: bold;
            }
        </style>
        <script>
            function toggleContent(id) {
                const el = document.getElementById(id);
                el.classList.toggle('hidden');
            }
        </script>
    </head>
    <body>
        <h1>Guild Gatekeeper Dashboard</h1>
        <div class="container">
            
            <div class="card">
                <h2>📊 Recent Leavers</h2>
                <button class="toggle" onclick="toggleContent('leavers')">Show / Hide Last {{ leavers|length }} Leavers</button>
                <div id="leavers" class="hidden">
                    {% if leavers %}
                        <table>
                            <tr><th>User</th><th>Removed Verified</th><th>Removed Alts</th><th>Timestamp</th></tr>
                            {% for l in leavers %}
                            <tr>
                                <td>{{ l.user }}</td>
                                <td class="{{ 'status-true' if l.removed_verified else 'status-false' }}">{{ l.removed_verified }}</td>
                                <td class="{{ 'status-true' if l.removed_alts else 'status-false' }}">{{ l.removed_alts }}</td>
                                <td>{{ l.timestamp }}</td>
                            </tr>
                            {% endfor %}
                        </table>
                    {% else %}
                        <p>No recent leavers found.</p>
                    {% endif %}
                </div>
            </div>

            {% for name, lines in logs.items() %}
            <div class="card">
                <h2>📘 {{ name }}</h2>
                <button class="toggle" onclick="toggleContent('{{ name|replace('.', '_') }}')">Show / Hide</button>
                <pre id="{{ name|replace('.', '_') }}" class="hidden">{{ lines|join('\\n') }}</pre>
            </div>
            {% endfor %}
        </div>
    </body>
    </html>
    """

    return render_template_string(html, logs=logs, leavers=leavers)


@app.route("/api")
def api():
    """Return JSON of all current stats."""
    return jsonify({
        "leavers": parse_recent_leavers(),
        "logs": get_logs()
    })


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
