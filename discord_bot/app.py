import os
import socket
import configparser
from flask import Flask
import traceback
from routes.status_routes import bp as status_bp

# ---------------------------------------------------------------------
# Configuration Loader
# ---------------------------------------------------------------------
def load_config():
    cfg_path = os.path.join(os.path.dirname(__file__), "config", "status_server.cfg")
    parser = configparser.ConfigParser()
    parser.read(cfg_path)
    return {
        "log_dir": parser.get("Paths", "log_dir"),
        "log_files": [x.strip() for x in parser.get("Files", "log_files").split(",")],
        "max_log_lines": parser.getint("Limits", "max_log_lines"),
        "max_recent": parser.getint("Limits", "max_recent"),
        "max_leavers": parser.getint("Limits", "max_leavers"),
        "host": parser.get("Server", "host"),
        "port": parser.getint("Server", "port"),
        "debug": parser.getboolean("Server", "debug"),
        "refresh_interval": parser.getint("Server", "refresh_interval"),
        "bot_process_name": parser.get("Monitoring", "bot_process_name"),
    }

# ---------------------------------------------------------------------
# Flask Application Factory
# ---------------------------------------------------------------------
def create_app():
    
    app = Flask(__name__, template_folder="templates")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.config["EXPLAIN_TEMPLATE_LOADING"] = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True


    # Load and attach configuration
    app.config["CUSTOM_CONFIG"] = load_config()

    # Register routes
    app.register_blueprint(status_bp)

    return app   # ✅ FIXED: must return the Flask app

# ---------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app = create_app()

    host = app.config["CUSTOM_CONFIG"].get("host", "0.0.0.0")
    default_port = app.config["CUSTOM_CONFIG"].get("port", 5000)
    port = default_port

    # Automatically find next available port if default in use
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, default_port))
    sock.close()

    if result == 0:
        for test_port in range(5050, 5100):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sock.connect_ex((host, test_port)) != 0:
                port = test_port
                sock.close()
                break
            sock.close()
        print(f"⚠️  Port {default_port} is in use. Switching to port {port}.")
    else:
        print(f"✅ Using default port {default_port}.")

    print(f"Starting Guild Gatekeeper Dashboard on {host}:{port}", flush=True)

    try:
        from waitress import serve
        print(f"✅ Waitress starting — open http://{host if host != '0.0.0.0' else 'localhost'}:{port}", flush=True)
        serve(app, host=host, port=port, threads=4)
    except Exception as e:
        print("❌ Failed to start Waitress server:", e, flush=True)
        traceback.print_exc()
        print("⚠️ Falling back to Flask development server...", flush=True)
        app.run(host=host, port=port)
