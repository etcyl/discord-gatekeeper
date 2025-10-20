import psutil

def is_bot_running():
    """Return True if the Discord bot is running, False otherwise."""
    matches = []
    for proc in psutil.process_iter(attrs=["pid", "cmdline", "username"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            username = proc.info.get("username", "")
            if not cmdline:
                continue

            # Match your bot process
            if "bot.py" in cmdline or "guildGateKeeper" in cmdline:
                matches.append((proc.pid, username, cmdline))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if matches:
        print("✅ Bot is running:")
        for pid, user, cmd in matches:
            print(f"   PID {pid:<6} | user={user:<10} | {cmd}")
        return True
    else:
        print("❌ Bot process not found.")
        return False


if __name__ == "__main__":
    status = is_bot_running()
    print("Result:", status)
