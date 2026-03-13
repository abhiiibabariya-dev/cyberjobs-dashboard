"""Standalone scan script — runs scrapers and saves to dashboard_jobs.json.
Called as a subprocess by dashboard.py so Flask stays responsive."""
import sys
import os
import traceback
import logging

# Prevent Flask from starting when we import dashboard functions
os.environ["SCAN_SUBPROCESS"] = "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dashboard import load_jobs_db, run_full_scan
    load_jobs_db()
    new = run_full_scan()
    print(f"Scan complete: {new} new jobs")
except Exception as e:
    log.error(f"Scan failed: {e}")
    traceback.print_exc()
    sys.exit(1)
