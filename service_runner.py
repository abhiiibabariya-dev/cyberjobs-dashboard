"""
CyberJobs Dashboard - Persistent Service Runner
Keeps the dashboard running even if it crashes. Auto-restarts with backoff.
"""
import subprocess
import sys
import os
import time
import logging

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "service_runner.log")
DASHBOARD = os.path.join(SCRIPT_DIR, "dashboard.py")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SERVICE] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
log = logging.getLogger("ServiceRunner")

def cleanup_stale_processes():
    """Kill stale dashboard/ngrok/chromedriver processes before starting."""
    for proc_name in ["ngrok.exe", "chromedriver.exe"]:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", proc_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            )
        except Exception:
            pass


def run():
    restart_count = 0
    cleanup_stale_processes()
    while True:
        log.info(f"Starting dashboard (restart #{restart_count})...")
        try:
            proc = subprocess.Popen(
                [sys.executable, DASHBOARD],
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            proc.wait()
            exit_code = proc.returncode
            log.info(f"Dashboard exited with code {exit_code}")
        except Exception as e:
            log.error(f"Failed to start dashboard: {e}")

        restart_count += 1
        # Backoff: wait longer after repeated failures (max 60s)
        wait = min(5 * restart_count, 60)
        log.info(f"Restarting in {wait}s...")
        time.sleep(wait)

        # Reset backoff after successful long run (>5 min)
        if restart_count > 1:
            restart_count = max(1, restart_count - 1)

if __name__ == "__main__":
    run()
