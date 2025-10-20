from flask import Blueprint, render_template, jsonify, Response, request, abort
from datetime import datetime
from services.status_utils import parse_log_events, get_logs, stream_bot_status, is_bot_running

bp = Blueprint("status", __name__)
START_TIME = datetime.now()

@bp.route("/")
def dashboard():
    from services.status_utils import is_bot_running_via_script, get_bot_uptime

    # Determine bot status
    bot_online = is_bot_running_via_script()
    uptime = get_bot_uptime() if bot_online else "N/A"

    # Parse logs and members
    member_joins, visitor_joins, leavers, member_count, visitor_count = parse_log_events()
    logs = get_logs()

    # Debug
    print(f"[DEBUG] Dashboard rendering: bot_online={bot_online}, uptime={uptime}", flush=True)

    return render_template(
        "dashboard.html",
        bot_status=bot_online,
        uptime=uptime,
        member_joins=member_joins,
        visitor_joins=visitor_joins,
        leavers=leavers,
        member_count=member_count,
        visitor_count=visitor_count,
        logs=logs
    )

@bp.route("/debug/logs")
def debug_logs():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403, "Access denied: logs only viewable locally.")
    from services.status_utils import get_logs
    return {"logs": get_logs()}

@bp.route("/api/status")
def api_status():
    """JSON endpoint for live updates."""
    from services.status_utils import is_bot_running, get_bot_uptime
    bot_status_result = is_bot_running()
    print("[DEBUG] Flask sees bot_status_result =", bot_status_result, flush=True)

    bot_status = "Online" if bot_status_result else "Offline"
    uptime = get_bot_uptime() if bot_status_result else "N/A"

    m, v, _ = parse_log_events()
    return jsonify({
        "bot_status": bot_status,
        "uptime": uptime,
        "guild_members": len(m),
        "visitors": len(v)
    })

@bp.route("/api")
def api_all():
    m, v, l = parse_log_events()
    return jsonify({"members": m, "visitors": v, "leavers": l, "logs": get_logs()})

@bp.route("/events/status")
def events_status():
    def event_stream():
        yield f"data: {stream_bot_status()}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@bp.route("/debug/botstatus")
def debug_botstatus():
    from services.status_utils import is_bot_running
    return {"status": is_bot_running()}
