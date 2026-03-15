"""
SOC Analyst L1 - Live AI Job Hunter Dashboard
Flask web app with real-time job scanning, one-click apply, and public URL via ngrok.
"""

import glob
import json
import os
import re
import sys
import time
import hashlib
import base64
import random
import string
import threading
import subprocess
import logging
import smtplib
import csv
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlencode, urlparse, parse_qs, unquote

import bcrypt
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, render_template_string, jsonify, request, Response
from flask_cors import CORS
from fake_useragent import UserAgent

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

try:
    from selenium_stealth import stealth as apply_stealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# ─── Setup ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("dashboard.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("Dashboard")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
JOBS_DB = os.path.join(SCRIPT_DIR, "dashboard_jobs.json")
EMAILED_DB = os.path.join(SCRIPT_DIR, "dashboard_emailed.json")
USERS_DB = os.path.join(SCRIPT_DIR, "users.json")
APP_LOG_DB = os.path.join(SCRIPT_DIR, "application_log.json")
BOOKMARKS_DB = os.path.join(SCRIPT_DIR, "bookmarks.json")
APP_NOTES_DB = os.path.join(SCRIPT_DIR, "app_notes.json")
REVIEWS_DB = os.path.join(SCRIPT_DIR, "company_reviews.json")
SALARY_DB = os.path.join(SCRIPT_DIR, "salary_data.json")
RECENT_SEARCHES_DB = os.path.join(SCRIPT_DIR, "recent_searches.json")

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        CONFIG = json.load(f)
else:
    # Default config for cloud deployment (Render, etc.)
    CONFIG = {
        "search_keywords": ["SOC Analyst", "Cyber Security Analyst", "Security Analyst"],
        "experience_range": {"min_years": 0, "max_years": 5},
        "locations": ["Mumbai", "Pune", "Remote"],
        "resume_path": "",
        "check_interval_minutes": 30,
        "applicant": {"name": "", "email": "", "phone": "", "linkedin_url": ""},
        "brevo": {"api_key": os.environ.get("BREVO_API_KEY", "")},
        "admin_secret": os.environ.get("ADMIN_SECRET", "cyberjobs2026"),
        "auto_apply": False,
        "email_hiring_teams": False,
        "save_results": True,
    }

ua = UserAgent()
app = Flask(__name__)
CORS(app)

# In-memory stores
ALL_JOBS = []
USERS = {}  # {user_id: {name, email, resume_path, applied_jobs: []}}
SCAN_LOCK = threading.Lock()
STATS = {"emails_sent": 0, "applied": 0, "new_today": 0}
OTP_STORE = {}  # {user_id: {"otp": "123456", "expires": datetime, "verified": False}}
SESSIONS = {}  # {session_token: {"user_id": ..., "logged_in_at": ...}}
RESET_TOKENS = {}  # {token: {"user_id": ..., "expires": datetime}}
PUBLIC_URL = None


def validate_password(password):
    """Check password strength: 8+ chars, 1 uppercase, 1 symbol. Returns error message or None."""
    if not password or len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least 1 uppercase letter (A-Z)."
    if not re.search(r'[^A-Za-z0-9]', password):
        return "Password must contain at least 1 symbol (!@#$%^&*...)."
    return None


def generate_otp():
    """Generate a 6-digit OTP."""
    return ''.join(random.choices(string.digits, k=6))


def generate_session_token():
    """Generate a random session token."""
    return hashlib.sha256(os.urandom(32)).hexdigest()


def hash_password(password):
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password, hashed):
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def send_otp_email(to_email, otp, user_name="User"):
    """Send OTP via Brevo transactional email."""
    api_key = CONFIG.get("brevo", {}).get("api_key", "")
    if not api_key or api_key == "YOUR_BREVO_API_KEY":
        log.warning("[OTP] No Brevo API key configured")
        return False

    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px">
<div style="text-align:center;padding:20px;background:linear-gradient(135deg,#1e3a8a,#2563eb);border-radius:12px 12px 0 0">
<h1 style="color:#fff;margin:0;font-size:22px">CyberJobs</h1>
<p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:13px">SOC Job Hunter - Verification</p>
</div>
<div style="background:#fff;border:1px solid #e2e4e9;border-top:none;border-radius:0 0 12px 12px;padding:24px">
<p style="color:#333;font-size:15px">Hello <strong>{user_name}</strong>,</p>
<p style="color:#555;font-size:14px">Your verification code is:</p>
<div style="text-align:center;margin:20px 0">
<span style="font-size:36px;font-weight:800;letter-spacing:8px;color:#2563eb;background:#eff6ff;padding:12px 24px;border-radius:10px;border:2px solid #bfdbfe">{otp}</span>
</div>
<p style="color:#888;font-size:12px;text-align:center">This code expires in <strong>10 minutes</strong>. Do not share it.</p>
</div>
</div>"""

    payload = {
        "sender": {"name": "CyberJobs", "email": CONFIG.get("applicant", {}).get("email", "noreply@cyberjobs.app")},
        "to": [{"email": to_email, "name": user_name}],
        "subject": f"Your CyberJobs Verification Code: {otp}",
        "htmlContent": html_body,
    }
    try:
        resp = requests.post("https://api.brevo.com/v3/smtp/email",
                             headers={"api-key": api_key, "Content-Type": "application/json"},
                             json=payload, timeout=30)
        if resp.status_code in (200, 201):
            log.info(f"[OTP] Email sent to {to_email}")
            return True
        else:
            log.warning(f"[OTP] Email failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"[OTP] Email error: {e}")
    return False


def send_otp_sms(phone, otp, user_name="User"):
    """Send OTP via Brevo transactional SMS."""
    api_key = CONFIG.get("brevo", {}).get("api_key", "")
    if not api_key or api_key == "YOUR_BREVO_API_KEY":
        return False

    # Format phone: ensure +91 prefix for Indian numbers
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        if phone.startswith("91") and len(phone) == 12:
            phone = "+" + phone
        elif len(phone) == 10:
            phone = "+91" + phone
        else:
            phone = "+91" + phone

    payload = {
        "type": "transactional",
        "unicodeEnabled": True,
        "sender": "CyberJobs",
        "recipient": phone,
        "content": f"Your CyberJobs verification code is: {otp}. Valid for 10 minutes. Do not share this code.",
    }
    try:
        resp = requests.post("https://api.brevo.com/v3/transactionalSMS/sms",
                             headers={"api-key": api_key, "Content-Type": "application/json"},
                             json=payload, timeout=30)
        if resp.status_code in (200, 201):
            log.info(f"[OTP] SMS sent to {phone}")
            return True
        else:
            log.warning(f"[OTP] SMS failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"[OTP] SMS error: {e}")
    return False


def clean_google_url(href):
    """Extract actual URL from Google redirect URLs like /url?q=https://...&sa=..."""
    if href.startswith("/url?"):
        parsed = parse_qs(urlparse(href).query)
        if "q" in parsed:
            return unquote(parsed["q"][0])
    if href.startswith("/search?"):
        return ""  # Skip Google internal links
    return href



def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


# ─── Persistence ─────────────────────────────────────────────────────────────

def load_jobs_db():
    global ALL_JOBS, STATS
    if os.path.exists(JOBS_DB):
        with open(JOBS_DB, "r") as f:
            data = json.load(f)
            ALL_JOBS = data.get("jobs", [])
            STATS = data.get("stats", STATS)
    # Fix any Google redirect URLs from previous scans
    fixed = 0
    for job in ALL_JOBS:
        url = job.get("url", "")
        if url.startswith("/url?"):
            cleaned = clean_google_url(url)
            if cleaned:
                job["url"] = cleaned
                fixed += 1
    if fixed:
        log.info(f"[Load] Fixed {fixed} Google redirect URLs")
        save_jobs_db()


def save_jobs_db():
    with open(JOBS_DB, "w") as f:
        json.dump({"jobs": ALL_JOBS, "stats": STATS}, f, indent=2, default=str)


def load_emailed():
    if os.path.exists(EMAILED_DB):
        with open(EMAILED_DB, "r") as f:
            return json.load(f)
    return []


def save_emailed(data):
    with open(EMAILED_DB, "w") as f:
        json.dump(data, f)


def load_users():
    global USERS
    if os.path.exists(USERS_DB):
        with open(USERS_DB, "r") as f:
            USERS = json.load(f)
    # Add default user (Abhishek)
    if "abhishek" not in USERS:
        USERS["abhishek"] = {
            "name": "Abhishek Babariya",
            "email": "abhibabariya007@gmail.com",
            "resume_path": CONFIG.get("resume_path", ""),
            "applied_jobs": [],
        }
        save_users()


def save_users():
    with open(USERS_DB, "w") as f:
        json.dump(USERS, f, indent=2)


def load_app_log():
    if os.path.exists(APP_LOG_DB):
        with open(APP_LOG_DB, "r") as f:
            return json.load(f)
    return []


def save_app_log(data):
    with open(APP_LOG_DB, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_bookmarks():
    if os.path.exists(BOOKMARKS_DB):
        with open(BOOKMARKS_DB, "r") as f:
            return json.load(f)
    return {}


def save_bookmarks(data):
    with open(BOOKMARKS_DB, "w") as f:
        json.dump(data, f, indent=2)


def load_app_notes():
    if os.path.exists(APP_NOTES_DB):
        with open(APP_NOTES_DB, "r") as f:
            return json.load(f)
    return {}


def save_app_notes(data):
    with open(APP_NOTES_DB, "w") as f:
        json.dump(data, f, indent=2)


def load_reviews():
    if os.path.exists(REVIEWS_DB):
        with open(REVIEWS_DB, "r") as f:
            return json.load(f)
    return []


def save_reviews(data):
    with open(REVIEWS_DB, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_salary_data():
    if os.path.exists(SALARY_DB):
        with open(SALARY_DB, "r") as f:
            return json.load(f)
    return []


def save_salary_data(data):
    with open(SALARY_DB, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_recent_searches():
    if os.path.exists(RECENT_SEARCHES_DB):
        with open(RECENT_SEARCHES_DB, "r") as f:
            return json.load(f)
    return {}


def save_recent_searches(data):
    with open(RECENT_SEARCHES_DB, "w") as f:
        json.dump(data, f, indent=2)


def categorize_job(title):
    """Categorize a job based on title keywords."""
    t = title.lower()
    if "soc" in t:
        return "SOC"
    if re.search(r'pentest|ethical hack', t):
        return "Pentesting"
    if re.search(r'grc|compliance|audit', t):
        return "GRC"
    if "cloud" in t:
        return "Cloud Security"
    if "network" in t:
        return "Network Security"
    if re.search(r'incident|dfir|forensic', t):
        return "DFIR"
    if "devsecops" in t:
        return "DevSecOps"
    if re.search(r'iam|identity', t):
        return "IAM"
    return "Security"


def compute_freshness(found_at_str):
    """Compute freshness label based on when the job was found."""
    if not found_at_str:
        return None
    try:
        found_at = datetime.fromisoformat(found_at_str.replace("Z", "+00:00").split("+")[0])
        delta = datetime.now() - found_at
        hours = delta.total_seconds() / 3600
        if hours < 4:
            return "hot"
        if hours < 24:
            return "new"
        if hours < 72:
            return "recent"
    except Exception:
        pass
    return None


def compute_profile_score(user):
    """Compute profile completeness score (0-100)."""
    score = 0
    if user.get("name"):
        score += 10
    if user.get("email"):
        score += 10
    if user.get("phone"):
        score += 10
    if user.get("resume_path") and os.path.exists(user.get("resume_path", "")):
        score += 20
    profile = user.get("profile", {})
    if profile.get("skills"):
        score += 15
    if profile.get("bio"):
        score += 10
    if profile.get("linkedin"):
        score += 10
    if profile.get("location"):
        score += 5
    if profile.get("experience"):
        score += 5
    if user.get("verified"):
        score += 5
    return score


def log_application(user_id, job, email_addr, brevo_response_ok):
    """Log each individual email send attempt with details for tracking."""
    app_log = load_app_log()
    app_log.append({
        "user_id": user_id,
        "job_title": job.get("title", ""),
        "company": job.get("company", ""),
        "platform": job.get("platform", ""),
        "job_url": job.get("url", ""),
        "sent_to": email_addr,
        "sent_at": datetime.now().isoformat(),
        "brevo_accepted": brevo_response_ok,
    })
    save_app_log(app_log)


# ─── Matching Logic ─────────────────────────────────────────────────────────

# Broad cybersecurity keyword sets for matching
CYBER_TITLE_KEYWORDS = [
    "soc", "security", "cyber", "cybersecurity", "infosec", "information security",
    "network security", "cloud security", "application security", "endpoint security",
    "threat", "incident", "vulnerability", "penetration", "ethical hack",
    "siem", "splunk", "qradar", "sentinel", "crowdstrike", "edr",
    "blue team", "red team", "purple team", "forensic", "malware",
    "grc", "governance", "compliance", "iso 27001", "audit",
    "devsecops", "iam", "identity", "firewall", "ids", "ips",
    "dlp", "data loss", "vapt", "bug bounty", "security research",
    "noc", "csoc", "dfir", "osint", "ctf",
]

CYBER_ROLE_WORDS = [
    "analyst", "engineer", "consultant", "specialist", "architect",
    "administrator", "operator", "manager", "lead", "associate",
    "intern", "trainee", "fresher", "junior", "senior",
    "tester", "hacker", "hunter", "researcher", "auditor",
]


def matches_experience(text):
    """Accept all experience levels up to max_years in config. Very permissive."""
    text_lower = text.lower()
    max_yr = CONFIG["experience_range"]["max_years"]

    if re.search(r'fresher|entry[\s-]*level|graduate|trainee|intern', text_lower):
        return True

    patterns = [
        r'(\d+)\s*[-\u2013to]+\s*(\d+)\s*(?:years?|yrs?)',
        r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)',
        r'(?:minimum|min|at\s*least)\s*(\d+)\s*(?:years?|yrs?)',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text_lower):
            groups = [g for g in match.groups() if g is not None]
            if len(groups) >= 2:
                low = int(groups[0])
                if low <= max_yr:
                    return True
            elif len(groups) == 1:
                if int(groups[0]) <= max_yr:
                    return True

    # If no experience mentioned at all, accept it
    if not re.search(r'(\d+)\s*(?:years?|yrs?)', text_lower):
        return True

    return False


def matches_keywords(text):
    """Broad match — any cybersecurity related title."""
    text_lower = text.lower()

    # Direct keyword match from config
    for kw in CONFIG["search_keywords"]:
        if kw.lower() in text_lower:
            return True

    # Any cyber keyword + any role word = match
    has_cyber = any(kw in text_lower for kw in CYBER_TITLE_KEYWORDS)
    has_role = any(rw in text_lower for rw in CYBER_ROLE_WORDS)
    if has_cyber and has_role:
        return True

    # Specific tool mentions with context
    tools = ["splunk", "qradar", "sentinel", "crowdstrike", "palo alto",
             "fortinet", "checkpoint", "wireshark", "nessus", "burp suite",
             "metasploit", "tenable", "qualys", "rapid7", "carbon black",
             "sentinelone", "defender", "elastic security", "wazuh", "suricata"]
    if any(t in text_lower for t in tools):
        return True

    # Cert mentions = likely cyber role
    certs = ["ceh", "oscp", "cissp", "comptia security", "security+",
             "cisa", "cism", "btl1", "ejpt", "ecsa", "gpen", "gcih", "gsec"]
    if any(c in text_lower for c in certs):
        return True

    return False


def is_cybersecurity_job(title, full_text=""):
    """Ultimate check — is this a cybersecurity job? Very broad."""
    combined = f"{title} {full_text}".lower()
    return matches_keywords(combined)


def job_hash(title, company, platform):
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{platform}"
    return hashlib.md5(raw.encode()).hexdigest()


# ─── Selenium Scraper ────────────────────────────────────────────────────────

def kill_zombie_browsers():
    """Kill stale chromedriver and headless Chrome processes."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chromedriver.exe"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            )
        except Exception:
            pass
    else:
        try:
            subprocess.run(["pkill", "-f", "chromedriver"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pname = (proc.info.get('name') or '').lower()
                if pname in ('chrome', 'chrome.exe', 'chromium', 'chromium-browser'):
                    cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                    if '--headless' in cmdline:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass


def force_quit_driver(driver):
    """Forcefully quit a Selenium driver and all its child processes."""
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass
    # Force kill chromedriver + headless Chrome, then wait for cleanup
    kill_zombie_browsers()
    time.sleep(2)


_CHROMEDRIVER_PATH = None  # Cached chromedriver path
_CHROMEDRIVER_LOCK = threading.Lock()  # Prevent race condition on download


def _get_chromedriver_path():
    global _CHROMEDRIVER_PATH
    with _CHROMEDRIVER_LOCK:
        if _CHROMEDRIVER_PATH and os.path.exists(_CHROMEDRIVER_PATH):
            return _CHROMEDRIVER_PATH
        _CHROMEDRIVER_PATH = ChromeDriverManager().install()
        return _CHROMEDRIVER_PATH


def pre_download_chromedriver():
    """Pre-download chromedriver before any parallel scrapers run. Prevents race conditions."""
    try:
        path = _get_chromedriver_path()
        log.info(f"[ChromeDriver] Ready at: {path}")
        return path
    except Exception as e:
        log.error(f"[ChromeDriver] Pre-download failed: {e}")
        return None


def create_driver():
    if not SELENIUM_AVAILABLE:
        return None
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"user-agent={ua.random}")
    for attempt in range(3):
        try:
            service = Service(_get_chromedriver_path())
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            if STEALTH_AVAILABLE:
                apply_stealth(driver, languages=["en-US", "en"], vendor="Google Inc.",
                              platform="Win32", webgl_vendor="Intel Inc.",
                              renderer="Intel Iris OpenGL Engine", fix_hairline=True)
            driver.set_page_load_timeout(20)
            driver.set_script_timeout(15)
            return driver
        except Exception as e:
            log.error(f"Driver error (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                kill_zombie_browsers()
                time.sleep(3)
    return None


def selenium_scrape_multi(urls_with_wait, page_timeout=30):
    """Scrape multiple URLs using a SINGLE Chrome driver. Returns list of (url, soup) tuples."""
    driver = create_driver()
    if not driver:
        return [(url, None) for url, _ in urls_with_wait]
    results = []
    try:
        for url, wait in urls_with_wait:
            try:
                # Thread-based page load timeout
                page_result = [None]

                def _load_page(d=driver, u=url, w=wait, pr=page_result):
                    try:
                        d.get(u)
                        time.sleep(w)
                        d.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(1)
                        pr[0] = BeautifulSoup(d.page_source, "html.parser")
                    except Exception:
                        pr[0] = None

                t = threading.Thread(target=_load_page, daemon=True)
                t.start()
                t.join(timeout=page_timeout)
                if t.is_alive():
                    log.warning(f"Selenium page timeout for {url}")
                    results.append((url, None))
                    # Driver is stuck, kill and recreate
                    force_quit_driver(driver)
                    driver = create_driver()
                    if not driver:
                        break
                else:
                    results.append((url, page_result[0]))
            except Exception as e:
                log.error(f"Selenium error for {url}: {e}")
                results.append((url, None))
    finally:
        force_quit_driver(driver)
    return results


# ─── Search Keywords for Scrapers ─────────────────────────────────────────────

SCRAPE_KEYWORDS_MAIN = [
    "SOC Analyst", "Cyber Security Analyst", "Cybersecurity Analyst",
    "Information Security Analyst", "Security Analyst", "Security Engineer",
    "Network Security Analyst", "Cloud Security Analyst", "SIEM Analyst",
    "Threat Analyst", "Incident Response Analyst", "Blue Team Analyst",
    "Penetration Tester", "Ethical Hacker", "Vulnerability Analyst",
    "Security Consultant", "GRC Analyst", "Malware Analyst",
    "DevSecOps", "Application Security", "Endpoint Security",
    "SOC Engineer", "Security Operations", "Cyber Security",
    "Information Security", "Network Security Engineer",
]

SCRAPE_KEYWORDS_EXTRA = [
    "SIEM Engineer", "Threat Intelligence", "Threat Hunter",
    "Red Team", "VAPT", "Compliance Analyst", "Security Auditor",
    "Forensics Analyst", "Digital Forensics", "Firewall Engineer",
    "IAM Analyst", "Splunk", "QRadar", "Sentinel", "CrowdStrike",
    "Data Security", "DLP Analyst", "Security Architect",
    "Cyber Risk", "Security Researcher", "EDR Analyst",
    "Junior Security", "Purple Team", "Bug Bounty",
]

SCRAPE_LOCATIONS = CONFIG.get("locations", ["India", "Remote"])


def _http_get(url, headers=None, timeout=15):
    """Safe HTTP GET with retries."""
    hdrs = headers or get_headers()
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                time.sleep(3)
                continue
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return None


# ─── Scrapers (API-based, NO Selenium for main portals = 10x faster) ─────────

def scrape_linkedin():
    """Scrape LinkedIn using their public guest jobs API — no login, no Selenium, 25 jobs/page."""
    log.info("[LinkedIn] Scanning via guest API...")
    jobs = []
    seen = set()

    for keyword in SCRAPE_KEYWORDS_MAIN + SCRAPE_KEYWORDS_EXTRA[:10]:
        for start in range(0, 200, 25):  # 8 pages x 25 = 200 per keyword
            try:
                params = {"keywords": keyword, "location": "India", "start": start,
                          "f_TPR": "r2592000", "sortBy": "R"}  # Last 30 days
                url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{urlencode(params)}"
                resp = _http_get(url)
                if not resp:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li")
                if not cards:
                    cards = soup.find_all("div", class_=re.compile(r"base-card|job-search-card"))
                if not cards:
                    break  # No more results for this keyword

                found_any = False
                for card in cards:
                    title_tag = card.find(["h3", "a", "span"], class_=re.compile(r"base-search-card__title|title"))
                    company_tag = card.find(["h4", "a", "span"], class_=re.compile(r"base-search-card__subtitle|company"))
                    loc_tag = card.find("span", class_=re.compile(r"job-search-card__location|location"))
                    link_tag = card.find("a", class_=re.compile(r"base-card__full-link|base-search-card--link"))
                    if not link_tag:
                        link_tag = card.find("a", href=re.compile(r"linkedin\.com/jobs/view"))
                    date_tag = card.find("time")

                    title = title_tag.get_text(strip=True) if title_tag else ""
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    location = loc_tag.get_text(strip=True) if loc_tag else "India"
                    link = link_tag["href"].split("?")[0] if link_tag and link_tag.get("href") else ""
                    posted = date_tag.get("datetime", "") if date_tag else ""

                    if not title or len(title) < 3:
                        continue

                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    found_any = True

                    if is_cybersecurity_job(title):
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "LinkedIn", "location": location,
                            "posted_date": posted,
                            "found_at": datetime.now().isoformat(),
                        })
                if not found_any:
                    break  # No more results
                time.sleep(0.3)
            except Exception as e:
                log.error(f"[LinkedIn API] {e}")
                break

    log.info(f"[LinkedIn] Found {len(jobs)} (API)")
    return jobs


def scrape_indeed():
    """Scrape Indeed India — quick Selenium scan with broad keywords."""
    log.info("[Indeed] Scanning via Selenium stealth...")
    jobs = []
    seen = set()
    driver = create_driver()
    if not driver:
        log.warning("[Indeed] No Selenium driver available, skipping.")
        return jobs

    # Quick broad keywords — Indeed India has limited cyber jobs
    indeed_queries = [
        "cyber security", "SOC analyst", "security analyst",
        "security engineer", "penetration tester", "information security",
        "SIEM", "threat analyst", "incident response", "devsecops",
    ]

    try:
        for query in indeed_queries:
            for start in range(0, 50, 10):  # 5 pages max per keyword
                try:
                    params = {"q": query, "l": "India", "fromage": "14", "sort": "date", "start": start}
                    url = f"https://in.indeed.com/jobs?{urlencode(params)}"
                    try:
                        driver.get(url)
                    except Exception:
                        break
                    time.sleep(2.5)

                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|cardOutline|tapItem"))
                    if not cards:
                        break

                    new_count = 0
                    for card in cards:
                        title_tag = card.find(["h2", "a", "span"], class_=re.compile(r"jobTitle|title|jcs-JobTitle"))
                        company_tag = card.find(["span", "a"], class_=re.compile(r"company|companyName"))
                        if not company_tag:
                            company_tag = card.find(attrs={"data-testid": re.compile(r"company-name")})
                        loc_tag = card.find(["div", "span"], class_=re.compile(r"companyLocation|location"))
                        sal_tag = card.find(["div", "span"], class_=re.compile(r"salary|estimated-salary"))
                        snippet_tag = card.find(["div", "ul"], class_=re.compile(r"job-snippet|underShelfFooter"))

                        title = title_tag.get_text(strip=True) if title_tag else ""
                        company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                        location = loc_tag.get_text(strip=True) if loc_tag else "India"
                        salary = sal_tag.get_text(strip=True) if sal_tag else ""
                        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

                        link_tag = card.find("a", href=re.compile(r"/rc/clk|viewjob|/pagead"))
                        if not link_tag:
                            link_tag = card.find("a", href=True)
                        href = ""
                        if link_tag:
                            href = link_tag.get("href", "")
                            if href and not href.startswith("http"):
                                href = "https://in.indeed.com" + href

                        if not title or len(title) < 3:
                            continue
                        dedup_key = f"{title.lower()}|{company.lower()}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        new_count += 1

                        full_text = f"{title} {snippet} {card.get_text(' ', strip=True)}"
                        if is_cybersecurity_job(title, full_text):
                            jobs.append({
                                "title": title, "company": company, "url": href,
                                "platform": "Indeed", "location": location,
                                "salary": salary, "snippet": snippet[:300],
                                "found_at": datetime.now().isoformat(),
                            })
                    if new_count == 0:
                        break
                except Exception as e:
                    log.error(f"[Indeed] {e}")
                    break
        log.info(f"[Indeed] Found {len(jobs)} (Selenium)")
    finally:
        try:
            force_quit_driver(driver)
        except Exception:
            pass
    return jobs


def _naukri_load_page(driver, url, result_holder):
    """Load a page in a thread so we can timeout."""
    try:
        driver.get(url)
        result_holder["ok"] = True
    except Exception as e:
        result_holder["error"] = str(e)


def _naukri_parse_cards(soup, seen, jobs):
    """Parse Naukri job cards from a page soup. Returns count of new unique cards."""
    cards = soup.find_all("div", class_=re.compile(r"srp-jobtuple|cust-job-tuple|styles_jlc"))
    if not cards:
        cards = soup.find_all("article", class_=re.compile(r"jobTuple"))
    if not cards:
        link_parents = []
        for link in soup.find_all("a", href=re.compile(r"naukri\.com/job-listings|/job/")):
            parent = link.find_parent(["div", "article", "li"])
            if parent and parent not in link_parents:
                link_parents.append(parent)
        cards = link_parents

    new_count = 0
    for card in cards:
        title_tag = card.find("a", class_=re.compile(r"title|desig|jobTitle"))
        if not title_tag:
            title_tag = card.find("a", href=re.compile(r"job-listings|/job/"))
        company_tag = card.find("a", class_=re.compile(r"subTitle|comp-name|companyInfo"))
        if not company_tag:
            company_tag = card.find("span", class_=re.compile(r"comp-name|company"))
        exp_tag = card.find("span", class_=re.compile(r"expwdth|exp|experience|ni-job-tuple-icon-srp-experience"))
        loc_tag = card.find("span", class_=re.compile(r"locWdth|loc|location|ni-job-tuple-icon-srp-location"))
        sal_tag = card.find("span", class_=re.compile(r"salWdth|sal|salary|ni-job-tuple-icon-srp-rupee"))
        snippet_tag = card.find(["div", "span"], class_=re.compile(r"job-desc|ellipsis|row3|description"))
        skills_tag = card.find(["ul", "div"], class_=re.compile(r"tags|skills|tag-li"))

        title = title_tag.get_text(strip=True) if title_tag else ""
        company = company_tag.get_text(strip=True) if company_tag else "Unknown"
        location = loc_tag.get_text(strip=True) if loc_tag else "India"
        experience = exp_tag.get_text(strip=True) if exp_tag else ""
        salary = sal_tag.get_text(strip=True) if sal_tag else ""
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
        skills = skills_tag.get_text(", ", strip=True) if skills_tag else ""

        link_href = ""
        if title_tag and title_tag.get("href"):
            link_href = title_tag["href"]
            if not link_href.startswith("http"):
                link_href = "https://www.naukri.com" + link_href

        if not title or len(title) < 3:
            continue
        dedup_key = f"{title.lower()}|{company.lower()}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        new_count += 1

        full_text = f"{title} {snippet} {skills} {card.get_text(' ', strip=True)}"
        if is_cybersecurity_job(title, full_text):
            jobs.append({
                "title": title, "company": company, "url": link_href,
                "platform": "Naukri", "location": location,
                "experience": experience, "salary": salary,
                "snippet": f"{snippet[:250]} | Skills: {skills}" if skills else snippet[:300],
                "found_at": datetime.now().isoformat(),
            })
    return new_count


def scrape_naukri():
    """Scrape Naukri using Selenium stealth — fresh driver per batch to avoid hangs."""
    log.info("[Naukri] Scanning via Selenium stealth...")
    jobs = []
    seen = set()

    exp_max = CONFIG["experience_range"]["max_years"]
    naukri_slugs = [
        "soc-analyst", "cyber-security-analyst", "cybersecurity-analyst",
        "information-security-analyst", "security-analyst", "security-engineer",
        "network-security", "cloud-security", "siem-analyst", "siem-engineer",
        "threat-analyst", "threat-intelligence", "incident-response",
        "penetration-tester", "ethical-hacker", "vulnerability-analyst",
        "vapt", "devsecops", "application-security", "endpoint-security",
        "malware-analyst", "forensics-analyst", "grc-analyst",
        "compliance-analyst", "security-consultant", "firewall-engineer",
        "splunk-analyst", "soc-engineer", "security-operations",
        "security-auditor", "cyber-defense",
    ]

    BATCH_SIZE = 5  # Create a fresh driver every 5 slugs
    PAGE_TIMEOUT = 25  # Max seconds to wait for a page load

    for batch_start in range(0, len(naukri_slugs), BATCH_SIZE):
        batch = naukri_slugs[batch_start:batch_start + BATCH_SIZE]
        driver = create_driver()
        if not driver:
            log.warning("[Naukri] No Selenium driver available, skipping batch.")
            continue

        try:
            for slug in batch:
                empty_pages = 0
                for page in range(1, 11):  # Up to 10 pages per slug
                    try:
                        url = f"https://www.naukri.com/{slug}-jobs-{page}?experience=0&experience={exp_max}"

                        # Load page with thread-based timeout
                        result = {}
                        loader = threading.Thread(target=_naukri_load_page, args=(driver, url, result))
                        loader.start()
                        loader.join(timeout=PAGE_TIMEOUT)

                        if loader.is_alive():
                            # Page load hung — force kill driver and clean up
                            log.warning(f"[Naukri] Page load hung on {slug} p{page}, killing driver")
                            try:
                                force_quit_driver(driver)
                            except Exception:
                                pass
                            kill_zombie_browsers()
                            driver = None
                            break

                        if "error" in result:
                            log.warning(f"[Naukri] Load error on {slug} p{page}: {result['error'][:60]}")
                            break

                        time.sleep(1.5)
                        soup = BeautifulSoup(driver.page_source, "html.parser")
                        new_count = _naukri_parse_cards(soup, seen, jobs)

                        if new_count == 0:
                            empty_pages += 1
                            if empty_pages >= 2:
                                break
                        else:
                            empty_pages = 0
                        time.sleep(0.5)
                    except Exception as e:
                        log.error(f"[Naukri] Page error: {e}")
                        break

                if driver is None:
                    # Driver was killed due to hang, create new one for remaining slugs
                    driver = create_driver()
                    if not driver:
                        break

                log.info(f"[Naukri] Slug '{slug}' done, {len(jobs)} total so far")
        finally:
            try:
                if driver:
                    force_quit_driver(driver)
            except Exception:
                pass

    log.info(f"[Naukri] Found {len(jobs)} total (Selenium)")
    return jobs


def scrape_glassdoor():
    """Scrape Glassdoor India — uses Selenium for JS rendering."""
    log.info("[Glassdoor] Scanning...")
    jobs = []
    seen = set()
    gd_keywords = [
        "SOC Analyst", "Cyber Security", "Security Analyst",
        "Security Engineer", "Threat Analyst", "SIEM",
        "Information Security", "Network Security", "Penetration Tester",
        "Incident Response", "Cloud Security", "DevSecOps",
    ]
    urls = []
    for keyword in gd_keywords:
        encoded = quote_plus(keyword)
        slug = keyword.lower().replace(" ", "-")
        for page in range(1, 4):  # 3 pages
            suffix = f"_IP{page}.htm" if page > 1 else ".htm"
            urls.append((f"https://www.glassdoor.co.in/Job/india-{slug}-jobs-SRCH_IL.0,5_IN115{suffix}?keyword={encoded}", 4))

    for i in range(0, len(urls), 4):
        batch = urls[i:i+4]
        for url, soup in selenium_scrape_multi(batch):
            if not soup:
                continue
            try:
                cards = soup.find_all("li", class_=re.compile(r"JobsList_jobListItem|react-job-listing"))
                if not cards:
                    cards = soup.find_all("div", class_=re.compile(r"jobCard|JobCard"))
                for card in cards:
                    title_tag = card.find(["a", "div"], class_=re.compile(r"jobTitle|JobCard_jobTitle|job-title"))
                    if not title_tag:
                        title_tag = card.find(attrs={"data-test": re.compile(r"job-title")})
                    company_tag = card.find(["div", "span"], class_=re.compile(r"employer|EmployerProfile|companyName"))
                    if not company_tag:
                        company_tag = card.find(attrs={"data-test": re.compile(r"emp-name")})
                    loc_tag = card.find(["div", "span"], class_=re.compile(r"location|loc"))
                    sal_tag = card.find(["div", "span"], class_=re.compile(r"salary|SalaryEstimate"))

                    title = title_tag.get_text(strip=True) if title_tag else ""
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    location = loc_tag.get_text(strip=True) if loc_tag else "India"
                    salary = sal_tag.get_text(strip=True) if sal_tag else ""

                    link = ""
                    if title_tag and title_tag.name == "a":
                        link = title_tag.get("href", "")
                    else:
                        el = card.find("a", href=True)
                        link = el["href"] if el else ""
                    if link and not link.startswith("http"):
                        link = "https://www.glassdoor.co.in" + link

                    if not title:
                        continue
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    if is_cybersecurity_job(title, card.get_text(" ", strip=True)):
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "Glassdoor", "location": location,
                            "salary": salary,
                            "found_at": datetime.now().isoformat(),
                        })
            except Exception as e:
                log.error(f"[Glassdoor] {e}")
    log.info(f"[Glassdoor] Found {len(jobs)}")
    return jobs


# ─── NEW PORTALS ──────────────────────────────────────────────────────────────

def scrape_google_jobs():
    """Scrape Google Jobs via Selenium + regular Google search via HTTP."""
    log.info("[Google Jobs] Scanning...")
    jobs = []
    seen = set()

    # Part 1: Google Jobs panel (Selenium required for JS)
    gj_queries = [
        "cyber security analyst jobs India", "SOC analyst jobs India",
        "information security analyst jobs", "security engineer jobs India",
        "cybersecurity jobs India", "network security jobs India",
        "threat analyst jobs India", "SIEM analyst jobs India",
        "penetration tester jobs India", "incident response jobs India",
        "vulnerability analyst jobs India", "cloud security jobs India",
        "GRC analyst jobs India", "DevSecOps jobs India",
        "ethical hacker jobs India", "security consultant jobs India",
        "blue team jobs India", "malware analyst jobs India",
        "security operations jobs India", "cyber security fresher jobs",
    ]

    gj_urls = [(f"https://www.google.com/search?q={quote_plus(q)}&ibp=htl;jobs", 5) for q in gj_queries]
    for i in range(0, len(gj_urls), 3):
        batch = gj_urls[i:i+3]
        for url, soup in selenium_scrape_multi(batch):
            if not soup:
                continue
            try:
                cards = soup.find_all("div", class_=re.compile(r"BjJfJf|PwjeAc|gws-plugins"))
                if not cards:
                    cards = soup.find_all("li", class_=re.compile(r"iFjolb"))
                for card in cards:
                    title_tag = card.find(["div", "h2", "span"], class_=re.compile(r"BjJfJf|vNEEBe|title"))
                    company_tag = card.find(["div", "span"], class_=re.compile(r"vNEEBe|nJlDiv|company"))
                    loc_tag = card.find(["div", "span"], class_=re.compile(r"Qk80Jf|location"))
                    sal_tag = card.find(["span", "div"], class_=re.compile(r"oNwCmf|salary"))
                    via_tag = card.find(["span", "div"], class_=re.compile(r"Kp2yc|via|source"))
                    snippet_tag = card.find(["span", "div"], class_=re.compile(r"HBvzbc|description"))

                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title or len(title) < 5:
                        continue
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    if is_cybersecurity_job(title, card.get_text(" ", strip=True)):
                        via = via_tag.get_text(strip=True) if via_tag else ""
                        link_tag = card.find("a", href=True)
                        link = ""
                        if link_tag:
                            href = link_tag["href"]
                            if href.startswith("/url?"):
                                href = clean_google_url(href)
                            if href and href.startswith("http"):
                                link = href
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": f"Google Jobs",
                            "location": loc_tag.get_text(strip=True) if loc_tag else "India",
                            "salary": sal_tag.get_text(strip=True) if sal_tag else "",
                            "snippet": snippet_tag.get_text(strip=True)[:300] if snippet_tag else "",
                            "found_at": datetime.now().isoformat(),
                        })
            except Exception as e:
                log.error(f"[Google Jobs] {e}")

    # Part 2: Regular Google search for job listings (HTTP, fast)
    log.info("[Google Search] Scanning...")
    gsearch_queries = [
        '"cyber security analyst" hiring India',
        '"SOC analyst" openings India 2026',
        '"security engineer" vacancy India',
        '"information security" jobs India fresher',
        '"penetration tester" hiring India',
        '"SIEM analyst" jobs India',
        '"threat analyst" openings India',
        '"cybersecurity" jobs India apply',
    ]
    for q in gsearch_queries:
        try:
            resp = _http_get(f"https://www.google.com/search?q={quote_plus(q)}&num=20")
            if not resp:
                continue
            gsoup = BeautifulSoup(resp.text, "html.parser")
            for result in gsoup.find_all("div", class_="g"):
                link_tag = result.find("a", href=True)
                title_tag = result.find("h3")
                snippet_tag = result.find(["span", "div"], class_=re.compile(r"VwiC3b|st"))
                if not link_tag or not title_tag:
                    continue
                href = link_tag["href"]
                if href.startswith("/url?"):
                    href = clean_google_url(href)
                if not href or not href.startswith("http"):
                    continue
                title_text = title_tag.get_text(strip=True)
                snippet_text = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if is_cybersecurity_job(title_text, snippet_text):
                    platform = "Google Search"
                    for pname, purl in [("Naukri", "naukri.com"), ("Indeed", "indeed.com"),
                                         ("LinkedIn", "linkedin.com"), ("Foundit", "foundit.in"),
                                         ("Shine", "shine.com"), ("TimesJobs", "timesjobs.com")]:
                        if purl in href:
                            platform = pname
                            break
                    dedup_key = href[:100]
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        jobs.append({
                            "title": title_text, "company": "See listing",
                            "url": href, "platform": platform,
                            "snippet": snippet_text[:300],
                            "found_at": datetime.now().isoformat(),
                        })
            time.sleep(2)
        except Exception as e:
            log.error(f"[Google Search] {e}")

    log.info(f"[Google Jobs] Found {len(jobs)} total")
    return jobs


def scrape_foundit():
    """Scrape Foundit.in (Monster India) via Selenium."""
    log.info("[Foundit] Scanning...")
    jobs = []
    seen = set()
    foundit_kw = [
        "soc analyst", "cyber security", "information security",
        "security analyst", "security engineer", "network security",
        "threat analyst", "siem", "penetration testing", "ethical hacking",
        "incident response", "cloud security", "devsecops", "vulnerability",
    ]
    urls = []
    for kw in foundit_kw:
        for page in [1, 2, 3]:
            urls.append((f"https://www.foundit.in/srp/results?query={quote_plus(kw)}&locations=India&experienceRanges=0~5&sort=1&page={page}", 5))

    for i in range(0, len(urls), 4):
        for url, soup in selenium_scrape_multi(urls[i:i+4]):
            if not soup:
                continue
            try:
                cards = soup.find_all("div", class_=re.compile(r"srpResultCardContainer|card-apply-content|jobTuple|cardCont"))
                for card in cards:
                    title_tag = card.find(["a", "h2", "h3", "div"], class_=re.compile(r"jobTitle|title|cardTitle|designation"))
                    company_tag = card.find(["span", "a", "div"], class_=re.compile(r"companyName|company|comp-name"))
                    loc_tag = card.find(["span", "div"], class_=re.compile(r"location|loc|cardLocation"))
                    sal_tag = card.find(["span", "div"], class_=re.compile(r"salary|sal|cardSalary"))
                    exp_tag = card.find(["span", "div"], class_=re.compile(r"experience|exp|cardExp"))

                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title:
                        continue
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    if is_cybersecurity_job(title, card.get_text(" ", strip=True)):
                        link = ""
                        if title_tag and title_tag.name == "a" and title_tag.get("href"):
                            link = title_tag["href"]
                        else:
                            a = card.find("a", href=True)
                            if a:
                                link = a["href"]
                        if link and not link.startswith("http"):
                            link = "https://www.foundit.in" + link
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "Foundit",
                            "location": loc_tag.get_text(strip=True) if loc_tag else "India",
                            "experience": exp_tag.get_text(strip=True) if exp_tag else "",
                            "salary": sal_tag.get_text(strip=True) if sal_tag else "",
                            "found_at": datetime.now().isoformat(),
                        })
            except Exception as e:
                log.error(f"[Foundit] {e}")
    log.info(f"[Foundit] Found {len(jobs)}")
    return jobs


def scrape_simplyhired():
    """Scrape SimplyHired India via Selenium."""
    log.info("[SimplyHired] Scanning...")
    jobs = []
    seen = set()
    sh_kw = [
        "cyber security", "SOC analyst", "security engineer",
        "information security", "network security", "threat analyst",
        "SIEM", "penetration tester", "incident response", "cybersecurity",
    ]
    urls = []
    for kw in sh_kw:
        for pn in [1, 2, 3]:
            urls.append((f"https://www.simplyhired.co.in/search?q={quote_plus(kw)}&l=India&pn={pn}", 4))

    for i in range(0, len(urls), 4):
        for url, soup in selenium_scrape_multi(urls[i:i+4]):
            if not soup:
                continue
            try:
                cards = soup.find_all(["article", "div", "li"], class_=re.compile(r"SerpJob|jobCard|result-card"))
                for card in cards:
                    title_tag = card.find(["a", "h2", "h3"], class_=re.compile(r"jobTitle|title|SerpJob-link"))
                    company_tag = card.find(["span", "a"], class_=re.compile(r"company|companyName|jobposting-company"))
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title:
                        continue
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    if is_cybersecurity_job(title, card.get_text(" ", strip=True)):
                        link = ""
                        if title_tag and title_tag.name == "a":
                            link = title_tag.get("href", "")
                        if link and not link.startswith("http"):
                            link = "https://www.simplyhired.co.in" + link
                        loc_tag = card.find(["span", "div"], class_=re.compile(r"location|loc"))
                        sal_tag = card.find(["span", "div"], class_=re.compile(r"salary"))
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "SimplyHired",
                            "location": loc_tag.get_text(strip=True) if loc_tag else "India",
                            "salary": sal_tag.get_text(strip=True) if sal_tag else "",
                            "found_at": datetime.now().isoformat(),
                        })
            except Exception as e:
                log.error(f"[SimplyHired] {e}")
    log.info(f"[SimplyHired] Found {len(jobs)}")
    return jobs


def scrape_timesjobs():
    """Scrape TimesJobs — pure HTTP, no Selenium, deep pagination."""
    log.info("[TimesJobs] Scanning...")
    jobs = []
    seen = set()
    tj_keywords = [
        "cyber+security", "SOC+analyst", "security+analyst", "information+security",
        "network+security", "SIEM", "threat+analyst", "penetration+testing",
        "security+engineer", "incident+response", "ethical+hacking",
        "vulnerability+assessment", "cloud+security", "devsecops",
        "cybersecurity", "security+consultant", "GRC", "malware+analyst",
    ]
    for kw in tj_keywords:
        for page in range(1, 6):  # 5 pages per keyword (reduced from 10)
            try:
                url = f"https://www.timesjobs.com/candidate/job-search.html?searchType=personalizedSearch&from=submit&txtKeywords={kw}&txtLocation=India&sequence={page}&startPage={page}"
                resp = _http_get(url)
                if not resp:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li", class_=re.compile(r"clearfix|job-bx"))
                if not cards:
                    break
                for card in cards:
                    title_tag = card.find(["a", "h2"], class_=re.compile(r"heading|title"))
                    if not title_tag:
                        hdr = card.find("header", class_=re.compile(r"clearfix"))
                        if hdr:
                            title_tag = hdr.find("a")
                    company_tag = card.find(["h3", "span"], class_=re.compile(r"comp-name|company"))
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title:
                        continue
                    company = company_tag.get_text(strip=True).replace("\r", "").replace("\n", "").strip() if company_tag else "Unknown"
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    full_text = card.get_text(" ", strip=True)
                    if is_cybersecurity_job(title, full_text):
                        link = title_tag.get("href", "") if title_tag and title_tag.name == "a" else ""
                        if link and not link.startswith("http"):
                            link = "https://www.timesjobs.com" + link
                        loc_tag = card.find(["span", "ul"], class_=re.compile(r"loc|location"))
                        exp_tag = card.find("span", class_=re.compile(r"exp|experience"))
                        sal_tag = card.find("span", class_=re.compile(r"sal|salary"))
                        desc_tag = card.find(["label", "div"], class_=re.compile(r"clearfix|desc"))
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "TimesJobs",
                            "location": loc_tag.get_text(strip=True) if loc_tag else "India",
                            "experience": exp_tag.get_text(strip=True) if exp_tag else "",
                            "salary": sal_tag.get_text(strip=True) if sal_tag else "",
                            "snippet": desc_tag.get_text(strip=True)[:300] if desc_tag else "",
                            "found_at": datetime.now().isoformat(),
                        })
                time.sleep(0.5)
            except Exception as e:
                log.error(f"[TimesJobs] {e}")
                break
    log.info(f"[TimesJobs] Found {len(jobs)}")
    return jobs


def scrape_shine():
    """Scrape Shine.com via Selenium."""
    log.info("[Shine] Scanning...")
    jobs = []
    seen = set()
    shine_kw = [
        "cyber-security", "soc-analyst", "information-security",
        "security-analyst", "security-engineer", "network-security",
        "siem", "threat-analyst", "penetration-testing", "ethical-hacking",
    ]
    urls = []
    for kw in shine_kw:
        for pg in range(1, 4):
            suffix = f"-{pg}" if pg > 1 else ""
            urls.append((f"https://www.shine.com/job-search/{kw}-jobs{suffix}", 4))

    for i in range(0, len(urls), 4):
        for url, soup in selenium_scrape_multi(urls[i:i+4]):
            if not soup:
                continue
            try:
                cards = soup.find_all(["div", "li"], class_=re.compile(r"result_card|job_card|jobCard|listView"))
                for card in cards:
                    title_tag = card.find(["a", "h2", "h3"], class_=re.compile(r"title|name|designation|job_title"))
                    company_tag = card.find(["span", "a", "div"], class_=re.compile(r"company|comp|org"))
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title:
                        continue
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    dedup_key = f"{title.lower()}|{company.lower()}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    if is_cybersecurity_job(title, card.get_text(" ", strip=True)):
                        link = title_tag.get("href", "") if title_tag and title_tag.name == "a" else ""
                        if link and not link.startswith("http"):
                            link = "https://www.shine.com" + link
                        loc_tag = card.find(["span", "div"], class_=re.compile(r"loc|location"))
                        exp_tag = card.find(["span", "div"], class_=re.compile(r"exp|experience"))
                        sal_tag = card.find(["span", "div"], class_=re.compile(r"sal|salary|ctc"))
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "Shine",
                            "location": loc_tag.get_text(strip=True) if loc_tag else "India",
                            "experience": exp_tag.get_text(strip=True) if exp_tag else "",
                            "salary": sal_tag.get_text(strip=True) if sal_tag else "",
                            "found_at": datetime.now().isoformat(),
                        })
            except Exception as e:
                log.error(f"[Shine] {e}")
    log.info(f"[Shine] Found {len(jobs)}")
    return jobs


# ─── Email Functions ─────────────────────────────────────────────────────────

def extract_emails_from_page(url):
    if not url:
        return []
    try:
        resp = requests.get(url, headers=get_headers(), timeout=5, allow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            found = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
            blacklist = ['example.com', 'naukri.com', 'indeed.com', 'linkedin.com',
                         'glassdoor.com', 'google.com', 'facebook.com', 'schema.org',
                         'w3.org', 'mozilla.org', 'cloudflare', 'amazonaws', 'sentry.io',
                         'jquery.com', 'github.com', 'apple.com', 'microsoft.com']
            emails = [e.lower() for e in found if not any(b in e.lower() for b in blacklist) and len(e) > 5]
            for a in soup.find_all("a", href=re.compile(r"mailto:")):
                email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                if email and "@" in email:
                    emails.append(email)
            return list(set(emails))
    except Exception:
        pass
    return []


def _domain_has_mx(domain):
    """Check if a domain has MX records (can receive email). Cached."""
    if not hasattr(_domain_has_mx, '_cache'):
        _domain_has_mx._cache = {}
    if domain in _domain_has_mx._cache:
        return _domain_has_mx._cache[domain]
    try:
        import socket
        import dns.resolver
        try:
            answers = dns.resolver.resolve(domain, 'MX')
            result = len(answers) > 0
        except Exception:
            # Fallback: try A record (some domains accept mail without MX)
            try:
                socket.getaddrinfo(domain, 25, socket.AF_INET)
                result = True
            except Exception:
                result = False
    except ImportError:
        # dnspython not installed — fallback to socket check
        import socket
        try:
            socket.getaddrinfo(domain, 25, socket.AF_INET)
            result = True
        except Exception:
            result = False
    _domain_has_mx._cache[domain] = result
    return result


def guess_hr_emails(company):
    if not company or company == "Unknown":
        return []
    clean = re.sub(r'\s*(pvt|private|ltd|limited|inc|corp|llp|solutions|technologies|tech|services)\s*\.?\s*', '', company.lower())
    clean = re.sub(r'[^a-z0-9]', '', clean.strip())
    if len(clean) < 3:
        return []
    candidate_domains = [f"{clean}.com", f"{clean}.in", f"{clean}.co.in"]
    # Only use domains that can actually receive email
    valid_domains = [d for d in candidate_domains if _domain_has_mx(d)]
    if not valid_domains:
        log.debug(f"[Email] No valid MX domains found for company '{company}' (tried: {candidate_domains})")
        return []
    prefixes = ["hr", "careers", "recruitment", "hiring", "jobs", "talent"]
    return [f"{p}@{d}" for d in valid_domains for p in prefixes]


# ─── Resume Keyword Extraction & Job Matching ────────────────────────────────

_RESUME_KEYWORDS_CACHE = {}  # user_id -> set of keywords

MATCH_KEYWORDS = [
    "soc", "siem", "splunk", "qradar", "sentinel", "fortisiem", "arcsight",
    "edr", "xdr", "crowdstrike", "falcon", "carbon black", "defender",
    "ids", "ips", "firewall", "palo alto", "fortinet", "checkpoint",
    "incident response", "threat hunting", "threat intelligence", "malware analysis",
    "phishing", "vulnerability", "penetration testing", "vapt", "osint",
    "log analysis", "alert triage", "forensic", "dfir",
    "mitre att&ck", "mitre", "cyber kill chain", "nist", "iso 27001",
    "active directory", "windows", "linux", "networking", "tcp/ip", "dns",
    "python", "bash", "powershell", "wireshark", "nmap", "burp suite",
    "grc", "compliance", "audit", "risk assessment",
    "sysmon", "nxlog", "elastic", "kibana", "grafana",
    "cloud security", "aws", "azure", "gcp",
    "security analyst", "soc analyst", "cyber security", "cybersecurity",
    "information security", "infosec", "network security",
    "devsecops", "iam", "dlp", "endpoint security",
    "virustotal", "hybrid analysis", "sandbox",
    "soar", "automation", "playbook", "runbook",
]


def extract_resume_keywords(user_id):
    """Extract skill/tool keywords from a user's resume PDF. Cached per user."""
    if user_id in _RESUME_KEYWORDS_CACHE:
        return _RESUME_KEYWORDS_CACHE[user_id]

    user = USERS.get(user_id, {})
    resume_path = user.get("resume_path", "")
    keywords = set()

    # Add profile skills
    skills_str = user.get("profile", {}).get("skills", "")
    if skills_str:
        for s in re.split(r'[,;|]', skills_str.lower()):
            s = s.strip()
            if len(s) > 1:
                keywords.add(s)

    # Extract from resume PDF
    if resume_path and os.path.exists(resume_path):
        try:
            import PyPDF2
            with open(resume_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages:
                    text += (page.extract_text() or "")
            text_lower = text.lower()

            # Match against known keywords
            for kw in MATCH_KEYWORDS:
                if kw in text_lower:
                    keywords.add(kw)

            # Also extract capitalized tool names (e.g., Splunk, QRadar)
            tools = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', text)
            for t in tools:
                tl = t.lower()
                if tl in [mk for mk in MATCH_KEYWORDS if len(mk) > 2]:
                    keywords.add(tl)

        except Exception as e:
            log.warning(f"[Match] Failed to extract resume keywords for {user_id}: {e}")

    _RESUME_KEYWORDS_CACHE[user_id] = keywords
    return keywords


def compute_match_score(user_id, job):
    """Compute how well a job matches a user's resume/skills. Returns (score, matched_keywords)."""
    user_keywords = extract_resume_keywords(user_id)
    if not user_keywords:
        return 0, []

    # Build job text
    job_text = " ".join([
        job.get("title", ""),
        job.get("description", ""),
        job.get("snippet", ""),
        job.get("company", ""),
    ]).lower()

    matched = [kw for kw in user_keywords if kw in job_text]

    if not matched:
        return 0, []

    # Score: percentage of user keywords found in job (capped at 100)
    score = min(int((len(matched) / max(len(user_keywords), 1)) * 100), 100)

    # Boost if job title directly matches key terms
    title_lower = job.get("title", "").lower()
    title_matches = sum(1 for kw in ["soc", "security", "cyber", "analyst", "siem", "incident"] if kw in title_lower)
    score = min(score + title_matches * 5, 100)

    return score, matched


def parse_jd_sections(jd_text):
    """Parse a job description into structured sections."""
    sections = {
        "overview": "",
        "responsibilities": "",
        "requirements": "",
        "nice_to_have": "",
        "benefits": "",
    }

    if not jd_text:
        return sections

    # Define section header patterns
    section_patterns = [
        (r'(?:key\s+)?(?:roles?\s*(?:&|and)?\s*)?responsibilities|what\s+you.ll\s+do|job\s+duties|key\s+duties',
         "responsibilities"),
        (r'requirements?|qualifications?|must\s+have|skills?\s+required|what\s+we.re\s+looking|who\s+you\s+are|what\s+you.ll\s+need',
         "requirements"),
        (r'nice\s+to\s+have|preferred|good\s+to\s+have|bonus|desired',
         "nice_to_have"),
        (r'benefits?|perks?|what\s+we\s+offer|why\s+join|compensation',
         "benefits"),
    ]

    lines = jd_text.split("\n")
    current_section = "overview"
    section_lines = {k: [] for k in sections}

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Check if this line is a section header
        matched_section = None
        for pattern, sec_name in section_patterns:
            if re.search(pattern, line_stripped, re.I) and len(line_stripped) < 80:
                matched_section = sec_name
                break

        if matched_section:
            current_section = matched_section
        else:
            section_lines[current_section].append(line_stripped)

    for key in sections:
        sections[key] = "\n".join(section_lines[key])

    return sections


def _extract_resume_summary(resume_path):
    """Extract key experience lines from a PDF resume for the email body."""
    if not resume_path or not os.path.exists(resume_path):
        return ""
    try:
        import PyPDF2
        with open(resume_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "")

        # Extract career objective / profile summary section
        text_lower = text.lower()
        summary = ""
        for header in ["profile summary", "career objective", "professional summary", "summary", "objective"]:
            idx = text_lower.find(header)
            if idx != -1:
                # Get text after this header until next major section
                after = text[idx:]
                # Find next section header (all-caps words followed by colon or newline)
                lines = after.split("\n")
                result_lines = []
                for i, line in enumerate(lines):
                    if i == 0:
                        continue  # skip the header itself
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Stop at next section header (EDUCATION, EXPERIENCE, SKILLS, etc.)
                    if stripped.upper() == stripped and len(stripped) > 3 and any(
                        kw in stripped.upper() for kw in ["EDUCATION", "EXPERIENCE", "SKILL", "CERTIF", "PROJECT", "WORK"]):
                        break
                    result_lines.append(stripped)
                    if len(result_lines) >= 4:
                        break
                if result_lines:
                    summary = " ".join(result_lines)
                    break

        # Extract experience section highlights
        experience = ""
        for header in ["experience", "work experience", "professional experience"]:
            idx = text_lower.find(header)
            if idx != -1:
                after = text[idx:]
                lines = after.split("\n")
                exp_lines = []
                for i, line in enumerate(lines):
                    if i == 0:
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.upper() == stripped and len(stripped) > 3 and any(
                        kw in stripped.upper() for kw in ["EDUCATION", "SKILL", "CERTIF", "PROJECT"]):
                        break
                    # Take bullet points (lines starting with bullet chars or verbs)
                    if any(stripped.startswith(c) for c in ["•", "–", "-", "\uf0b7"]) or (len(stripped) > 20 and i > 1):
                        clean = stripped.lstrip("•–-\uf0b7 ")
                        if len(clean) > 20:
                            exp_lines.append(clean)
                    if len(exp_lines) >= 3:
                        break
                if exp_lines:
                    experience = " ".join(exp_lines)
                    break

        return (summary + " " + experience).strip()
    except Exception as e:
        log.warning(f"[Resume] Failed to extract text: {e}")
        return ""


def _find_user_resume(user_id):
    """Auto-detect a user's resume from the resumes/ folder."""
    resumes_dir = os.path.join(SCRIPT_DIR, "resumes")
    if not os.path.isdir(resumes_dir):
        return ""
    pattern = os.path.join(resumes_dir, f"{user_id}_*")
    matches = glob.glob(pattern)
    if matches:
        matches.sort(key=os.path.getmtime, reverse=True)
        return matches[0]
    return ""


def send_email_brevo(to_email, job, user=None):
    """Send application email from the user's email. BCC the user a copy."""
    api_key = CONFIG.get("brevo", {}).get("api_key", "")
    if not api_key or api_key == "YOUR_BREVO_API_KEY":
        return False

    verified_sender = CONFIG.get("applicant", {}).get("email", "abhibabariya007@gmail.com")
    if user:
        sender_name = user["name"]
        user_email = user["email"]
        profile = user.get("profile", {})
        # Always auto-detect resume from resumes/ folder for the user
        user_id = None
        for uid, u in USERS.items():
            if u is user:
                user_id = uid
                break
        resume_path = ""
        if user_id:
            resume_path = _find_user_resume(user_id)
        # Fallback to stored path only if auto-detect found nothing
        if not resume_path or not os.path.exists(resume_path):
            resume_path = user.get("resume_path", "")
    else:
        sender_name = CONFIG.get("applicant", {}).get("name", "Abhishek Babariya")
        user_email = verified_sender
        resume_path = ""
        profile = {}

    # Attachment - only attach if resume file actually exists
    attachment = None
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            attachment = [{"content": base64.b64encode(f.read()).decode("utf-8"),
                           "name": os.path.basename(resume_path)}]

    job_title = job.get("title", "Cyber Security Analyst")
    company = job.get("company", "your organization")
    subject = f"Application for {job_title} - {sender_name}"

    # Build personalized body from resume
    resume_summary = _extract_resume_summary(resume_path)
    if resume_summary:
        experience_para = f"<p>{resume_summary}</p>"
    else:
        # Fallback: use profile fields if available
        bio = profile.get("bio", "")
        skills = profile.get("skills", "")
        exp_years = profile.get("experience", "")
        if bio:
            experience_para = f"<p>{bio}</p>"
        elif skills or exp_years:
            parts = []
            if exp_years:
                parts.append(f"I have {exp_years} year(s) of experience in cybersecurity")
            if skills:
                parts.append(f"with skills in {skills}")
            experience_para = f"<p>{'. '.join(parts)}.</p>"
        else:
            experience_para = "<p>I am passionate about cybersecurity and eager to contribute to your security operations team. My resume is attached with detailed experience and qualifications.</p>"

    # Contact info
    phone = user.get("phone", "") if user else ""
    linkedin = profile.get("linkedin", "")
    contact_parts = [f'Email: <a href="mailto:{user_email}">{user_email}</a>']
    if phone:
        contact_parts.append(f"Phone: {phone}")
    if linkedin:
        contact_parts.append(f'LinkedIn: <a href="{linkedin}">{linkedin}</a>')

    html_body = f"""<div style="font-family: Arial, sans-serif; max-width: 650px; color: #333;">
<p>Dear Hiring Manager,</p>
<p>I am writing to express my interest in the <strong>{job_title}</strong> position at <strong>{company}</strong>.</p>
{experience_para}
<p>My resume is attached for your consideration. I would welcome the opportunity to discuss how I can contribute to your team.</p>
<p>Kind regards,<br><strong>{sender_name}</strong><br>
{'<br>'.join(contact_parts)}</p>
</div>"""

    # Try sending FROM user's email first, fallback to verified sender
    payload = {
        "sender": {"name": sender_name, "email": user_email},
        "to": [{"email": to_email}],
        "replyTo": {"name": sender_name, "email": user_email},
        "bcc": [{"email": user_email}],
        "subject": subject,
        "htmlContent": html_body,
    }
    if attachment:
        payload["attachment"] = attachment

    headers = {"api-key": api_key, "Content-Type": "application/json"}

    try:
        resp = requests.post("https://api.brevo.com/v3/smtp/email",
                             headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            log.info(f"[Email] Sent to {to_email} for '{job_title}' FROM {user_email} (by {sender_name})")
            return True

        # If Brevo rejects user's email as sender (unverified), fallback to verified sender
        if resp.status_code == 400 and "sender" in resp.text.lower():
            log.info(f"[Email] User email {user_email} not verified on Brevo, using verified sender fallback")
            payload["sender"]["email"] = verified_sender
            resp2 = requests.post("https://api.brevo.com/v3/smtp/email",
                                  headers=headers, json=payload, timeout=30)
            if resp2.status_code in (200, 201):
                log.info(f"[Email] Sent to {to_email} for '{job_title}' via verified sender (reply-to: {user_email})")
                return True
            else:
                log.warning(f"[Email] Fallback also failed {to_email}: {resp2.status_code} {resp2.text[:200]}")
        else:
            log.warning(f"[Email] Failed {to_email}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"[Email] Error: {e}")
    return False


# ─── LinkedIn Feed & Profile Scraper ─────────────────────────────────────────

def linkedin_login(driver):
    """Login to LinkedIn. Returns True on success."""
    li_config = CONFIG.get("linkedin", {})
    if not li_config.get("username") or not li_config.get("password"):
        return False
    try:
        driver.get("https://www.linkedin.com/login")
        time.sleep(3)
        email_field = driver.find_element(By.ID, "username")
        pass_field = driver.find_element(By.ID, "password")
        email_field.send_keys(li_config["username"])
        pass_field.send_keys(li_config["password"])
        pass_field.submit()
        time.sleep(6)
        # Check if login worked
        current = driver.current_url
        if "feed" in current or "mynetwork" in current:
            log.info("[LinkedIn Feed] Login successful")
            return True
        if "checkpoint" in current or "challenge" in current:
            log.warning("[LinkedIn Feed] Login blocked by verification checkpoint - using Google fallback")
            return False
        log.warning(f"[LinkedIn Feed] Login status unclear, URL: {current}")
        return False
    except Exception as e:
        log.warning(f"[LinkedIn Feed] Login failed: {e}")
        return False


def scroll_and_collect(driver, scroll_count=5):
    """Scroll page and collect all visible post texts."""
    posts_data = []
    seen_texts = set()

    for i in range(scroll_count):
        # Get page source after each scroll
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # LinkedIn post containers - try multiple selectors
        containers = []
        for selector in [
            {"class_": re.compile(r"feed-shared-update-v2|occludable-update")},
            {"class_": re.compile(r"feed-shared-update|update-components")},
            {"class_": re.compile(r"profile-creator-shared-feed-update")},
        ]:
            found = soup.find_all(["div", "article"], **selector)
            if found:
                containers = found
                break

        # Fallback: find any div with substantial text content
        if not containers:
            containers = soup.find_all("div", class_=re.compile(r"feed-shared|shared-update|artdeco-card"))

        for container in containers:
            # Get the text content of the post
            text_divs = container.find_all(["div", "span"], class_=re.compile(
                r"feed-shared-text|feed-shared-inline-show-more-text|break-words|update-components-text"
            ))
            if not text_divs:
                text_divs = container.find_all(["span", "div"], class_=re.compile(r"visually-hidden|t-black--light|t-normal"))

            post_text = ""
            for td in text_divs:
                t = td.get_text(" ", strip=True)
                if len(t) > 30:
                    post_text = t
                    break

            if not post_text:
                post_text = container.get_text(" ", strip=True)

            # Deduplicate by first 100 chars
            key = post_text[:100].strip()
            if key in seen_texts or len(post_text) < 30:
                continue
            seen_texts.add(key)

            # Get poster info
            poster = ""
            poster_el = container.find(["span", "a"], class_=re.compile(r"update-components-actor__name|feed-shared-actor__name"))
            if poster_el:
                poster = poster_el.get_text(strip=True)

            # Get all links in the post
            links = []
            for a in container.find_all("a", href=True):
                href = a["href"]
                if any(x in href for x in ["linkedin.com/jobs", "naukri.com", "indeed.com",
                                            "glassdoor.com", "forms.gle", "forms.google",
                                            "bit.ly", "lnkd.in", "apply"]):
                    links.append(href)

            # Get poster's LinkedIn profile link
            poster_link = ""
            for a in container.find_all("a", href=re.compile(r"linkedin\.com/in/")):
                poster_link = a["href"].split("?")[0]
                break

            # Extract emails from the post text
            emails_in_post = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', post_text)
            blacklist = ['example.com', 'linkedin.com', 'google.com', 'facebook.com', 'schema.org']
            emails_in_post = [e.lower() for e in emails_in_post if not any(b in e.lower() for b in blacklist)]

            posts_data.append({
                "text": post_text,
                "poster": poster,
                "poster_link": poster_link,
                "links": links,
                "emails": emails_in_post,
            })

        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

    log.info(f"[LinkedIn Feed] Collected {len(posts_data)} posts after {scroll_count} scrolls")
    return posts_data


def parse_hiring_posts(posts_data, source_label):
    """Filter and parse posts that are about hiring for SOC/Security roles."""
    jobs = []

    hiring_keywords = [
        "hiring", "opening", "vacancy", "looking for", "job alert",
        "apply now", "walk-in", "fresher", "immediate joiner",
        "urgent requirement", "job opportunity", "we are hiring",
        "urgent hiring", "actively hiring", "join our team",
        "open position", "job role", "requirement for", "wanted",
        "recruitment", "career opportunity", "dm me", "dm for",
        "interested candidates", "share your cv", "send your resume",
        "drop your resume", "forward your cv", "mail your cv",
        "walk in interview", "refer", "referral",
    ]

    security_keywords = [
        "soc", "security", "cyber", "analyst", "monitoring", "siem",
        "blue team", "incident response", "threat", "infosec",
        "information security", "security operations", "l1", "tier 1",
        "splunk", "qradar", "sentinel", "crowdstrike", "edr",
        "vulnerability", "penetration", "ethical hacking",
        "network security", "endpoint", "malware", "firewall",
    ]

    for post in posts_data:
        text = post["text"]
        text_lower = text.lower()

        # Must contain a hiring keyword AND a security keyword
        has_hiring = any(kw in text_lower for kw in hiring_keywords)
        has_security = any(kw in text_lower for kw in security_keywords)

        if not (has_hiring and has_security):
            continue

        # Extract job title
        title = extract_job_title(text)

        # Extract company
        company = extract_company(text, post.get("poster", ""))

        # Extract location
        location = "India"
        for loc in CONFIG["locations"]:
            if loc.lower() in text_lower:
                location = loc
                break
        # Also check for common cities
        for city in ["bangalore", "bengaluru", "hyderabad", "delhi", "noida", "gurgaon",
                      "chennai", "kolkata", "mumbai", "pune", "ahmedabad", "surat", "remote"]:
            if city in text_lower:
                location = city.title()
                break

        # Extract experience
        exp_text = ""
        exp_match = re.search(r'(\d+)\s*[-\u2013to]+\s*(\d+)\s*(?:years?|yrs?)', text_lower)
        if exp_match:
            exp_text = f"{exp_match.group(1)}-{exp_match.group(2)} years"
        elif re.search(r'fresher|0\s*(?:years?|yrs?)|entry\s*level', text_lower):
            exp_text = "Fresher / 0 years"

        # Best link (apply link or poster profile)
        link = ""
        if post["links"]:
            link = post["links"][0]
        elif post.get("poster_link"):
            link = post["poster_link"]

        # Prepare HR emails from the post
        hr_emails = post.get("emails", [])

        # Create snippet
        snippet = text[:250].replace("\n", " ").strip()
        if len(text) > 250:
            snippet += "..."

        jobs.append({
            "title": title,
            "company": company,
            "url": link,
            "platform": source_label,
            "location": location,
            "experience": exp_text,
            "found_at": datetime.now().isoformat(),
            "source_profile": post.get("poster_link", ""),
            "poster": post.get("poster", ""),
            "snippet": snippet,
            "hr_emails_from_post": hr_emails,
        })

    return jobs


def extract_job_title(text):
    """Extract the most relevant job title from post text."""
    lines = text.split("\n")

    # Priority patterns for job titles
    title_patterns = [
        r'(?:role|position|job\s*title|opening|hiring\s*for|looking\s*for)\s*[:\-\u2013]\s*(.+?)(?:\n|$|\.)',
        r'((?:SOC|Security|Cyber|SIEM|Blue\s*Team|Threat|Information\s*Security|Network\s*Security)\s*(?:Analyst|Engineer|Specialist|Associate|Consultant|Administrator|Operator)(?:\s*L[123])?(?:\s*[-/]\s*\w+)?)',
        r'((?:Junior|Senior|Jr|Sr)?\s*(?:SOC|Security|Cyber)\s*\w+)',
    ]

    for pattern in title_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            if 10 < len(title) < 100:
                return title

    # Fallback: find a line with security keywords
    for line in lines:
        line = line.strip()
        if 10 < len(line) < 100:
            line_lower = line.lower()
            if any(w in line_lower for w in ["soc", "analyst", "security", "cyber", "siem", "blue team"]):
                if any(w in line_lower for w in ["hiring", "opening", "role", "position", "wanted", "looking"]):
                    return line[:100]

    # Last resort
    for line in lines:
        line = line.strip()
        if 10 < len(line) < 80 and any(w in line.lower() for w in ["soc", "analyst", "security", "cyber"]):
            return line[:100]

    return "SOC / Cyber Security Role (from LinkedIn Post)"


def extract_company(text, poster_name):
    """Extract company name from post text."""
    company_patterns = [
        r'(?:company|organization|firm)\s*[:\-]\s*([A-Z][A-Za-z\s&.]+?)(?:\n|$|,)',
        r'(?:at|@)\s+([A-Z][A-Za-z\s&.]{2,30}?)(?:\s*[-|,\n.])',
        r'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(?:is\s+)?(?:hiring|looking|recruiting)',
        r'(?:join|work\s+with|work\s+at)\s+([A-Z][A-Za-z\s&.]{2,30}?)(?:\s*[-|,\n.])',
    ]

    for pattern in company_patterns:
        m = re.search(pattern, text)
        if m:
            company = m.group(1).strip()
            # Filter out common false positives
            if company.lower() not in ["we", "our", "the", "team", "i", "my", "this", "a", "an"]:
                return company[:50]

    # Try poster name as fallback context
    if poster_name and len(poster_name) > 2:
        return f"via {poster_name[:40]}"

    return "Unknown"


def scrape_linkedin_profiles():
    """Scrape job posts from LinkedIn via Google search + profile pages."""
    profiles = CONFIG.get("linkedin_profiles", [])
    log.info(f"[LinkedIn Feed] Starting feed + profile scan...")
    jobs = []

    driver = create_driver()
    if not driver:
        log.error("[LinkedIn Feed] Could not create driver")
        return []

    try:
        # === APPROACH 1: Google search for LinkedIn hiring posts ===
        # This works WITHOUT login - searches Google for recent LinkedIn posts about hiring
        google_queries = [
            'site:linkedin.com/posts "hiring" "SOC analyst" India',
            'site:linkedin.com/posts "hiring" "security analyst" India',
            'site:linkedin.com/posts "hiring" "cyber security" "L1" OR "fresher"',
            'site:linkedin.com/posts "urgent hiring" "SOC" OR "security analyst"',
            'site:linkedin.com/feed/update "SOC" "hiring" India',
        ]

        for query in google_queries:
            try:
                encoded = quote_plus(query)
                driver.get(f"https://www.google.com/search?q={encoded}&tbs=qdr:w")  # Last week
                time.sleep(3)

                soup = BeautifulSoup(driver.page_source, "html.parser")
                results = soup.find_all("div", class_=re.compile(r"tF2Cxc|g|MjjYud"))
                if not results:
                    results = soup.find_all("div", class_="g")

                for result in results:
                    link_tag = result.find("a", href=True)
                    title_tag = result.find("h3")
                    snippet_tag = result.find(["span", "div"], class_=re.compile(r"aCOpRe|VwiC3b|st"))

                    if not link_tag:
                        continue

                    raw_href = link_tag["href"]
                    href = clean_google_url(raw_href)
                    if not href or "linkedin.com" not in href:
                        continue

                    title_text = title_tag.get_text(strip=True) if title_tag else ""
                    snippet_text = snippet_tag.get_text(strip=True) if snippet_tag else ""
                    full_text = f"{title_text} {snippet_text}".lower()

                    # Must be hiring-related and security-related
                    if not any(h in full_text for h in ["hiring", "opening", "vacancy", "looking for", "job"]):
                        continue
                    if not any(s in full_text for s in ["soc", "security", "cyber", "analyst", "siem"]):
                        continue

                    # Extract info
                    title = extract_job_title(f"{title_text}\n{snippet_text}")
                    company = extract_company(f"{title_text}\n{snippet_text}", "")

                    # Extract emails from snippet
                    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', snippet_text)

                    location = "India"
                    for loc in CONFIG["locations"]:
                        if loc.lower() in full_text:
                            location = loc
                            break

                    jobs.append({
                        "title": title,
                        "company": company,
                        "url": href,
                        "platform": "LinkedIn (Feed)",
                        "location": location,
                        "found_at": datetime.now().isoformat(),
                        "snippet": snippet_text[:250] if snippet_text else title_text[:250],
                        "hr_emails_from_post": emails,
                        "poster": "",
                    })

                log.info(f"[LinkedIn Feed] Google: '{query[:40]}...' -> {len(results)} results checked")
                time.sleep(2)
            except Exception as e:
                log.error(f"[LinkedIn Feed] Google search error: {e}")

        # === APPROACH 2: Try to login and scrape feed ===
        logged_in = linkedin_login(driver)

        if logged_in:
            # Scrape LinkedIn Feed
            log.info("[LinkedIn Feed] Scanning your feed for hiring posts...")
            try:
                driver.get("https://www.linkedin.com/feed/")
                time.sleep(4)
                feed_posts = scroll_and_collect(driver, scroll_count=4)
                feed_jobs = parse_hiring_posts(feed_posts, "LinkedIn (Feed)")
                jobs.extend(feed_jobs)
                log.info(f"[LinkedIn Feed] Found {len(feed_jobs)} jobs from feed")
            except Exception as e:
                log.error(f"[LinkedIn Feed] Feed scan error: {e}")

            # Search LinkedIn for hiring posts
            search_terms = ["SOC analyst hiring", "cyber security analyst hiring India"]
            for term in search_terms:
                try:
                    encoded = quote_plus(term)
                    driver.get(f"https://www.linkedin.com/search/results/content/?keywords={encoded}&sortBy=%22date_posted%22")
                    time.sleep(4)
                    search_posts = scroll_and_collect(driver, scroll_count=2)
                    search_jobs = parse_hiring_posts(search_posts, "LinkedIn (Feed)")
                    jobs.extend(search_jobs)
                    log.info(f"[LinkedIn Feed] '{term}' -> {len(search_jobs)} jobs")
                except Exception as e:
                    log.error(f"[LinkedIn Feed] Search error: {e}")

        # === APPROACH 3: Scrape specific profiles (Niranth D etc.) ===
        for profile_url in profiles:
            clean = profile_url.rstrip("/").split("?")[0]
            log.info(f"[LinkedIn Profile] Scanning: {clean}")

            # Also Google search for this person's posts
            try:
                person_name = clean.split("/in/")[-1].replace("-", " ").rstrip("/")
                google_q = quote_plus(f'site:linkedin.com "{person_name}" "hiring" OR "opening" "security" OR "SOC" OR "analyst"')
                driver.get(f"https://www.google.com/search?q={google_q}&tbs=qdr:w")
                time.sleep(3)
                soup = BeautifulSoup(driver.page_source, "html.parser")
                g_results = soup.find_all("div", class_="g")
                for result in g_results:
                    link_tag = result.find("a", href=True)
                    title_tag = result.find("h3")
                    snippet_tag = result.find(["span", "div"], class_=re.compile(r"aCOpRe|VwiC3b|st"))
                    if not link_tag:
                        continue
                    raw_href = link_tag["href"]
                    href = clean_google_url(raw_href)
                    if not href:
                        continue
                    title_text = title_tag.get_text(strip=True) if title_tag else ""
                    snippet_text = snippet_tag.get_text(strip=True) if snippet_tag else ""
                    full_text = f"{title_text} {snippet_text}".lower()
                    if any(s in full_text for s in ["soc", "security", "cyber", "analyst", "hiring", "opening"]):
                        title = extract_job_title(f"{title_text}\n{snippet_text}")
                        company = extract_company(f"{title_text}\n{snippet_text}", person_name)
                        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', snippet_text)
                        jobs.append({
                            "title": title,
                            "company": company,
                            "url": href,
                            "platform": "LinkedIn (Profile)",
                            "location": "India",
                            "found_at": datetime.now().isoformat(),
                            "poster": person_name.title(),
                            "source_profile": clean,
                            "snippet": snippet_text[:250] if snippet_text else title_text[:250],
                            "hr_emails_from_post": emails,
                        })
                log.info(f"[LinkedIn Profile] Google found {len(g_results)} results for {person_name}")
            except Exception as e:
                log.error(f"[LinkedIn Profile] Google search error: {e}")

            # Try direct profile activity page (works if logged in)
            if logged_in:
                for activity_path in ["/recent-activity/all/"]:
                    try:
                        driver.get(clean + activity_path)
                        time.sleep(4)
                        profile_posts = scroll_and_collect(driver, scroll_count=5)
                        profile_jobs = parse_hiring_posts(profile_posts, "LinkedIn (Profile)")
                        jobs.extend(profile_jobs)
                        log.info(f"[LinkedIn Profile] {activity_path} -> {len(profile_jobs)} jobs")
                    except Exception as e:
                        log.error(f"[LinkedIn Profile] Error: {e}")

    except Exception as e:
        log.error(f"[LinkedIn Feed] Fatal error: {e}")
    finally:
        try:
            force_quit_driver(driver)
        except:
            pass

    # Deduplicate by snippet
    seen = set()
    unique_jobs = []
    for j in jobs:
        key = j.get("snippet", "")[:80]
        if key and key not in seen:
            seen.add(key)
            unique_jobs.append(j)

    log.info(f"[LinkedIn Feed] Total unique jobs from feed/profiles: {len(unique_jobs)}")
    return unique_jobs


# ─── Scan All Platforms ──────────────────────────────────────────────────────

def run_full_scan():
    """Run scan — called either in-process or from subprocess.
    API scrapers run in PARALLEL, Selenium scrapers run sequentially after."""
    global ALL_JOBS, STATS
    log.info("=== Starting full scan ===")
    kill_zombie_browsers()

    existing_hashes = {job_hash(j["title"], j["company"], j["platform"]) for j in ALL_JOBS}
    for j in ALL_JOBS:
        j["is_new"] = False
    old_jobs = list(ALL_JOBS)
    new_jobs = []
    _lock = threading.Lock()

    def _add_results(name, found):
        nonlocal new_jobs
        added = 0
        with _lock:
            for job in found:
                jh = job_hash(job["title"], job["company"], job["platform"])
                if jh not in existing_hashes:
                    job["is_new"] = True
                    job["email_sent"] = False
                    new_jobs.append(job)
                    existing_hashes.add(jh)
                    added += 1
            if added > 0:
                ALL_JOBS[:] = new_jobs + old_jobs
                STATS["new_today"] = len(new_jobs)
                STATS["last_scan"] = datetime.now().isoformat()
                save_jobs_db()
        log.info(f"[{name}] +{added} new (total: {len(new_jobs)} new, {len(old_jobs) + len(new_jobs)} overall)")

    # Pre-download chromedriver ONCE before any scrapers run (prevents race condition)
    pre_download_chromedriver()

    # ── PHASE 1: API scrapers in parallel (NO Selenium — pure HTTP only) ──
    api_scrapers = [
        ("LinkedIn", scrape_linkedin, 480),
        ("TimesJobs", scrape_timesjobs, 180),
    ]

    results = {}

    def _run_api_scraper(name, fn, timeout):
        try:
            container = [None]
            def _inner():
                try:
                    container[0] = fn()
                except Exception as ex:
                    log.error(f"[{name}] Thread error: {ex}")
                    container[0] = []
            t = threading.Thread(target=_inner, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                log.warning(f"[{name}] TIMEOUT after {timeout}s — skipping")
                results[name] = []
            else:
                results[name] = container[0] or []
            _add_results(name, results[name])
        except Exception as e:
            log.error(f"[{name}] Error: {e}")

    log.info("── Phase 1: API scrapers (parallel) ──")
    api_threads = []
    for name, fn, timeout in api_scrapers:
        t = threading.Thread(target=_run_api_scraper, args=(name, fn, timeout), daemon=True)
        t.start()
        api_threads.append(t)

    # Wait for all API scrapers (max 8 min total)
    for t in api_threads:
        t.join(timeout=500)

    log.info(f"── Phase 1 done: {len(new_jobs)} new jobs so far ──")

    # ── PHASE 2: Selenium scrapers sequentially (one at a time, no race condition) ──
    selenium_scrapers = [
        ("Naukri", scrape_naukri, 480),
        ("Indeed", scrape_indeed, 300),
        ("Glassdoor", scrape_glassdoor, 180),
        ("Google Jobs", scrape_google_jobs, 180),
        ("SimplyHired", scrape_simplyhired, 180),
        ("LinkedIn Profiles", scrape_linkedin_profiles, 180),
        ("Foundit", scrape_foundit, 240),
        ("Shine", scrape_shine, 180),
    ]

    log.info("── Phase 2: Selenium scrapers (sequential) ──")
    for name, fn, timeout in selenium_scrapers:
        try:
            container = [None]
            def _inner(f=fn, c=container):
                try:
                    c[0] = f()
                except Exception as ex:
                    log.error(f"[{name}] Thread error: {ex}")
                    c[0] = []
            t = threading.Thread(target=_inner, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                log.warning(f"[{name}] TIMEOUT after {timeout}s — skipping. Killing browsers.")
                kill_zombie_browsers()
                found = []
            else:
                found = container[0] or []
            _add_results(name, found)
        except Exception as e:
            log.error(f"[{name}] Error: {e}")

    # Final save
    ALL_JOBS[:] = new_jobs + old_jobs
    STATS["new_today"] = len(new_jobs)
    STATS["last_scan"] = datetime.now().isoformat()
    save_jobs_db()
    log.info(f"=== Scan complete: {len(new_jobs)} new jobs, {len(ALL_JOBS)} total ===")
    return len(new_jobs)


def run_scan_subprocess():
    """Launch scan as a separate Python process so Flask stays responsive."""
    scan_script = os.path.join(SCRIPT_DIR, "run_scan.py")
    # Create a mini scan script if it doesn't exist
    if not os.path.exists(scan_script):
        with open(scan_script, "w") as f:
            f.write("""import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard import *
load_jobs_db()
run_full_scan()
""")
    try:
        scan_log_path = os.path.join(SCRIPT_DIR, "scan_output.log")
        scan_log_file = open(scan_log_path, "w")
        proc = subprocess.Popen(
            [sys.executable, scan_script],
            cwd=SCRIPT_DIR,
            stdout=scan_log_file,
            stderr=scan_log_file,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        log.info(f"[Scan] Started subprocess PID {proc.pid}")
        # Poll subprocess and reload jobs every 30s for live updates
        while proc.poll() is None:
            time.sleep(30)
            try:
                load_jobs_db()
                log.info(f"[Scan] Live reload — {len(ALL_JOBS)} jobs on dashboard")
            except Exception:
                pass
        scan_log_file.close()
        log.info(f"[Scan] Subprocess finished with code {proc.returncode}")
        # Final reload
        load_jobs_db()
        SCAN_STATUS["last_new"] = STATS.get("new_today", 0)
    except Exception as e:
        log.error(f"[Scan] Subprocess error: {e}")
        try:
            scan_log_file.close()
        except Exception:
            pass
    finally:
        SCAN_STATUS["running"] = False


# ─── Flask Routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def api_jobs():
    today = datetime.now().date().isoformat()
    new_today = sum(1 for j in ALL_JOBS if j.get("found_at", "").startswith(today))
    emails = STATS.get("emails_sent", 0)
    user_id = request.args.get("user_id", "")

    # Add applied_by info and match scores to each job
    jobs_with_info = []
    for job in ALL_JOBS:
        j = dict(job)
        jid = job_hash(j["title"], j["company"], j["platform"])
        applied_by = []
        for uid, user in USERS.items():
            if jid in user.get("applied_jobs", []):
                applied_by.append(uid)
        j["applied_by"] = applied_by

        # Compute match score if user is logged in
        if user_id and user_id in USERS:
            score, matched = compute_match_score(user_id, job)
            j["match_score"] = score
            j["match_keywords"] = matched[:8]  # Top 8 matched keywords

        # Add category and freshness
        j["category"] = categorize_job(j.get("title", ""))
        j["freshness"] = compute_freshness(j.get("found_at", ""))
        jobs_with_info.append(j)

    return jsonify({
        "jobs": jobs_with_info,
        "total": len(ALL_JOBS),
        "new_today": new_today,
        "emails_sent": emails,
        "applied": sum(1 for j in jobs_with_info if j.get("applied_by")),
    })


@app.route("/api/stats")
def api_stats():
    """Detailed stats breakdown for stat card popups."""
    today = datetime.now().date().isoformat()

    # Platform breakdown
    platform_counts = {}
    for j in ALL_JOBS:
        p = j.get("platform", "Unknown")
        platform_counts[p] = platform_counts.get(p, 0) + 1

    # New today per platform
    new_today_breakdown = {}
    for j in ALL_JOBS:
        if j.get("found_at", "").startswith(today):
            p = j.get("platform", "Unknown")
            new_today_breakdown[p] = new_today_breakdown.get(p, 0) + 1

    # Location breakdown
    location_counts = {}
    for j in ALL_JOBS:
        loc = j.get("location", "Unknown")
        location_counts[loc] = location_counts.get(loc, 0) + 1

    # Company breakdown (top 10)
    company_counts = {}
    for j in ALL_JOBS:
        c = j.get("company", "Unknown")
        if c != "Unknown":
            company_counts[c] = company_counts.get(c, 0) + 1
    top_companies = sorted(company_counts.items(), key=lambda x: -x[1])[:10]

    # Emails breakdown
    emailed = load_emailed()
    emails_by_user = {}
    for e in emailed:
        parts = e.split("|")
        if len(parts) >= 3:
            uid = parts[2]
            emails_by_user[uid] = emails_by_user.get(uid, 0) + 1

    # User details
    user_details = []
    for uid, u in USERS.items():
        user_details.append({
            "name": u["name"],
            "email": u["email"],
            "applications": len(u.get("applied_jobs", [])),
            "has_resume": bool(u.get("resume_path") and os.path.exists(u.get("resume_path", ""))),
            "emails_sent": emails_by_user.get(uid, 0),
        })

    # Applied jobs details
    applied_jobs = []
    for j in ALL_JOBS:
        jid = job_hash(j["title"], j["company"], j["platform"])
        for uid, user in USERS.items():
            if jid in user.get("applied_jobs", []):
                applied_jobs.append({
                    "title": j["title"],
                    "company": j["company"],
                    "platform": j["platform"],
                    "applied_by": user["name"],
                })

    return jsonify({
        "total_jobs": len(ALL_JOBS),
        "new_today": sum(1 for j in ALL_JOBS if j.get("found_at", "").startswith(today)),
        "platform_breakdown": platform_counts,
        "new_today_breakdown": new_today_breakdown,
        "location_breakdown": location_counts,
        "top_companies": [{"name": c, "count": n} for c, n in top_companies],
        "emails_sent": STATS.get("emails_sent", 0),
        "emails_by_user": emails_by_user,
        "users": user_details,
        "applied_jobs": applied_jobs,
        "last_scan": ALL_JOBS[0]["found_at"] if ALL_JOBS else "Never",
    })


SCAN_STATUS = {"running": False, "last_new": 0}

@app.route("/api/scan", methods=["POST"])
def api_scan():
    if SCAN_STATUS["running"]:
        today = datetime.now().date().isoformat()
        new_today = sum(1 for j in ALL_JOBS if j.get("found_at", "").startswith(today))
        return jsonify({
            "jobs": ALL_JOBS, "total": len(ALL_JOBS), "new_count": 0,
            "new_today": new_today, "emails_sent": STATS.get("emails_sent", 0),
            "message": "Scan already in progress, showing cached results"
        })

    SCAN_STATUS["running"] = True
    threading.Thread(target=run_scan_subprocess, daemon=True).start()

    # Return current data immediately — frontend will poll for updates
    today = datetime.now().date().isoformat()
    new_today = sum(1 for j in ALL_JOBS if j.get("found_at", "").startswith(today))
    return jsonify({
        "jobs": ALL_JOBS, "total": len(ALL_JOBS), "new_count": 0,
        "new_today": new_today, "emails_sent": STATS.get("emails_sent", 0),
        "scanning": True, "message": "Scan started in background"
    })


@app.route("/api/scan_status")
def api_scan_status():
    """Check if a background scan is running or completed."""
    today = datetime.now().date().isoformat()
    new_today = sum(1 for j in ALL_JOBS if j.get("found_at", "").startswith(today))
    return jsonify({
        "running": SCAN_STATUS["running"],
        "total": len(ALL_JOBS),
        "new_today": new_today,
        "new_count": SCAN_STATUS["last_new"],
        "emails_sent": STATS.get("emails_sent", 0),
    })


def _apply_background(user_id, job, job_id):
    """Background worker: fetch emails, send applications, update state."""
    global STATS
    try:
        user = USERS[user_id]

        # Find HR emails - check post emails first, then page, then guess
        emails = job.get("hr_emails_from_post", [])
        emails += extract_emails_from_page(job.get("url", ""))
        guessed = guess_hr_emails(job.get("company", ""))
        all_emails = list(set(emails + guessed))

        if not all_emails:
            log.warning(f"[ApplyBG] No HR emails found for {job.get('company', '?')}")
            return

        emailed = load_emailed()
        sent_count = 0

        for email_addr in all_emails[:5]:
            email_key = f"{email_addr}|{job['company'].lower()}|{user_id}"
            if email_key in emailed:
                continue
            ok = send_email_brevo(email_addr, job, user)
            log_application(user_id, job, email_addr, ok)
            if ok:
                emailed.append(email_key)
                sent_count += 1
                time.sleep(1)

        save_emailed(emailed)

        if sent_count > 0:
            if "applied_jobs" not in user:
                user["applied_jobs"] = []
            user["applied_jobs"].append(job_id)
            save_users()
            STATS["emails_sent"] = STATS.get("emails_sent", 0) + sent_count
            save_jobs_db()
            log.info(f"[ApplyBG] Sent {sent_count} emails for {job.get('company', '?')} (user={user_id})")
        else:
            log.info(f"[ApplyBG] All emails already contacted for {job.get('company', '?')} (user={user_id})")
    except Exception as e:
        log.error(f"[ApplyBG] Error: {e}")


@app.route("/api/apply", methods=["POST"])
def api_apply():
    data = request.json
    idx = data.get("job_index")
    user_id = data.get("user_id", "")

    if idx is None or idx >= len(ALL_JOBS):
        return jsonify({"success": False, "message": "Invalid job index"})

    if user_id not in USERS:
        return jsonify({"success": False, "message": "Please register first before applying."})

    job = ALL_JOBS[idx]
    job_id = job_hash(job["title"], job["company"], job["platform"])

    # Check if THIS user already applied to THIS job
    if job_id in USERS[user_id].get("applied_jobs", []):
        return jsonify({"success": True, "message": "You already applied to this job!", "emails_sent": 0})

    # Mark as applied immediately to prevent double-clicks
    if "applied_jobs" not in USERS[user_id]:
        USERS[user_id]["applied_jobs"] = []
    USERS[user_id]["applied_jobs"].append(job_id)
    save_users()

    # Send emails in background thread — respond instantly
    threading.Thread(
        target=_apply_background,
        args=(user_id, job.copy(), job_id),
        daemon=True,
    ).start()

    return jsonify({
        "success": True,
        "message": f"Applying to {job['company']}... Check 'My Apps' for delivery status.",
        "emails_sent": -1,
        "sending": True,
    })


@app.route("/api/my_applications")
def api_my_applications():
    """Get all applications for a specific user with real-time delivery status from Brevo."""
    user_id = request.args.get("user_id", "")
    if not user_id or user_id not in USERS:
        return jsonify({"success": False, "message": "Invalid user_id", "applications": []})

    user = USERS[user_id]
    app_log = load_app_log()

    # Filter logs for this user
    user_apps = [a for a in app_log if a.get("user_id") == user_id]

    # Group by company+job for cleaner display
    grouped = {}
    for a in user_apps:
        key = f"{a['job_title']}|{a['company']}"
        if key not in grouped:
            grouped[key] = {
                "job_title": a["job_title"],
                "company": a["company"],
                "platform": a.get("platform", ""),
                "job_url": a.get("job_url", ""),
                "applied_at": a["sent_at"],
                "emails": [],
            }
        grouped[key]["emails"].append({
            "to": a["sent_to"],
            "sent_at": a["sent_at"],
            "brevo_accepted": a.get("brevo_accepted", False),
            "delivery_status": "pending",  # Will be enriched by /api/email_status
        })

    applications = sorted(grouped.values(), key=lambda x: x["applied_at"], reverse=True)

    return jsonify({
        "success": True,
        "user_name": user.get("name", ""),
        "total_applications": len(applications),
        "total_emails_sent": len(user_apps),
        "applications": applications,
    })


@app.route("/api/email_status")
def api_email_status():
    """Check real-time delivery status of sent emails from Brevo API.
    Query: ?email=hr@company.com or ?days=1 for recent activity."""
    api_key = CONFIG.get("brevo", {}).get("api_key", "")
    if not api_key or api_key == "YOUR_BREVO_API_KEY":
        return jsonify({"success": False, "message": "Brevo API not configured"})

    email = request.args.get("email", "")
    days = int(request.args.get("days", "3"))
    user_id = request.args.get("user_id", "")

    try:
        headers = {"api-key": api_key, "Content-Type": "application/json"}

        # Paginate through all events
        events = []
        offset = 0
        while True:
            params = {"limit": 100, "offset": offset, "days": min(days, 30)}
            if email:
                params["email"] = email
            resp = requests.get("https://api.brevo.com/v3/smtp/statistics/events",
                                headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                if not events:
                    return jsonify({"success": False, "message": f"Brevo API error: {resp.status_code}"})
                break
            batch = resp.json().get("events", [])
            events.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 500:  # Safety cap
                break

        # Build per-email status summary
        email_statuses = {}
        for e in events:
            addr = e.get("email", "")
            evt = e.get("event", "")
            date = e.get("date", "")
            subject = e.get("subject", "")
            reason = e.get("reason", "")

            if addr not in email_statuses:
                email_statuses[addr] = {
                    "email": addr,
                    "subject": subject,
                    "events": [],
                    "final_status": "sent",
                }

            email_statuses[addr]["events"].append({
                "event": evt,
                "date": date,
                "reason": reason[:100] if reason else "",
            })

            # Determine final status (priority: delivered > opened > deferred > bounced)
            current = email_statuses[addr]["final_status"]
            if evt == "opened":
                email_statuses[addr]["final_status"] = "opened"
            elif evt == "delivered" and current not in ("opened",):
                email_statuses[addr]["final_status"] = "delivered"
            elif evt == "softBounces" and current not in ("opened", "delivered"):
                email_statuses[addr]["final_status"] = "soft_bounce"
            elif evt == "hardBounces" and current not in ("opened", "delivered"):
                email_statuses[addr]["final_status"] = "hard_bounce"
            elif evt == "blocked" and current not in ("opened", "delivered"):
                email_statuses[addr]["final_status"] = "blocked"
            elif evt == "deferred" and current not in ("opened", "delivered", "soft_bounce", "hard_bounce"):
                email_statuses[addr]["final_status"] = "deferred"

        # If user_id provided, filter to only emails sent by that user
        if user_id:
            app_log = load_app_log()
            user_emails = {a["sent_to"] for a in app_log if a.get("user_id") == user_id}
            email_statuses = {k: v for k, v in email_statuses.items() if k in user_emails}

        # Summary counts
        status_counts = {}
        for es in email_statuses.values():
            s = es["final_status"]
            status_counts[s] = status_counts.get(s, 0) + 1

        return jsonify({
            "success": True,
            "total_tracked": len(email_statuses),
            "summary": status_counts,
            "emails": sorted(email_statuses.values(), key=lambda x: x["events"][-1]["date"] if x["events"] else "", reverse=True),
        })
    except Exception as e:
        log.error(f"[EmailStatus] Error: {e}")
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/register", methods=["POST"])
def api_register():
    """Register a new user — direct registration without OTP."""
    data = request.json
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "")

    if not name or not email or not phone:
        return jsonify({"success": False, "message": "Name, email, and phone number are all required."})

    if "@" not in email:
        return jsonify({"success": False, "message": "Invalid email address."})

    pwd_err = validate_password(password)
    if pwd_err:
        return jsonify({"success": False, "message": pwd_err})

    # Validate phone (Indian numbers: 10 digits or +91...)
    phone_clean = phone.replace(" ", "").replace("-", "").replace("+91", "")
    if not phone_clean.isdigit() or len(phone_clean) != 10:
        return jsonify({"success": False, "message": "Enter a valid 10-digit phone number."})

    # Check if email or phone already registered
    for uid, u in USERS.items():
        if u.get("email", "").lower() == email.lower():
            return jsonify({"success": False, "message": "An account with this email already exists. Please login."})
        existing_phone = u.get("phone", "").replace(" ", "").replace("-", "").replace("+91", "")
        if existing_phone and existing_phone == phone_clean:
            return jsonify({"success": False, "message": "An account with this phone number already exists. Please login."})

    # Create user_id from name
    user_id = re.sub(r'[^a-z0-9]', '', name.lower())
    if not user_id:
        user_id = email.split("@")[0]

    # Ensure unique user_id
    base_id = user_id
    counter = 1
    while user_id in USERS:
        user_id = f"{base_id}{counter}"
        counter += 1

    # Register directly — no OTP needed
    USERS[user_id] = {
        "name": name,
        "email": email,
        "phone": phone,
        "password_hash": hash_password(password),
        "resume_path": "",
        "applied_jobs": [],
        "profile": {
            "linkedin": "",
            "location": "",
            "experience": "",
            "skills": "",
            "bio": "",
        },
        "mfa_enabled": False,
        "verified": True,
        "registered_at": datetime.now().isoformat(),
    }
    save_users()
    log.info(f"[User] Registered: {name} ({email}) as {user_id}")

    # Create session — log them in immediately
    token = generate_session_token()
    SESSIONS[token] = {"user_id": user_id, "logged_in_at": datetime.now().isoformat()}

    return jsonify({
        "success": True, "user_id": user_id, "otp_required": False,
        "session_token": token,
        "message": f"Registration successful! Welcome {name}!"
    })


@app.route("/api/verify_otp", methods=["POST"])
def api_verify_otp():
    """Verify OTP for registration or login."""
    data = request.json
    user_id = data.get("user_id", "").strip()
    otp_input = data.get("otp", "").strip()

    if not user_id or not otp_input:
        return jsonify({"success": False, "message": "User ID and OTP are required."})

    otp_data = OTP_STORE.get(user_id)
    if not otp_data:
        return jsonify({"success": False, "message": "No pending verification. Please register again."})

    if datetime.now() > otp_data["expires"]:
        del OTP_STORE[user_id]
        return jsonify({"success": False, "message": "OTP expired. Please register again."})

    if otp_input != otp_data["otp"]:
        return jsonify({"success": False, "message": "Invalid OTP. Please try again."})

    # OTP verified — complete registration or login
    otp_data["verified"] = True
    pending = otp_data.get("pending_data")

    if pending:
        # New registration
        USERS[user_id] = {
            "name": pending["name"],
            "email": pending["email"],
            "phone": pending["phone"],
            "password_hash": pending.get("password_hash", ""),
            "resume_path": "",
            "applied_jobs": USERS.get(user_id, {}).get("applied_jobs", []),
            "profile": {
                "linkedin": "",
                "location": "",
                "experience": "",
                "skills": "",
                "bio": "",
            },
            "mfa_enabled": False,
            "verified": True,
            "registered_at": datetime.now().isoformat(),
        }
        save_users()
        log.info(f"[User] Registered & verified: {pending['name']} ({pending['email']}) as {user_id}")

    # Create session
    token = generate_session_token()
    SESSIONS[token] = {"user_id": user_id, "logged_in_at": datetime.now().isoformat()}

    del OTP_STORE[user_id]
    return jsonify({
        "success": True, "user_id": user_id, "session_token": token,
        "message": f"Verified! Welcome {USERS[user_id]['name']}!"
    })


@app.route("/api/resend_otp", methods=["POST"])
def api_resend_otp():
    """Resend OTP to user's email and phone."""
    data = request.json
    user_id = data.get("user_id", "").strip()

    otp_data = OTP_STORE.get(user_id)
    if not otp_data:
        return jsonify({"success": False, "message": "No pending verification found."})

    # Generate fresh OTP
    otp = generate_otp()
    otp_data["otp"] = otp
    otp_data["expires"] = datetime.now() + timedelta(minutes=10)

    pending = otp_data.get("pending_data", {})
    email = pending.get("email") or USERS.get(user_id, {}).get("email", "")
    phone = pending.get("phone") or USERS.get(user_id, {}).get("phone", "")
    name = pending.get("name") or USERS.get(user_id, {}).get("name", "User")

    email_sent = send_otp_email(email, otp, name) if email else False
    sms_sent = send_otp_sms(phone, otp, name) if phone else False

    return jsonify({"success": True, "message": "New OTP sent!"})


@app.route("/api/login", methods=["POST"])
def api_login():
    """Login with email + password. Then 2FA OTP is sent."""
    data = request.json
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email:
        return jsonify({"success": False, "message": "Email is required."})

    # Find user by email — prefer account with password_hash set
    found_uid = None
    for uid, u in USERS.items():
        if u.get("email", "").lower() == email.lower():
            if u.get("password_hash"):
                found_uid = uid
                break
            elif not found_uid:
                found_uid = uid

    if not found_uid:
        return jsonify({"success": False, "message": "No account found with this email. Please register first."})

    user = USERS[found_uid]

    # Verify password
    if user.get("password_hash"):
        if not password:
            return jsonify({"success": False, "message": "Password is required.", "needs_password": True})
        if not verify_password(password, user["password_hash"]):
            return jsonify({"success": False, "message": "Incorrect password."})
    else:
        # Legacy user without password — let them set one now
        if not password:
            return jsonify({"success": False, "message": "Please enter a password to secure your account.", "needs_password": True})
        pwd_err = validate_password(password)
        if pwd_err:
            return jsonify({"success": False, "message": pwd_err})
        user["password_hash"] = hash_password(password)
        save_users()
        log.info(f"[Auth] Legacy user {found_uid} set password on login")

    # Check if 2FA/MFA is enabled for this user
    if user.get("mfa_enabled"):
        # Send 2FA OTP
        otp = generate_otp()
        OTP_STORE[found_uid] = {
            "otp": otp,
            "expires": datetime.now() + timedelta(minutes=10),
            "verified": False,
        }

        email_sent = send_otp_email(email, otp, user["name"])
        phone = user.get("phone", "")
        sms_sent = send_otp_sms(phone, otp, user["name"]) if phone else False

        channels = []
        if email_sent:
            channels.append(email)
        if sms_sent:
            masked_phone = phone[:3] + "****" + phone[-3:] if len(phone) > 6 else phone
            channels.append(masked_phone)

        return jsonify({
            "success": True, "user_id": found_uid, "otp_required": True,
            "message": f"2FA code sent to {' & '.join(channels) if channels else 'your registered contacts'}.",
        })
    else:
        # No 2FA — log in directly
        token = generate_session_token()
        SESSIONS[token] = {"user_id": found_uid, "logged_in_at": datetime.now().isoformat()}
        log.info(f"[Auth] Direct login (no 2FA): {found_uid}")
        return jsonify({
            "success": True, "user_id": found_uid, "otp_required": False,
            "session_token": token,
            "message": "Login successful!",
        })


@app.route("/api/reset_password", methods=["POST"])
def api_reset_password():
    """Request password reset — sends a reset link to the user's email."""
    data = request.json
    email = data.get("email", "").strip()

    if not email:
        return jsonify({"success": False, "message": "Email is required."})

    found_uid = None
    for uid, u in USERS.items():
        if u.get("email", "").lower() == email.lower():
            found_uid = uid
            break

    if not found_uid:
        return jsonify({"success": False, "message": "No account found with this email."})

    user = USERS[found_uid]

    # Generate a unique reset token
    token = hashlib.sha256(os.urandom(32)).hexdigest()[:48]
    RESET_TOKENS[token] = {
        "user_id": found_uid,
        "email": email,
        "expires": datetime.now() + timedelta(minutes=30),
    }

    # Build reset link
    base_url = PUBLIC_URL or f"http://{request.host}"
    reset_link = f"{base_url}/reset-password?token={token}"

    # Send reset email with link
    api_key = CONFIG.get("brevo", {}).get("api_key", "")
    verified_sender = CONFIG.get("applicant", {}).get("email", "abhibabariya007@gmail.com")
    email_sent = False
    if api_key and api_key != "YOUR_BREVO_API_KEY":
        html_body = f"""<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;color:#333">
<div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);padding:24px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CyberJobs</h1>
    <p style="color:rgba(255,255,255,0.8);margin:4px 0 0;font-size:13px">Password Reset</p>
</div>
<div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;padding:24px">
    <p style="font-size:14px">Hi <strong>{user['name']}</strong>,</p>
    <p style="font-size:14px;color:#555">We received a request to reset your password. Click the button below to set a new password:</p>
    <div style="text-align:center;margin:24px 0">
        <a href="{reset_link}" style="display:inline-block;background:#2563eb;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">Reset Password</a>
    </div>
    <p style="font-size:12px;color:#999">Or copy this link: <a href="{reset_link}" style="color:#2563eb;word-break:break-all">{reset_link}</a></p>
    <hr style="border:none;border-top:1px solid #eee;margin:16px 0">
    <p style="font-size:11px;color:#999">This link expires in 30 minutes. If you didn't request this, ignore this email.</p>
</div>
</div>"""

        payload = {
            "sender": {"name": "CyberJobs", "email": verified_sender},
            "to": [{"email": email}],
            "subject": "Reset Your CyberJobs Password",
            "htmlContent": html_body,
        }
        try:
            resp = requests.post("https://api.brevo.com/v3/smtp/email",
                                 headers={"api-key": api_key, "Content-Type": "application/json"},
                                 json=payload, timeout=30)
            email_sent = resp.status_code in (200, 201)
            if email_sent:
                log.info(f"[Auth] Password reset link sent to {email} for {found_uid}")
            else:
                log.warning(f"[Auth] Reset email failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log.error(f"[Auth] Reset email error: {e}")

    if email_sent:
        return jsonify({"success": True, "message": f"Password reset link sent to {email}. Check your inbox!"})
    else:
        # Email not available — give the user the reset link directly
        return jsonify({"success": True, "reset_url": reset_link, "message": "Redirecting to password reset..."})


@app.route("/reset-password")
def reset_password_page():
    """Serve the password reset page when user clicks the link from email."""
    token = request.args.get("token", "")
    token_data = RESET_TOKENS.get(token)

    if not token_data:
        return render_template_string(RESET_PAGE_HTML, valid=False, error="Invalid or expired reset link.", token="")

    if datetime.now() > token_data["expires"]:
        del RESET_TOKENS[token]
        return render_template_string(RESET_PAGE_HTML, valid=False, error="This reset link has expired. Please request a new one.", token="")

    return render_template_string(RESET_PAGE_HTML, valid=True, error="", token=token, email=token_data["email"])


@app.route("/api/reset_password_confirm", methods=["POST"])
def api_reset_password_confirm():
    """Set new password using a valid reset token."""
    data = request.json
    token = data.get("token", "").strip()
    new_password = data.get("new_password", "")

    if not token or not new_password:
        return jsonify({"success": False, "message": "All fields are required."})

    pwd_err = validate_password(new_password)
    if pwd_err:
        return jsonify({"success": False, "message": pwd_err})

    token_data = RESET_TOKENS.get(token)
    if not token_data:
        return jsonify({"success": False, "message": "Invalid or expired reset link. Request a new one."})

    if datetime.now() > token_data["expires"]:
        del RESET_TOKENS[token]
        return jsonify({"success": False, "message": "Reset link expired. Request a new one."})

    user_id = token_data["user_id"]
    USERS[user_id]["password_hash"] = hash_password(new_password)
    save_users()
    del RESET_TOKENS[token]
    log.info(f"[Auth] Password reset completed for {user_id}")

    return jsonify({"success": True, "message": "Password updated successfully! You can now login."})


@app.route("/api/toggle_mfa", methods=["POST"])
def api_toggle_mfa():
    """Enable or disable 2FA/MFA for a user."""
    data = request.json
    user_id = data.get("user_id", "").strip()
    enable = data.get("enable", False)

    if user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    USERS[user_id]["mfa_enabled"] = bool(enable)
    save_users()
    status = "enabled" if enable else "disabled"
    log.info(f"[Auth] 2FA {status} for {user_id}")
    return jsonify({"success": True, "mfa_enabled": bool(enable), "message": f"Two-Factor Authentication {status}."})


# Reset password page HTML template
RESET_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
    <title>Reset Password - CyberJobs</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:'Inter',sans-serif;min-height:100vh;background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 50%,#2563eb 100%);display:flex;align-items:center;justify-content:center;padding:20px}
        .card{background:#fff;border-radius:16px;padding:32px 28px;width:100%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
        .brand{text-align:center;margin-bottom:20px}
        .brand-logo{width:50px;height:50px;background:linear-gradient(135deg,#2563eb,#60a5fa);border-radius:12px;display:flex;align-items:center;justify-content:center;margin:0 auto 10px;font-size:18px;font-weight:800;color:#fff}
        .brand h1{font-size:22px;font-weight:800;color:#1a1a2e}
        .brand h1 span{color:#2563eb}
        .brand p{font-size:13px;color:#8a8a9a;margin-top:2px}
        label{display:block;font-size:12px;font-weight:600;color:#4a4a5a;margin:14px 0 4px}
        input[type=password]{width:100%;padding:12px 14px;border:1px solid #e2e4e9;border-radius:8px;font-size:16px;outline:none;font-family:inherit;-webkit-appearance:none}
        input:focus{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.1)}
        .btn{width:100%;padding:14px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;margin-top:16px;transition:all .15s;-webkit-appearance:none}
        .btn-primary{background:#2563eb;color:#fff}
        .btn-primary:hover{background:#1d4ed8}
        .btn:disabled{opacity:.5;pointer-events:none}
        .msg{text-align:center;padding:16px;border-radius:8px;font-size:13px;margin-top:14px;font-weight:500}
        .msg.success{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}
        .msg.error{background:#fef2f2;color:#dc2626;border:1px solid #fecaca}
        .info{font-size:11px;color:#8a8a9a;text-align:center;margin-top:12px}
        .email-badge{text-align:center;background:#eff6ff;color:#2563eb;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;margin-bottom:6px}
        .strength{height:4px;border-radius:2px;margin-top:6px;transition:all .3s}
        .str-weak{background:#dc2626;width:33%}
        .str-medium{background:#ea580c;width:66%}
        .str-strong{background:#16a34a;width:100%}
        .str-label{font-size:10px;margin-top:4px;font-weight:600}
        .rules{font-size:11px;color:#6b7280;margin-top:8px;line-height:1.6}
        .rules .ok{color:#16a34a}
        .rules .fail{color:#dc2626}
    </style>
</head>
<body>
    <div class="card">
        <div class="brand">
            <div class="brand-logo">CJ</div>
            <h1><span>Cyber</span>Jobs</h1>
            <p>Reset Your Password</p>
        </div>

        {% if not valid %}
        <div class="msg error">{{ error }}</div>
        <div class="info" style="margin-top:16px"><a href="/" style="color:#2563eb;text-decoration:none;font-weight:600">Back to Login</a></div>
        {% else %}
        <div class="email-badge">{{ email }}</div>

        <div id="resetForm">
            <label>New Password <span style="color:#dc2626">*</span></label>
            <input type="password" id="newPass" placeholder="e.g. MyPass@123" oninput="checkStrength()">
            <div class="strength" id="passStrength"></div>
            <div class="rules" id="passRules">
                <div id="rLen" class="fail">&#x2717; At least 8 characters</div>
                <div id="rUpper" class="fail">&#x2717; At least 1 uppercase letter (A-Z)</div>
                <div id="rSymbol" class="fail">&#x2717; At least 1 symbol (!@#$%^&*...)</div>
            </div>

            <label>Confirm Password <span style="color:#dc2626">*</span></label>
            <input type="password" id="confirmPass" placeholder="Re-enter password">

            <button class="btn btn-primary" onclick="resetPassword()" id="resetBtn">Reset Password</button>
        </div>

        <div id="resultMsg" style="display:none"></div>
        <div class="info">Strong password = 8+ chars, 1 uppercase, 1 symbol</div>

        <script>
            function checkStrength(){
                const p=document.getElementById('newPass').value;
                const bar=document.getElementById('passStrength');
                const hasLen=p.length>=8;
                const hasUpper=/[A-Z]/.test(p);
                const hasSymbol=/[^A-Za-z0-9]/.test(p);
                const score=(hasLen?1:0)+(hasUpper?1:0)+(hasSymbol?1:0);

                document.getElementById('rLen').className=hasLen?'ok':'fail';
                document.getElementById('rLen').innerHTML=(hasLen?'&#x2713;':'&#x2717;')+' At least 8 characters';
                document.getElementById('rUpper').className=hasUpper?'ok':'fail';
                document.getElementById('rUpper').innerHTML=(hasUpper?'&#x2713;':'&#x2717;')+' At least 1 uppercase letter (A-Z)';
                document.getElementById('rSymbol').className=hasSymbol?'ok':'fail';
                document.getElementById('rSymbol').innerHTML=(hasSymbol?'&#x2713;':'&#x2717;')+' At least 1 symbol (!@#$%^&*...)';

                if(score<=1){bar.className='strength str-weak'}
                else if(score===2){bar.className='strength str-medium'}
                else{bar.className='strength str-strong'}
            }

            function validatePassword(p){
                if(p.length<8) return 'Password must be at least 8 characters';
                if(!/[A-Z]/.test(p)) return 'Password must contain at least 1 uppercase letter';
                if(!/[^A-Za-z0-9]/.test(p)) return 'Password must contain at least 1 symbol (!@#$%^&*...)';
                return null;
            }

            async function resetPassword(){
                const np=document.getElementById('newPass').value;
                const cp=document.getElementById('confirmPass').value;
                const err=validatePassword(np);
                if(err){showMsg(err,'error');return}
                if(np!==cp){showMsg('Passwords do not match','error');return}

                const btn=document.getElementById('resetBtn');
                btn.disabled=true;btn.textContent='Resetting...';

                try{
                    const r=await fetch(window.location.origin+'/api/reset_password_confirm',{
                        method:'POST',
                        headers:{'Content-Type':'application/json','ngrok-skip-browser-warning':'true'},
                        body:JSON.stringify({token:'{{ token }}',new_password:np})
                    });
                    if(!r.ok){showMsg('Server error ('+r.status+'). Try again.','error');btn.disabled=false;btn.textContent='Reset Password';return}
                    const ct=r.headers.get('content-type')||'';
                    if(!ct.includes('application/json')){showMsg('Unexpected response. Please try again.','error');btn.disabled=false;btn.textContent='Reset Password';return}
                    const d=await r.json();
                    if(d.success){
                        document.getElementById('resetForm').style.display='none';
                        showMsg(d.message+'<br><br><a href="/" style="color:#2563eb;font-weight:600;text-decoration:none">Go to Login &rarr;</a>','success');
                    }else{
                        showMsg(d.message,'error');
                        btn.disabled=false;btn.textContent='Reset Password';
                    }
                }catch(e){
                    showMsg('Network error. Check your connection and try again.','error');
                    btn.disabled=false;btn.textContent='Reset Password';
                }
            }

            function showMsg(text,type){
                const msg=document.getElementById('resultMsg');
                msg.style.display='block';
                msg.className='msg '+type;
                msg.innerHTML=text;
            }
        </script>
        {% endif %}
    </div>
</body>
</html>"""


# ─── BOOKMARKS ──────────────────────────────────────────────────────

@app.route("/api/bookmark", methods=["POST"])
def api_toggle_bookmark():
    """Toggle bookmark for a job."""
    data = request.json or {}
    user_id = data.get("user_id", "")
    job_index = data.get("job_index")

    if not user_id:
        return jsonify({"success": False, "message": "user_id required."})
    if job_index is None or job_index < 0 or job_index >= len(ALL_JOBS):
        return jsonify({"success": False, "message": "Invalid job_index."})

    job = ALL_JOBS[job_index]
    jid = job_hash(job["title"], job["company"], job["platform"])

    bookmarks = load_bookmarks()
    if user_id not in bookmarks:
        bookmarks[user_id] = []

    if jid in bookmarks[user_id]:
        bookmarks[user_id].remove(jid)
        action = "removed"
    else:
        bookmarks[user_id].append(jid)
        action = "added"

    save_bookmarks(bookmarks)
    return jsonify({"success": True, "action": action, "job_hash": jid})


@app.route("/api/bookmarks")
def api_get_bookmarks():
    """Get bookmarked job hashes for a user."""
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"success": False, "message": "user_id required."})

    bookmarks = load_bookmarks()
    return jsonify({"success": True, "bookmarks": bookmarks.get(user_id, [])})


# ─── APPLICATION STATUS ────────────────────────────────────────────

@app.route("/api/app_status", methods=["POST"])
def api_update_app_status():
    """Update application status for a job."""
    data = request.json or {}
    user_id = data.get("user_id", "")
    job_hash_val = data.get("job_hash", "")
    status = data.get("status", "")

    if not user_id or not job_hash_val:
        return jsonify({"success": False, "message": "user_id and job_hash required."})

    valid_statuses = ["applied", "viewed", "interview", "offer", "rejected"]
    if status not in valid_statuses:
        return jsonify({"success": False, "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"})

    app_log = load_app_log()
    updated = False
    for entry in app_log:
        entry_hash = job_hash(entry.get("job_title", ""), entry.get("company", ""), entry.get("platform", ""))
        if entry.get("user_id") == user_id and entry_hash == job_hash_val:
            entry["status"] = status
            entry["status_updated_at"] = datetime.now().isoformat()
            updated = True
            break

    if not updated:
        return jsonify({"success": False, "message": "Application entry not found."})

    save_app_log(app_log)
    return jsonify({"success": True, "message": f"Status updated to '{status}'."})


# ─── APPLICATION NOTES ──────────────────────────────────────────────

@app.route("/api/notes", methods=["POST"])
def api_save_note():
    """Save a note for a job."""
    data = request.json or {}
    user_id = data.get("user_id", "")
    job_hash_val = data.get("job_hash", "")
    note = data.get("note", "")

    if not user_id or not job_hash_val:
        return jsonify({"success": False, "message": "user_id and job_hash required."})

    notes = load_app_notes()
    if user_id not in notes:
        notes[user_id] = {}

    notes[user_id][job_hash_val] = {
        "note": note,
        "updated_at": datetime.now().isoformat(),
    }
    save_app_notes(notes)
    return jsonify({"success": True, "message": "Note saved."})


@app.route("/api/notes")
def api_get_notes():
    """Get all notes for a user."""
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"success": False, "message": "user_id required."})

    notes = load_app_notes()
    return jsonify({"success": True, "notes": notes.get(user_id, {})})


# ─── RECOMMENDED JOBS ──────────────────────────────────────────────

@app.route("/api/recommended")
def api_recommended():
    """Get top N recommended jobs sorted by match_score, excluding applied."""
    user_id = request.args.get("user_id", "")
    limit = int(request.args.get("limit", 10))

    if not user_id or user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    applied_hashes = set(USERS[user_id].get("applied_jobs", []))
    scored_jobs = []

    for i, job in enumerate(ALL_JOBS):
        jid = job_hash(job["title"], job["company"], job["platform"])
        if jid in applied_hashes:
            continue
        score, matched = compute_match_score(user_id, job)
        if score > 0:
            j = dict(job)
            j["match_score"] = score
            j["match_keywords"] = matched[:8]
            j["job_index"] = i
            j["category"] = categorize_job(j.get("title", ""))
            j["freshness"] = compute_freshness(j.get("found_at", ""))
            scored_jobs.append(j)

    scored_jobs.sort(key=lambda x: x["match_score"], reverse=True)
    return jsonify({"success": True, "jobs": scored_jobs[:limit], "total": len(scored_jobs)})


# ─── SIMILAR JOBS ───────────────────────────────────────────────────

@app.route("/api/similar")
def api_similar():
    """Find jobs similar to a given job by title keyword overlap, company, or location."""
    job_index = request.args.get("job_index")
    limit = int(request.args.get("limit", 5))

    if job_index is None:
        return jsonify({"success": False, "message": "job_index required."})

    job_index = int(job_index)
    if job_index < 0 or job_index >= len(ALL_JOBS):
        return jsonify({"success": False, "message": "Invalid job_index."})

    target = ALL_JOBS[job_index]
    target_words = set(re.findall(r'\w+', target.get("title", "").lower()))
    target_company = target.get("company", "").lower().strip()
    target_location = target.get("location", "").lower().strip()

    scored = []
    for i, job in enumerate(ALL_JOBS):
        if i == job_index:
            continue
        job_words = set(re.findall(r'\w+', job.get("title", "").lower()))
        overlap = len(target_words & job_words)
        bonus = 0
        if target_company and job.get("company", "").lower().strip() == target_company:
            bonus += 2
        if target_location and job.get("location", "").lower().strip() == target_location:
            bonus += 1
        score = overlap + bonus
        if score > 0:
            j = dict(job)
            j["similarity_score"] = score
            j["job_index"] = i
            scored.append(j)

    scored.sort(key=lambda x: x["similarity_score"], reverse=True)
    return jsonify({"success": True, "jobs": scored[:limit]})


# ─── RECENT SEARCHES ───────────────────────────────────────────────

@app.route("/api/recent_searches", methods=["POST"])
def api_save_recent_search():
    """Save a recent search query."""
    data = request.json or {}
    user_id = data.get("user_id", "")
    query = data.get("query", "").strip()

    if not user_id or not query:
        return jsonify({"success": False, "message": "user_id and query required."})

    searches = load_recent_searches()
    if user_id not in searches:
        searches[user_id] = []

    # Remove duplicate if exists, then prepend
    searches[user_id] = [q for q in searches[user_id] if q != query]
    searches[user_id].insert(0, query)
    searches[user_id] = searches[user_id][:20]  # Keep last 20

    save_recent_searches(searches)
    return jsonify({"success": True, "message": "Search saved."})


@app.route("/api/recent_searches")
def api_get_recent_searches():
    """Get recent searches for a user."""
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"success": False, "message": "user_id required."})

    searches = load_recent_searches()
    return jsonify({"success": True, "searches": searches.get(user_id, [])})


# ─── COMPANY REVIEWS ───────────────────────────────────────────────

@app.route("/api/reviews")
def api_get_reviews():
    """Get reviews for a company (case-insensitive partial match)."""
    company = request.args.get("company", "").strip().lower()
    if not company:
        return jsonify({"success": False, "message": "company parameter required."})

    reviews = load_reviews()
    matched = [r for r in reviews if company in r.get("company", "").lower()]
    return jsonify({"success": True, "reviews": matched, "total": len(matched)})


@app.route("/api/reviews", methods=["POST"])
def api_post_review():
    """Submit a company review."""
    data = request.json or {}
    company = data.get("company", "").strip()
    rating = data.get("rating")
    title = data.get("title", "").strip()
    pros = data.get("pros", "").strip()
    cons = data.get("cons", "").strip()
    user_id = data.get("user_id", "")

    if not company or not rating or not user_id:
        return jsonify({"success": False, "message": "company, rating, and user_id required."})

    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Rating must be 1-5."})

    reviews = load_reviews()
    reviews.append({
        "company": company,
        "rating": rating,
        "title": title,
        "pros": pros,
        "cons": cons,
        "user_id": user_id,
        "created_at": datetime.now().isoformat(),
    })
    save_reviews(reviews)
    return jsonify({"success": True, "message": "Review submitted."})


# ─── SALARY INSIGHTS ───────────────────────────────────────────────

@app.route("/api/salary_insights")
def api_salary_insights():
    """Aggregate salary data from jobs + user submissions, grouped by role/location."""
    salary_data = load_salary_data()

    # Group by role
    by_role = {}
    for entry in salary_data:
        role = entry.get("role", "Unknown")
        if role not in by_role:
            by_role[role] = {"count": 0, "min": None, "max": None, "entries": []}
        by_role[role]["count"] += 1
        mn = entry.get("min_salary", 0)
        mx = entry.get("max_salary", 0)
        if by_role[role]["min"] is None or mn < by_role[role]["min"]:
            by_role[role]["min"] = mn
        if by_role[role]["max"] is None or mx > by_role[role]["max"]:
            by_role[role]["max"] = mx
        by_role[role]["entries"].append(entry)

    # Group by location
    by_location = {}
    for entry in salary_data:
        loc = entry.get("location", "Unknown")
        if loc not in by_location:
            by_location[loc] = {"count": 0, "min": None, "max": None}
        by_location[loc]["count"] += 1
        mn = entry.get("min_salary", 0)
        mx = entry.get("max_salary", 0)
        if by_location[loc]["min"] is None or mn < by_location[loc]["min"]:
            by_location[loc]["min"] = mn
        if by_location[loc]["max"] is None or mx > by_location[loc]["max"]:
            by_location[loc]["max"] = mx

    return jsonify({
        "success": True,
        "by_role": by_role,
        "by_location": by_location,
        "total_reports": len(salary_data),
    })


@app.route("/api/salary_report", methods=["POST"])
def api_salary_report():
    """Submit a salary data point."""
    data = request.json or {}
    role = data.get("role", "").strip()
    company = data.get("company", "").strip()
    min_salary = data.get("min_salary")
    max_salary = data.get("max_salary")
    location = data.get("location", "").strip()
    user_id = data.get("user_id", "")

    if not role or not user_id:
        return jsonify({"success": False, "message": "role and user_id required."})

    try:
        min_salary = int(min_salary) if min_salary is not None else 0
        max_salary = int(max_salary) if max_salary is not None else 0
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Salary values must be numbers."})

    salary_data = load_salary_data()
    salary_data.append({
        "role": role,
        "company": company,
        "min_salary": min_salary,
        "max_salary": max_salary,
        "location": location,
        "user_id": user_id,
        "submitted_at": datetime.now().isoformat(),
    })
    save_salary_data(salary_data)
    return jsonify({"success": True, "message": "Salary report submitted."})


# ─── EXPORT APPLICATIONS ───────────────────────────────────────────

@app.route("/api/export_apps")
def api_export_apps():
    """Export user's application history as CSV."""
    user_id = request.args.get("user_id", "")
    fmt = request.args.get("format", "csv")

    if not user_id:
        return jsonify({"success": False, "message": "user_id required."})

    app_log = load_app_log()
    user_apps = [e for e in app_log if e.get("user_id") == user_id]

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Job Title", "Company", "Platform", "Job URL", "Sent To", "Sent At", "Status", "Brevo Accepted"])
        for entry in user_apps:
            writer.writerow([
                entry.get("job_title", ""),
                entry.get("company", ""),
                entry.get("platform", ""),
                entry.get("job_url", ""),
                entry.get("sent_to", ""),
                entry.get("sent_at", ""),
                entry.get("status", "applied"),
                entry.get("brevo_accepted", ""),
            ])
        csv_data = output.getvalue()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=applications_{user_id}.csv"},
        )

    return jsonify({"success": False, "message": "Unsupported format. Use format=csv."})


# ─── JOB ALERTS ─────────────────────────────────────────────────────

@app.route("/api/alerts", methods=["POST"])
def api_save_alerts():
    """Save job alert preferences for a user."""
    data = request.json or {}
    user_id = data.get("user_id", "")

    if not user_id or user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    keywords = data.get("keywords", [])
    locations = data.get("locations", [])
    frequency = data.get("frequency", "daily")
    enabled = data.get("enabled", True)

    if frequency not in ["daily", "weekly", "instant"]:
        return jsonify({"success": False, "message": "Frequency must be daily, weekly, or instant."})

    USERS[user_id]["alerts"] = {
        "keywords": keywords,
        "locations": locations,
        "frequency": frequency,
        "enabled": enabled,
        "updated_at": datetime.now().isoformat(),
    }
    save_users()
    return jsonify({"success": True, "message": "Alert preferences saved."})


@app.route("/api/alerts")
def api_get_alerts():
    """Get job alert preferences for a user."""
    user_id = request.args.get("user_id", "")
    if not user_id or user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    alerts = USERS[user_id].get("alerts", {})
    return jsonify({"success": True, "alerts": alerts})


# ─── ADMIN PANEL ────────────────────────────────────────────────────
ADMIN_SECRET = CONFIG.get("admin_secret", "cyberjobs2026")


@app.route("/admin")
def admin_page():
    """Serve the admin panel (password protected via JS)."""
    return render_template_string(ADMIN_PAGE_HTML)


@app.route("/api/admin/auth", methods=["POST"])
def admin_auth():
    secret = (request.json or {}).get("secret", "")
    if secret == ADMIN_SECRET:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Wrong admin password."})


@app.route("/api/admin/dashboard", methods=["POST"])
def admin_dashboard_data():
    """Return all data for admin panel."""
    emailed = load_emailed()
    app_log = load_app_log()
    users_data = {}
    for uid, u in USERS.items():
        users_data[uid] = {
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "has_password": bool(u.get("password_hash")),
            "mfa_enabled": u.get("mfa_enabled", False),
            "verified": u.get("verified", False),
            "registered_at": u.get("registered_at", ""),
            "resume": bool(u.get("resume_path") and os.path.exists(u.get("resume_path", ""))),
            "applied_count": len(u.get("applied_jobs", [])),
            "profile": u.get("profile", {}),
        }
    return jsonify({
        "users": users_data,
        "stats": STATS,
        "total_jobs": len(ALL_JOBS),
        "total_emails": len(emailed),
        "total_applications": len(app_log),
        "sessions": len(SESSIONS),
        "public_url": PUBLIC_URL or "N/A",
        "app_log": app_log[-50:],  # last 50 logs
        "emailed_count": len(emailed),
    })


@app.route("/api/admin/user_update", methods=["POST"])
def admin_user_update():
    """Update user fields from admin panel."""
    data = request.json
    uid = data.get("user_id", "")
    if uid not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    user = USERS[uid]
    if "name" in data and data["name"]:
        user["name"] = data["name"]
    if "email" in data and data["email"]:
        user["email"] = data["email"]
    if "phone" in data and data["phone"]:
        user["phone"] = data["phone"]
    if "mfa_enabled" in data:
        user["mfa_enabled"] = bool(data["mfa_enabled"])
    if "verified" in data:
        user["verified"] = bool(data["verified"])
    save_users()
    log.info(f"[Admin] Updated user: {uid}")
    return jsonify({"success": True, "message": f"User '{uid}' updated."})


@app.route("/api/admin/reset_user_password", methods=["POST"])
def admin_reset_user_password():
    """Admin force-reset a user's password."""
    data = request.json
    uid = data.get("user_id", "")
    new_pass = data.get("new_password", "")
    if uid not in USERS:
        return jsonify({"success": False, "message": "User not found."})
    pwd_err = validate_password(new_pass)
    if pwd_err:
        return jsonify({"success": False, "message": pwd_err})
    USERS[uid]["password_hash"] = hash_password(new_pass)
    save_users()
    log.info(f"[Admin] Password reset for {uid}")
    return jsonify({"success": True, "message": f"Password reset for '{uid}'."})


@app.route("/api/admin/delete_user", methods=["POST"])
def admin_delete_user():
    """Delete a user account."""
    uid = (request.json or {}).get("user_id", "")
    if uid not in USERS:
        return jsonify({"success": False, "message": "User not found."})
    del USERS[uid]
    save_users()
    # Clean sessions
    to_del = [k for k, v in SESSIONS.items() if v.get("user_id") == uid]
    for k in to_del:
        del SESSIONS[k]
    log.info(f"[Admin] Deleted user: {uid}")
    return jsonify({"success": True, "message": f"User '{uid}' deleted."})


@app.route("/api/admin/add_user", methods=["POST"])
def admin_add_user():
    """Admin creates a new user account directly (no OTP needed)."""
    data = request.json or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    verified = data.get("verified", True)

    if not name or not email or not password:
        return jsonify({"success": False, "message": "Name, email, and password are required."})

    # Check if email already exists
    for uid, u in USERS.items():
        if u.get("email", "").lower() == email.lower():
            return jsonify({"success": False, "message": f"Email already registered as '{uid}'."})

    pwd_err = validate_password(password)
    if pwd_err:
        return jsonify({"success": False, "message": pwd_err})

    user_id = name.lower().replace(" ", "")
    # Ensure unique ID
    base_id = user_id
    counter = 1
    while user_id in USERS:
        user_id = f"{base_id}{counter}"
        counter += 1

    USERS[user_id] = {
        "name": name,
        "email": email,
        "phone": phone,
        "password_hash": hash_password(password),
        "verified": verified,
        "mfa_enabled": False,
        "registered_at": datetime.now().isoformat(),
        "applied_jobs": [],
        "profile": {},
    }
    save_users()
    log.info(f"[Admin] Created user: {user_id} ({email})")
    return jsonify({"success": True, "message": f"User '{user_id}' created successfully."})


@app.route("/api/admin/clear_applied", methods=["POST"])
def admin_clear_applied():
    """Clear applied jobs list for a user."""
    uid = (request.json or {}).get("user_id", "")
    if uid not in USERS:
        return jsonify({"success": False, "message": "User not found."})
    USERS[uid]["applied_jobs"] = []
    save_users()
    log.info(f"[Admin] Cleared applied jobs for {uid}")
    return jsonify({"success": True, "message": f"Applied jobs cleared for '{uid}'."})


@app.route("/api/admin/clear_all_jobs", methods=["POST"])
def admin_clear_all_jobs():
    """Clear all jobs from database."""
    global ALL_JOBS
    ALL_JOBS = []
    save_jobs_db()
    log.info("[Admin] All jobs cleared")
    return jsonify({"success": True, "message": "All jobs cleared."})


@app.route("/api/admin/clear_email_log", methods=["POST"])
def admin_clear_email_log():
    """Clear emailed log."""
    save_emailed([])
    log.info("[Admin] Email log cleared")
    return jsonify({"success": True, "message": "Email log cleared."})


@app.route("/api/admin/jobs", methods=["POST"])
def admin_jobs_data():
    """Return paginated jobs data for admin panel."""
    data = request.json or {}
    page = data.get("page", 1)
    per_page = data.get("per_page", 50)
    search = data.get("search", "").lower()

    filtered = ALL_JOBS
    if search:
        filtered = [j for j in ALL_JOBS if search in j.get("title", "").lower()
                     or search in j.get("company", "").lower()
                     or search in j.get("platform", "").lower()
                     or search in j.get("location", "").lower()]

    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    page_jobs = []
    for j in filtered[start:end]:
        page_jobs.append({
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "platform": j.get("platform", ""),
            "url": j.get("url", ""),
            "found_at": j.get("found_at", ""),
            "is_new": j.get("is_new", False),
        })

    # Platform breakdown
    platforms = {}
    for j in ALL_JOBS:
        p = j.get("platform", "Unknown")
        platforms[p] = platforms.get(p, 0) + 1

    return jsonify({
        "jobs": page_jobs,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_all": len(ALL_JOBS),
        "platforms": platforms,
        "scan_running": SCAN_STATUS.get("running", False),
    })


@app.route("/api/admin/delete_job", methods=["POST"])
def admin_delete_job():
    """Delete a specific job by title+company+platform hash."""
    data = request.json or {}
    title = data.get("title", "")
    company = data.get("company", "")
    platform = data.get("platform", "")
    target_hash = job_hash(title, company, platform)
    before = len(ALL_JOBS)
    ALL_JOBS[:] = [j for j in ALL_JOBS if job_hash(j.get("title",""), j.get("company",""), j.get("platform","")) != target_hash]
    removed = before - len(ALL_JOBS)
    if removed:
        save_jobs_db()
        log.info(f"[Admin] Deleted job: {title} @ {company}")
    return jsonify({"success": removed > 0, "message": f"Removed {removed} job(s)."})


ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin Panel - CyberJobs</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login-card{background:#1e293b;border-radius:16px;padding:32px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,.4)}
.login-card h1{text-align:center;font-size:22px;margin-bottom:20px;color:#60a5fa}
.login-card input{width:100%;padding:12px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e2e8f0;font-size:14px;outline:none;font-family:inherit}
.login-card input:focus{border-color:#3b82f6}
.login-card button{width:100%;padding:12px;border:none;border-radius:8px;background:#3b82f6;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-top:12px;font-family:inherit}
.login-card button:hover{background:#2563eb}

#adminPanel{display:none}
.topbar{background:#1e293b;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155;position:sticky;top:0;z-index:100}
.topbar h1{font-size:18px;font-weight:800;color:#60a5fa}
.topbar .url{font-size:11px;color:#64748b;margin-left:12px}
.topbar button{padding:8px 16px;border:none;border-radius:6px;background:#ef4444;color:#fff;font-size:12px;font-weight:600;cursor:pointer}

.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:16px 24px}
.stat-card{background:#1e293b;border-radius:12px;padding:16px;text-align:center;border:1px solid #334155}
.stat-card .val{font-size:28px;font-weight:800;color:#60a5fa}
.stat-card .lbl{font-size:11px;color:#64748b;font-weight:600;margin-top:4px;text-transform:uppercase}

.tabs{display:flex;gap:4px;padding:0 24px;margin-top:8px}
.tab{padding:10px 20px;border:none;border-radius:8px 8px 0 0;background:#1e293b;color:#94a3b8;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;border:1px solid #334155;border-bottom:none}
.tab.active{background:#0f172a;color:#60a5fa;border-color:#3b82f6}

.tab-content{background:#0f172a;border:1px solid #334155;border-radius:0 12px 12px 12px;margin:0 24px 24px;padding:20px;min-height:400px}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:10px 12px;background:#1e293b;color:#94a3b8;font-size:11px;text-transform:uppercase;font-weight:700;border-bottom:1px solid #334155;position:sticky;top:0}
td{padding:10px 12px;border-bottom:1px solid #1e293b;color:#cbd5e1;vertical-align:top}
tr:hover td{background:#1e293b}

.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700}
.badge-green{background:#064e3b;color:#34d399}
.badge-red{background:#450a0a;color:#f87171}
.badge-blue{background:#172554;color:#60a5fa}
.badge-yellow{background:#422006;color:#fbbf24}

.btn{padding:6px 12px;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;transition:.15s}
.btn-blue{background:#3b82f6;color:#fff}
.btn-blue:hover{background:#2563eb}
.btn-red{background:#dc2626;color:#fff}
.btn-red:hover{background:#b91c1c}
.btn-green{background:#16a34a;color:#fff}
.btn-green:hover{background:#15803d}
.btn-yellow{background:#d97706;color:#fff}
.btn-yellow:hover{background:#b45309}
.btn-outline{background:transparent;border:1px solid #475569;color:#94a3b8}
.btn-outline:hover{border-color:#60a5fa;color:#60a5fa}

.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center;padding:20px}
.modal-bg.show{display:flex}
.modal{background:#1e293b;border-radius:12px;padding:24px;width:100%;max-width:480px;max-height:85vh;overflow-y:auto}
.modal h2{font-size:16px;color:#f1f5f9;margin-bottom:16px}
.modal label{display:block;font-size:11px;font-weight:600;color:#94a3b8;margin:10px 0 4px;text-transform:uppercase}
.modal input,.modal select{width:100%;padding:10px;border:1px solid #334155;border-radius:6px;background:#0f172a;color:#e2e8f0;font-size:13px;outline:none;font-family:inherit}
.modal input:focus,.modal select:focus{border-color:#3b82f6}
.modal .btn-row{display:flex;gap:8px;margin-top:16px}

.search-bar{width:100%;padding:10px 14px;border:1px solid #334155;border-radius:8px;background:#1e293b;color:#e2e8f0;font-size:13px;outline:none;font-family:inherit;margin-bottom:14px}
.search-bar:focus{border-color:#3b82f6}

.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:13px;font-weight:600;z-index:300;animation:slideIn .3s}
.toast-success{background:#064e3b;color:#34d399;border:1px solid #34d399}
.toast-error{background:#450a0a;color:#f87171;border:1px solid #f87171}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}

.empty{text-align:center;padding:40px;color:#475569;font-size:14px}
.scroll-table{max-height:500px;overflow-y:auto}
</style>
</head>
<body>

<!-- Login Gate -->
<div class="login-wrap" id="loginGate">
    <div class="login-card">
        <h1>Admin Panel</h1>
        <input type="password" id="adminPass" placeholder="Enter admin password" onkeydown="if(event.key==='Enter')adminLogin()">
        <button onclick="adminLogin()">Login</button>
        <p id="loginErr" style="color:#f87171;font-size:12px;text-align:center;margin-top:10px;display:none"></p>
    </div>
</div>

<!-- Admin Panel -->
<div id="adminPanel">
    <div class="topbar">
        <div style="display:flex;align-items:center">
            <h1>CyberJobs Admin</h1>
            <span class="url" id="pubUrl"></span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
            <button class="btn btn-blue" onclick="refreshData()">Refresh</button>
            <button onclick="adminLogout()">Logout</button>
        </div>
    </div>

    <!-- Stats -->
    <div class="stats-row" id="statsRow"></div>

    <!-- Tabs -->
    <div class="tabs">
        <button class="tab active" onclick="switchTab('users',this)">Users</button>
        <button class="tab" onclick="switchTab('jobs',this)">Jobs</button>
        <button class="tab" onclick="switchTab('applications',this)">Applications</button>
        <button class="tab" onclick="switchTab('actions',this)">Quick Actions</button>
    </div>

    <!-- Users Tab -->
    <div class="tab-content" id="tab-users">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:14px">
            <input type="text" class="search-bar" style="margin-bottom:0;flex:1" placeholder="Search users by name, email, ID..." oninput="filterUsers(this.value)">
            <button class="btn btn-green" onclick="openAddUser()">+ Add User</button>
        </div>
        <div class="scroll-table">
            <table>
                <thead><tr>
                    <th>User ID</th><th>Name</th><th>Email</th><th>Phone</th><th>Password</th><th>2FA</th><th>Verified</th><th>Resume</th><th>Applied</th><th>Registered</th><th>Actions</th>
                </tr></thead>
                <tbody id="usersBody"></tbody>
            </table>
        </div>
    </div>

    <!-- Jobs Tab -->
    <div class="tab-content" id="tab-jobs" style="display:none">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:14px;flex-wrap:wrap">
            <input type="text" class="search-bar" style="margin-bottom:0;flex:1;min-width:200px" placeholder="Search jobs by title, company, platform, location..." id="jobSearch" oninput="debounceJobSearch()">
            <button class="btn btn-green" onclick="triggerScan()" id="scanBtn">Run Scan Now</button>
            <span id="scanStatus" style="font-size:12px;color:#94a3b8"></span>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="platformTags"></div>
        <div class="scroll-table" style="max-height:600px">
            <table>
                <thead><tr>
                    <th>Title</th><th>Company</th><th>Location</th><th>Platform</th><th>Found At</th><th>Link</th><th>Actions</th>
                </tr></thead>
                <tbody id="jobsBody"></tbody>
            </table>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;padding:8px 0">
            <span style="font-size:12px;color:#64748b" id="jobsPageInfo"></span>
            <div style="display:flex;gap:6px">
                <button class="btn btn-outline" onclick="jobsPage(-1)" id="jobsPrev">Prev</button>
                <button class="btn btn-outline" onclick="jobsPage(1)" id="jobsNext">Next</button>
            </div>
        </div>
    </div>

    <!-- Applications Tab -->
    <div class="tab-content" id="tab-applications" style="display:none">
        <div class="scroll-table">
            <table>
                <thead><tr>
                    <th>User</th><th>Job Title</th><th>Company</th><th>Platform</th><th>Sent To</th><th>Sent At</th><th>Accepted</th>
                </tr></thead>
                <tbody id="appLogBody"></tbody>
            </table>
        </div>
    </div>

    <!-- Quick Actions Tab -->
    <div class="tab-content" id="tab-actions" style="display:none">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px">
            <div class="stat-card" style="text-align:left">
                <h3 style="font-size:14px;color:#f1f5f9;margin-bottom:12px">Danger Zone</h3>
                <div style="display:flex;flex-direction:column;gap:8px">
                    <button class="btn btn-red" style="width:100%;padding:12px" onclick="confirmAction('Clear ALL Jobs','This will remove all scraped jobs from database.',clearAllJobs)">Clear All Jobs</button>
                    <button class="btn btn-yellow" style="width:100%;padding:12px" onclick="confirmAction('Clear Email Log','This will clear the sent email tracking list.',clearEmailLog)">Clear Email Log</button>
                </div>
            </div>
            <div class="stat-card" style="text-align:left">
                <h3 style="font-size:14px;color:#f1f5f9;margin-bottom:12px">System Info</h3>
                <div style="font-size:12px;color:#94a3b8;line-height:2" id="sysInfo"></div>
            </div>
        </div>
    </div>
</div>

<!-- Edit User Modal -->
<div class="modal-bg" id="editModal">
    <div class="modal">
        <h2 id="editTitle">Edit User</h2>
        <input type="hidden" id="editUid">
        <label>Name</label>
        <input type="text" id="editName">
        <label>Email</label>
        <input type="email" id="editEmail">
        <label>Phone</label>
        <input type="tel" id="editPhone">
        <label>2FA / MFA</label>
        <select id="editMfa"><option value="false">Disabled</option><option value="true">Enabled</option></select>
        <label>Verified</label>
        <select id="editVerified"><option value="false">No</option><option value="true">Yes</option></select>
        <div class="btn-row">
            <button class="btn btn-blue" onclick="saveUserEdit()">Save Changes</button>
            <button class="btn btn-outline" onclick="closeModal('editModal')">Cancel</button>
        </div>
    </div>
</div>

<!-- Reset Password Modal -->
<div class="modal-bg" id="resetModal">
    <div class="modal">
        <h2>Reset Password</h2>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:12px">For user: <strong id="resetForUser"></strong></p>
        <input type="hidden" id="resetUid">
        <label>New Password</label>
        <input type="password" id="resetPass" placeholder="Min 8 chars, 1 uppercase, 1 symbol">
        <label>Confirm Password</label>
        <input type="password" id="resetPassConfirm" placeholder="Re-enter password">
        <div class="btn-row">
            <button class="btn btn-blue" onclick="doResetPassword()">Reset Password</button>
            <button class="btn btn-outline" onclick="closeModal('resetModal')">Cancel</button>
        </div>
    </div>
</div>

<!-- Add User Modal -->
<div class="modal-bg" id="addUserModal">
    <div class="modal">
        <h2>Add New User</h2>
        <label>Name</label>
        <input type="text" id="addUserName" placeholder="Full name">
        <label>Email</label>
        <input type="email" id="addUserEmail" placeholder="Email address">
        <label>Phone</label>
        <input type="tel" id="addUserPhone" placeholder="Phone (optional)">
        <label>Password</label>
        <input type="password" id="addUserPass" placeholder="Min 8 chars, 1 uppercase, 1 symbol">
        <label>Verified</label>
        <select id="addUserVerified"><option value="true">Yes</option><option value="false">No</option></select>
        <div class="btn-row">
            <button class="btn btn-green" onclick="doAddUser()">Create User</button>
            <button class="btn btn-outline" onclick="closeModal('addUserModal')">Cancel</button>
        </div>
    </div>
</div>

<!-- Confirm Modal -->
<div class="modal-bg" id="confirmModal">
    <div class="modal">
        <h2 id="confirmTitle">Confirm</h2>
        <p style="font-size:13px;color:#94a3b8" id="confirmMsg"></p>
        <div class="btn-row" style="margin-top:20px">
            <button class="btn btn-red" id="confirmBtn" onclick="">Yes, Do It</button>
            <button class="btn btn-outline" onclick="closeModal('confirmModal')">Cancel</button>
        </div>
    </div>
</div>

<script>
let DATA={};
let pendingConfirmFn=null;

function toast(msg,type='success'){
    const t=document.createElement('div');
    t.className='toast toast-'+type;t.textContent=msg;
    document.body.appendChild(t);
    setTimeout(()=>t.remove(),3000);
}

async function adminLogin(){
    const pass=document.getElementById('adminPass').value;
    const r=await fetch('/api/admin/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({secret:pass})});
    const d=await r.json();
    if(d.success){
        sessionStorage.setItem('admin','1');
        document.getElementById('loginGate').style.display='none';
        document.getElementById('adminPanel').style.display='block';
        refreshData();
    }else{
        const e=document.getElementById('loginErr');e.textContent=d.message;e.style.display='block';
    }
}

function adminLogout(){
    sessionStorage.removeItem('admin');
    document.getElementById('adminPanel').style.display='none';
    document.getElementById('loginGate').style.display='flex';
    document.getElementById('adminPass').value='';
}

async function refreshData(){
    try{
        const r=await fetch('/api/admin/dashboard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
        DATA=await r.json();
        renderStats();
        renderUsers();
        renderAppLog();
        renderSysInfo();
    }catch(e){toast('Failed to load data','error')}
}

function renderStats(){
    const s=DATA.stats||{};
    const html=[
        {val:Object.keys(DATA.users||{}).length,lbl:'Users',color:'#60a5fa'},
        {val:DATA.total_jobs||0,lbl:'Total Jobs',color:'#34d399'},
        {val:s.emails_sent||0,lbl:'Emails Sent',color:'#fbbf24'},
        {val:s.applied||0,lbl:'Applied',color:'#a78bfa'},
        {val:DATA.total_applications||0,lbl:'App Logs',color:'#f472b6'},
        {val:DATA.sessions||0,lbl:'Active Sessions',color:'#fb923c'},
    ].map(x=>`<div class="stat-card"><div class="val" style="color:${x.color}">${x.val}</div><div class="lbl">${x.lbl}</div></div>`).join('');
    document.getElementById('statsRow').innerHTML=html;
    document.getElementById('pubUrl').textContent=DATA.public_url||'';
}

function renderUsers(){
    const users=DATA.users||{};
    const rows=Object.entries(users).map(([uid,u])=>{
        const regDate=u.registered_at?new Date(u.registered_at).toLocaleDateString():'Legacy';
        return `<tr data-search="${uid} ${u.name} ${u.email} ${u.phone}".toLowerCase()>
            <td><strong style="color:#60a5fa">${uid}</strong></td>
            <td>${u.name}</td>
            <td style="font-size:12px">${u.email}</td>
            <td style="font-size:12px">${u.phone||'-'}</td>
            <td>${u.has_password?'<span class="badge badge-green">Set</span>':'<span class="badge badge-red">None</span>'}</td>
            <td>${u.mfa_enabled?'<span class="badge badge-blue">ON</span>':'<span class="badge badge-yellow">OFF</span>'}</td>
            <td>${u.verified?'<span class="badge badge-green">Yes</span>':'<span class="badge badge-red">No</span>'}</td>
            <td>${u.resume?'<span class="badge badge-green">Yes</span>':'<span class="badge badge-red">No</span>'}</td>
            <td style="text-align:center">${u.applied_count}</td>
            <td style="font-size:11px">${regDate}</td>
            <td>
                <div style="display:flex;gap:4px;flex-wrap:wrap">
                    <button class="btn btn-blue" onclick="openEdit('${uid}')">Edit</button>
                    <button class="btn btn-yellow" onclick="openResetPass('${uid}')">Password</button>
                    <button class="btn btn-outline" onclick="clearApplied('${uid}')">Clear Apps</button>
                    <button class="btn btn-red" onclick="confirmAction('Delete ${uid}','This will permanently delete this user account.',()=>deleteUser('${uid}'))">Delete</button>
                </div>
            </td>
        </tr>`;
    }).join('');
    document.getElementById('usersBody').innerHTML=rows||'<tr><td colspan="11" class="empty">No users found</td></tr>';
}

function filterUsers(q){
    q=q.toLowerCase();
    document.querySelectorAll('#usersBody tr').forEach(tr=>{
        const s=tr.getAttribute('data-search')||'';
        tr.style.display=s.includes(q)?'':'none';
    });
}

function renderAppLog(){
    const logs=(DATA.app_log||[]).reverse();
    const rows=logs.map(l=>{
        const dt=l.sent_at?new Date(l.sent_at).toLocaleString():'';
        return `<tr>
            <td style="color:#60a5fa;font-weight:600">${l.user_id||'-'}</td>
            <td>${l.job_title||'-'}</td>
            <td>${l.company||'-'}</td>
            <td>${l.platform||'-'}</td>
            <td style="font-size:12px">${l.sent_to||'-'}</td>
            <td style="font-size:11px">${dt}</td>
            <td>${l.brevo_accepted?'<span class="badge badge-green">Yes</span>':'<span class="badge badge-red">No</span>'}</td>
        </tr>`;
    }).join('');
    document.getElementById('appLogBody').innerHTML=rows||'<tr><td colspan="7" class="empty">No application logs</td></tr>';
}

function renderSysInfo(){
    const html=`
        <div>Public URL: <strong>${DATA.public_url||'N/A'}</strong></div>
        <div>Total Jobs: <strong>${DATA.total_jobs||0}</strong></div>
        <div>Emails Tracked: <strong>${DATA.emailed_count||0}</strong></div>
        <div>Active Sessions: <strong>${DATA.sessions||0}</strong></div>
        <div>Total Users: <strong>${Object.keys(DATA.users||{}).length}</strong></div>
    `;
    document.getElementById('sysInfo').innerHTML=html;
}

function switchTab(name,el){
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    el.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c=>c.style.display='none');
    document.getElementById('tab-'+name).style.display='block';
}

function openModal(id){document.getElementById(id).classList.add('show')}
function closeModal(id){document.getElementById(id).classList.remove('show')}

function openEdit(uid){
    const u=(DATA.users||{})[uid];if(!u)return;
    document.getElementById('editUid').value=uid;
    document.getElementById('editTitle').textContent='Edit: '+uid;
    document.getElementById('editName').value=u.name;
    document.getElementById('editEmail').value=u.email;
    document.getElementById('editPhone').value=u.phone||'';
    document.getElementById('editMfa').value=u.mfa_enabled?'true':'false';
    document.getElementById('editVerified').value=u.verified?'true':'false';
    openModal('editModal');
}

async function saveUserEdit(){
    const uid=document.getElementById('editUid').value;
    const body={
        user_id:uid,
        name:document.getElementById('editName').value,
        email:document.getElementById('editEmail').value,
        phone:document.getElementById('editPhone').value,
        mfa_enabled:document.getElementById('editMfa').value==='true',
        verified:document.getElementById('editVerified').value==='true',
    };
    const r=await fetch('/api/admin/user_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    closeModal('editModal');
    if(d.success)refreshData();
}

function openResetPass(uid){
    const u=(DATA.users||{})[uid];if(!u)return;
    document.getElementById('resetUid').value=uid;
    document.getElementById('resetForUser').textContent=u.name+' ('+uid+')';
    document.getElementById('resetPass').value='';
    document.getElementById('resetPassConfirm').value='';
    openModal('resetModal');
}

async function doResetPassword(){
    const uid=document.getElementById('resetUid').value;
    const np=document.getElementById('resetPass').value;
    const npc=document.getElementById('resetPassConfirm').value;
    if(np!==npc){toast('Passwords do not match','error');return}
    const r=await fetch('/api/admin/reset_user_password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,new_password:np})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success){closeModal('resetModal');refreshData()}
}

async function clearApplied(uid){
    if(!confirm('Clear all applied jobs for '+uid+'?'))return;
    const r=await fetch('/api/admin/clear_applied',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success)refreshData();
}

async function deleteUser(uid){
    const r=await fetch('/api/admin/delete_user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    closeModal('confirmModal');
    if(d.success)refreshData();
}

function confirmAction(title,msg,fn){
    document.getElementById('confirmTitle').textContent=title;
    document.getElementById('confirmMsg').textContent=msg;
    pendingConfirmFn=fn;
    document.getElementById('confirmBtn').onclick=()=>{if(pendingConfirmFn)pendingConfirmFn();closeModal('confirmModal')};
    openModal('confirmModal');
}

async function clearAllJobs(){
    const r=await fetch('/api/admin/clear_all_jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success)refreshData();
}

async function clearEmailLog(){
    const r=await fetch('/api/admin/clear_email_log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success)refreshData();
}

// ─── Jobs Tab ───
let jobsCurrentPage=1;
let jobsSearchTimer=null;

function debounceJobSearch(){
    clearTimeout(jobsSearchTimer);
    jobsSearchTimer=setTimeout(()=>{jobsCurrentPage=1;loadJobs()},300);
}

function jobsPage(dir){
    jobsCurrentPage+=dir;
    if(jobsCurrentPage<1)jobsCurrentPage=1;
    loadJobs();
}

async function loadJobs(){
    const search=document.getElementById('jobSearch').value;
    try{
        const r=await fetch('/api/admin/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page:jobsCurrentPage,per_page:50,search:search})});
        const d=await r.json();
        renderJobsTab(d);
    }catch(e){toast('Failed to load jobs','error')}
}

function renderJobsTab(d){
    // Platform tags
    const platforms=d.platforms||{};
    document.getElementById('platformTags').innerHTML=Object.entries(platforms).map(([p,c])=>`<span class="badge badge-blue" style="font-size:11px;padding:4px 10px">${p}: ${c}</span>`).join('');

    // Scan status
    document.getElementById('scanStatus').textContent=d.scan_running?'Scan in progress...':'';
    document.getElementById('scanBtn').disabled=d.scan_running;
    document.getElementById('scanBtn').textContent=d.scan_running?'Scanning...':'Run Scan Now';

    // Jobs table
    const rows=(d.jobs||[]).map(j=>{
        const dt=j.found_at?new Date(j.found_at).toLocaleString():'';
        const newBadge=j.is_new?'<span class="badge badge-green" style="margin-left:6px">NEW</span>':'';
        return `<tr>
            <td><strong>${j.title}</strong>${newBadge}</td>
            <td>${j.company}</td>
            <td style="font-size:12px">${j.location||'-'}</td>
            <td><span class="badge badge-blue">${j.platform}</span></td>
            <td style="font-size:11px">${dt}</td>
            <td>${j.url?`<a href="${j.url}" target="_blank" style="color:#60a5fa;font-size:12px">Open</a>`:'-'}</td>
            <td><button class="btn btn-red" onclick="deleteJob('${j.title.replace(/'/g,"\\'")}','${j.company.replace(/'/g,"\\'")}','${j.platform.replace(/'/g,"\\'")}')">Delete</button></td>
        </tr>`;
    }).join('');
    document.getElementById('jobsBody').innerHTML=rows||'<tr><td colspan="7" class="empty">No jobs found</td></tr>';

    // Pagination
    const totalPages=Math.ceil(d.total/d.per_page)||1;
    document.getElementById('jobsPageInfo').textContent=`Showing ${d.jobs.length} of ${d.total} jobs (Page ${d.page}/${totalPages}) | Total in DB: ${d.total_all}`;
    document.getElementById('jobsPrev').disabled=d.page<=1;
    document.getElementById('jobsNext').disabled=d.page>=totalPages;
}

async function deleteJob(title,company,platform){
    if(!confirm('Delete job: '+title+' @ '+company+'?'))return;
    const r=await fetch('/api/admin/delete_job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,company,platform})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success)loadJobs();
}

async function triggerScan(){
    document.getElementById('scanBtn').disabled=true;
    document.getElementById('scanBtn').textContent='Starting...';
    try{
        const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
        const d=await r.json();
        toast(d.message||'Scan started','success');
        document.getElementById('scanStatus').textContent='Scan in progress...';
        document.getElementById('scanBtn').textContent='Scanning...';
        // Poll scan status every 10s
        const poll=setInterval(async()=>{
            try{
                const sr=await fetch('/api/scan_status');
                const sd=await sr.json();
                if(!sd.running){
                    clearInterval(poll);
                    document.getElementById('scanBtn').disabled=false;
                    document.getElementById('scanBtn').textContent='Run Scan Now';
                    document.getElementById('scanStatus').textContent='';
                    loadJobs();
                    refreshData();
                    toast('Scan complete!','success');
                }
            }catch(e){}
        },10000);
    }catch(e){
        toast('Failed to start scan','error');
        document.getElementById('scanBtn').disabled=false;
        document.getElementById('scanBtn').textContent='Run Scan Now';
    }
}

// ─── Add User from Admin ───
function openAddUser(){
    document.getElementById('addUserName').value='';
    document.getElementById('addUserEmail').value='';
    document.getElementById('addUserPhone').value='';
    document.getElementById('addUserPass').value='';
    document.getElementById('addUserVerified').value='true';
    openModal('addUserModal');
}

async function doAddUser(){
    const name=document.getElementById('addUserName').value.trim();
    const email=document.getElementById('addUserEmail').value.trim();
    const phone=document.getElementById('addUserPhone').value.trim();
    const password=document.getElementById('addUserPass').value;
    const verified=document.getElementById('addUserVerified').value==='true';
    if(!name||!email||!password){toast('Name, email, and password are required','error');return}
    const r=await fetch('/api/admin/add_user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,email,phone,password,verified})});
    const d=await r.json();
    toast(d.message,d.success?'success':'error');
    if(d.success){closeModal('addUserModal');refreshData()}
}

// Load jobs when jobs tab is opened
const origSwitchTab=switchTab;
switchTab=function(name,el){
    origSwitchTab(name,el);
    if(name==='jobs')loadJobs();
};

// Auto-login if session exists
if(sessionStorage.getItem('admin')){
    document.getElementById('loginGate').style.display='none';
    document.getElementById('adminPanel').style.display='block';
    refreshData();
}
</script>
</body>
</html>"""


@app.route("/api/profile", methods=["GET"])
def api_get_profile():
    """Get user profile details."""
    user_id = request.args.get("user_id", "")
    if user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    user = USERS[user_id]
    return jsonify({
        "success": True,
        "user_id": user_id,
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "resume_path": user.get("resume_path", ""),
        "has_resume": bool(user.get("resume_path") and os.path.exists(user.get("resume_path", ""))),
        "applications": len(user.get("applied_jobs", [])),
        "profile": user.get("profile", {}),
        "registered_at": user.get("registered_at", ""),
        "verified": user.get("verified", False),
        "mfa_enabled": user.get("mfa_enabled", False),
        "profile_score": compute_profile_score(user),
    })


@app.route("/api/profile", methods=["POST"])
def api_update_profile():
    """Update user profile details."""
    data = request.json
    user_id = data.get("user_id", "")
    if user_id not in USERS:
        return jsonify({"success": False, "message": "User not found."})

    user = USERS[user_id]

    # Update basic fields if provided
    if data.get("name"):
        user["name"] = data["name"].strip()
    if data.get("phone"):
        user["phone"] = data["phone"].strip()

    # Update profile fields
    profile = user.get("profile", {})
    for field in ["linkedin", "location", "experience", "skills", "bio"]:
        if field in data:
            profile[field] = data[field].strip()
    user["profile"] = profile

    save_users()
    log.info(f"[Profile] Updated for {user_id}")
    return jsonify({"success": True, "message": "Profile updated!"})


@app.route("/api/upload_resume", methods=["POST"])
def api_upload_resume():
    """Upload resume for a user."""
    user_id = request.form.get("user_id", "")
    if user_id not in USERS:
        return jsonify({"success": False, "message": "User not found. Register first."})

    if "resume" not in request.files:
        return jsonify({"success": False, "message": "No resume file provided."})

    file = request.files["resume"]
    if file.filename == "":
        return jsonify({"success": False, "message": "No file selected."})

    # Save resume
    resumes_dir = os.path.join(SCRIPT_DIR, "resumes")
    os.makedirs(resumes_dir, exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename)
    filepath = os.path.join(resumes_dir, f"{user_id}_{safe_name}")
    file.save(filepath)

    USERS[user_id]["resume_path"] = filepath
    save_users()

    log.info(f"[User] Resume uploaded for {user_id}: {filepath}")
    return jsonify({"success": True, "message": "Resume uploaded successfully!"})


@app.route("/api/users")
def api_users():
    user_list = []
    for uid, u in USERS.items():
        user_list.append({
            "user_id": uid, "name": u["name"], "email": u["email"],
            "phone": u.get("phone", ""),
            "has_resume": bool(u.get("resume_path") and os.path.exists(u.get("resume_path", ""))),
            "applications": len(u.get("applied_jobs", [])),
            "verified": u.get("verified", False),
        })
    return jsonify({"users": user_list})


@app.route("/api/job_details", methods=["POST"])
def api_job_details():
    """Fetch job description, HR emails from the job posting page."""
    data = request.json
    idx = data.get("job_index")
    if idx is None or idx >= len(ALL_JOBS):
        return jsonify({"success": False})

    job = ALL_JOBS[idx]
    url = job.get("url", "")
    platform = job.get("platform", "")
    result = {"success": True, "title": job["title"], "company": job["company"],
              "platform": platform, "url": url, "jd": "", "hr_emails": [], "mailto_links": []}

    if not url:
        result["jd"] = "No URL available for this job posting."
        return jsonify(result)

    is_linkedin_post = "Feed" in platform or "Profile" in platform or "/posts/" in url or "/feed/update" in url

    try:
        # Try selenium first for JS-rendered pages
        soup = selenium_scrape_multi([(url, 5)])[0][1] if SELENIUM_AVAILABLE else None
        if not soup:
            resp = requests.get(url, headers=get_headers(), timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

        if soup:
            # Remove scripts, styles, nav
            for tag in soup.find_all(["script", "style", "nav", "footer", "aside"]):
                tag.decompose()

            jd_text = ""

            if is_linkedin_post:
                # ── LinkedIn Post: extract actual post content ──
                # LinkedIn post containers
                post_selectors = [
                    {"class_": re.compile(r"feed-shared-update-v2__description|feed-shared-text|attributed-text", re.I)},
                    {"class_": re.compile(r"break-words|feed-shared-inline-show-more-text", re.I)},
                    {"class_": re.compile(r"update-components-text|feed-shared-update", re.I)},
                    {"attrs": {"data-test-id": re.compile(r"main-feed-activity-content")}},
                    {"class_": re.compile(r"core-section-container", re.I)},
                ]
                for sel in post_selectors:
                    post_div = soup.find(["div", "span", "section", "article"], **sel)
                    if post_div:
                        jd_text = post_div.get_text("\n", strip=True)
                        if len(jd_text) > 50:  # meaningful content
                            break

                # If selectors didn't work, try broader extraction
                if not jd_text or len(jd_text) < 50:
                    # Get all text, remove LinkedIn chrome
                    for tag in soup.find_all(["header"]):
                        tag.decompose()
                    body_text = soup.get_text("\n", strip=True)
                    # Filter out LinkedIn UI text
                    skip_patterns = re.compile(
                        r'^(Sign in|Join now|Report this|Like|Comment|Repost|Send|Share|'
                        r'More from|people reacted|comments?|reaction|'
                        r'See who|View all|Show less|Show more|'
                        r'Cookie|Privacy|Terms|LinkedIn|'
                        r'\d+ followers?|Dismiss|Close menu|'
                        r'Agree & Join|Accept & Continue|Skip to main).*',
                        re.I
                    )
                    lines = []
                    for line in body_text.split("\n"):
                        line = line.strip()
                        if len(line) < 3:
                            continue
                        if skip_patterns.match(line):
                            continue
                        if line in ["Like", "Comment", "Repost", "Send", "Share"]:
                            continue
                        lines.append(line)

                    # Find the actual post content — usually the longest continuous block
                    if lines:
                        jd_text = "\n".join(lines)

                # Prepend saved snippet if we have one and post content is thin
                snippet = job.get("snippet", "")
                if snippet and (not jd_text or len(jd_text) < 100):
                    jd_text = f"[From LinkedIn Post]\n{snippet}\n\n---\n\n{jd_text}" if jd_text else f"[From LinkedIn Post]\n{snippet}"

                # Add poster info
                poster = job.get("poster", "")
                if poster:
                    jd_text = f"Posted by: {poster}\n\n{jd_text}"

            else:
                # ── Regular job page: extract JD ──
                jd_selectors = [
                    {"class_": re.compile(r"job-description|jobDescription|jd-desc|styles_JDC|description|job-details|JDContainer", re.I)},
                    {"class_": re.compile(r"jobsearch-JobComponent|job_description|jobDescriptionContent", re.I)},
                    {"id": re.compile(r"job-description|jobDescription|jd", re.I)},
                    {"attrs": {"data-testid": re.compile(r"job-description|jobDescription")}},
                ]
                for sel in jd_selectors:
                    jd_div = soup.find(["div", "section", "article"], **sel)
                    if jd_div:
                        jd_text = jd_div.get_text("\n", strip=True)
                        break

                if not jd_text:
                    main = soup.find(["main", "article"]) or soup.find("div", class_=re.compile(r"content|main|body"))
                    if main:
                        jd_text = main.get_text("\n", strip=True)
                    else:
                        jd_text = soup.get_text("\n", strip=True)

            # Clean and truncate
            lines = [l.strip() for l in jd_text.split("\n") if l.strip() and len(l.strip()) > 2]
            jd_text = "\n".join(lines[:100])
            if len(jd_text) > 5000:
                jd_text = jd_text[:5000] + "..."
            result["jd"] = jd_text
            result["sections"] = parse_jd_sections(jd_text)

            # Extract HR emails from the page
            page_text = soup.get_text(" ", strip=True)
            found_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', page_text)
            blacklist = ['example.com', 'naukri.com', 'indeed.com', 'linkedin.com', 'glassdoor.com',
                         'google.com', 'facebook.com', 'schema.org', 'w3.org', 'sentry.io',
                         'jquery.com', 'github.com', 'apple.com', 'microsoft.com', 'cloudflare', 'amazonaws']
            hr_emails = list(set(e.lower() for e in found_emails if not any(b in e.lower() for b in blacklist) and len(e) > 5))

            # Also get mailto links
            for a in soup.find_all("a", href=re.compile(r"mailto:")):
                email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                if email and "@" in email and email not in hr_emails:
                    hr_emails.append(email)

            result["hr_emails"] = hr_emails

            # Generate mailto links (opens Gmail compose)
            for email in hr_emails:
                subject = quote_plus(f"REG : Applying for {job['title']} - Application")
                body = quote_plus(f"Dear Hiring Manager,\n\nI am writing to express my interest in the {job['title']} position at {job['company']}.\n\nI have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation.\n\nPlease find my resume attached.\n\nKind regards")
                gmail_link = f"https://mail.google.com/mail/?view=cm&to={email}&su={subject}&body={body}"
                result["mailto_links"].append({"email": email, "gmail_link": gmail_link})

            # Add emails found from LinkedIn posts
            post_emails = job.get("hr_emails_from_post", [])
            for email in post_emails:
                if email not in hr_emails:
                    hr_emails.append(email)
                    subject = quote_plus(f"REG : Applying for {job['title']} - Application")
                    body = quote_plus(f"Dear Hiring Manager,\n\nI am writing to express my interest in the {job['title']} position at {job['company']}.\n\nI have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation.\n\nPlease find my resume attached.\n\nKind regards")
                    gmail_link = f"https://mail.google.com/mail/?view=cm&to={email}&su={subject}&body={body}"
                    result["mailto_links"].append({"email": email, "gmail_link": gmail_link, "from_post": True})

            # Also add guessed HR emails with mailto
            guessed = guess_hr_emails(job.get("company", ""))
            for email in guessed[:6]:
                if email not in hr_emails:
                    subject = quote_plus(f"REG : Applying for {job['title']} - Application")
                    body = quote_plus(f"Dear Hiring Manager,\n\nI am writing to express my interest in the {job['title']} position at {job['company']}.\n\nI have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation.\n\nPlease find my resume attached.\n\nKind regards")
                    gmail_link = f"https://mail.google.com/mail/?view=cm&to={email}&su={subject}&body={body}"
                    result["mailto_links"].append({"email": email, "gmail_link": gmail_link, "guessed": True})

    except Exception as e:
        log.error(f"[JD Fetch] Error: {e}")
        result["jd"] = f"Could not fetch job details: {str(e)}"

    return jsonify(result)


@app.route("/api/ai_chat", methods=["POST"])
def api_ai_chat():
    """AI Assistant - answers questions about SOC jobs, interview prep, skills."""
    data = request.json
    question = data.get("question", "").strip().lower()
    job_idx = data.get("job_index")  # Optional: context about a specific job

    if not question:
        return jsonify({"answer": "Please ask a question about SOC Analyst jobs, interview prep, or skills."})

    # Try Gemini API first
    gemini_key = CONFIG.get("gemini_api_key", "")
    if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY":
        return ai_chat_gemini(question, job_idx, gemini_key)

    # Fallback: built-in knowledge base
    return ai_chat_builtin(question, job_idx)


def ai_chat_gemini(question, job_idx, api_key):
    """Use Google Gemini API for AI responses."""
    job_context = ""
    if job_idx is not None and job_idx < len(ALL_JOBS):
        job = ALL_JOBS[job_idx]
        job_context = f"\nContext - The user is asking about this job: {job['title']} at {job['company']} on {job['platform']}. URL: {job.get('url', 'N/A')}\n"

    prompt = f"""You are an AI career assistant specialized in cybersecurity and SOC (Security Operations Center) roles.
The user is looking for SOC Analyst L1 / Junior Cyber Security Analyst positions with 0-2 years experience.
{job_context}
User question: {question}

Provide a helpful, concise, actionable answer. Include specific tips, skills, tools, or interview answers as relevant.
Format with bullet points where appropriate. Keep response under 300 words."""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if text:
                return jsonify({"answer": text, "ai": "gemini"})
    except Exception as e:
        log.error(f"[Gemini] Error: {e}")

    return ai_chat_builtin(question, job_idx)


def ai_chat_builtin(question, job_idx):
    """Built-in knowledge base for SOC Analyst questions."""
    q = question.lower()

    # Job-specific context
    job_info = ""
    if job_idx is not None and job_idx < len(ALL_JOBS):
        job = ALL_JOBS[job_idx]
        job_info = f"This job is: {job['title']} at {job['company']} ({job['platform']})"

    knowledge = {
        "siem": "**SIEM Tools for SOC L1:**\n- **Splunk** - Most popular, learn SPL queries\n- **IBM QRadar** - Common in enterprise SOCs\n- **Microsoft Sentinel** - Azure cloud SIEM\n- **ELK Stack** - Open source (Elasticsearch, Logstash, Kibana)\n- **ArcSight** - HP enterprise SIEM\n\n**Key skills:** Log correlation, creating alerts, dashboards, writing detection rules, understanding log sources (firewall, IDS/IPS, endpoint).",

        "interview": "**Common SOC L1 Interview Questions:**\n\n1. **What is a SOC?** - Security Operations Center that monitors, detects, and responds to security incidents 24/7\n2. **What is SIEM?** - Security Information and Event Management - collects and analyzes logs\n3. **Difference between IDS and IPS?** - IDS detects and alerts, IPS detects and blocks\n4. **What is a false positive?** - An alert that incorrectly identifies normal activity as malicious\n5. **OSI Model layers?** - Physical, Data Link, Network, Transport, Session, Presentation, Application\n6. **What is phishing?** - Social engineering attack via email to steal credentials\n7. **Common ports:** HTTP(80), HTTPS(443), SSH(22), DNS(53), FTP(21), RDP(3389), SMTP(25)\n8. **What is incident response?** - Preparation, Identification, Containment, Eradication, Recovery, Lessons Learned\n9. **What is MITRE ATT&CK?** - Framework of adversary tactics and techniques\n10. **How do you triage an alert?** - Check severity, verify IOCs, check affected assets, correlate with other logs, escalate if needed",

        "skills": "**Must-Have Skills for SOC Analyst L1:**\n\n**Technical:**\n- SIEM tools (Splunk, QRadar, Sentinel)\n- Network fundamentals (TCP/IP, DNS, HTTP)\n- Log analysis and correlation\n- Malware analysis basics\n- Wireshark / tcpdump\n- Windows & Linux administration\n- Firewall / IDS / IPS concepts\n- Email header analysis (phishing)\n- MITRE ATT&CK framework\n\n**Certifications (helpful):**\n- CompTIA Security+\n- CEH (Certified Ethical Hacker)\n- Google Cybersecurity Certificate\n- BTL1 (Blue Team Level 1)\n- SOC Analyst certification (EC-Council)\n\n**Soft Skills:**\n- Analytical thinking\n- Documentation\n- Communication\n- Attention to detail",

        "salary": "**SOC Analyst L1 Salary (India):**\n- **Fresher (0-1 yr):** 2.5 - 4.5 LPA\n- **1-2 years:** 4 - 7 LPA\n- **Top companies:** 6 - 10 LPA\n- **Metro cities (Mumbai, Bangalore):** Higher range\n- **Remote roles:** Variable\n\n**Tips to negotiate:**\n- Get certified (Security+, CEH)\n- Show hands-on lab experience\n- Mention SOC tools you've worked with\n- Highlight any incident handling experience",

        "resume": "**SOC Analyst L1 Resume Tips:**\n\n1. **Summary:** 2-3 lines highlighting SOC monitoring, SIEM, incident response\n2. **Skills section:** List SIEM tools, networking, OS, security tools\n3. **Experience:** Use action verbs - Monitored, Analyzed, Investigated, Escalated, Documented\n4. **Projects:** Home lab, TryHackMe, HackTheBox, SOC simulations\n5. **Certifications:** Security+, CEH, Google Cybersecurity\n6. **Keywords:** SOC, SIEM, incident response, threat monitoring, log analysis, alert triage, MITRE ATT&CK\n\n**Avoid:** Generic summaries, listing irrelevant skills, typos",

        "prepare": "**How to Prepare for SOC L1 Role:**\n\n1. **Learn SIEM:** Free Splunk training at splunk.com\n2. **Practice:** TryHackMe SOC Level 1 path (free)\n3. **Study:** CompTIA Security+ material\n4. **Lab:** Set up ELK stack at home, analyze logs\n5. **Read:** MITRE ATT&CK framework\n6. **Follow:** Security blogs, Twitter infosec community\n7. **Projects:** Document everything you do in a portfolio\n8. **Mock interviews:** Practice common SOC questions\n\n**Free Resources:**\n- LetsDefend.io - SOC simulator\n- CyberDefenders.org - Blue team challenges\n- TryHackMe - SOC Level 1 path",

        "tools": "**Essential SOC Tools:**\n\n**SIEM:** Splunk, QRadar, Sentinel, ELK\n**EDR:** CrowdStrike, Carbon Black, SentinelOne, Defender for Endpoint\n**Network:** Wireshark, tcpdump, Zeek, Suricata\n**Threat Intel:** VirusTotal, AbuseIPDB, OTX, MISP\n**Ticketing:** ServiceNow, Jira, TheHive\n**SOAR:** Phantom, Demisto (XSOAR), Swimlane\n**Forensics:** Autopsy, Volatility, FTK\n**Sandbox:** Any.Run, Joe Sandbox, Cuckoo",
    }

    # Match question to knowledge
    for key, answer in knowledge.items():
        if key in q:
            return jsonify({"answer": answer + (f"\n\n---\n{job_info}" if job_info else ""), "ai": "builtin"})

    # Broader matching
    if any(w in q for w in ["interview", "question", "ask", "prepare for interview"]):
        return jsonify({"answer": knowledge["interview"], "ai": "builtin"})
    if any(w in q for w in ["skill", "learn", "know", "need to"]):
        return jsonify({"answer": knowledge["skills"], "ai": "builtin"})
    if any(w in q for w in ["salary", "pay", "package", "ctc", "lpa", "compensation"]):
        return jsonify({"answer": knowledge["salary"], "ai": "builtin"})
    if any(w in q for w in ["resume", "cv", "profile"]):
        return jsonify({"answer": knowledge["resume"], "ai": "builtin"})
    if any(w in q for w in ["tool", "software", "platform", "technology"]):
        return jsonify({"answer": knowledge["tools"], "ai": "builtin"})
    if any(w in q for w in ["prepare", "study", "start", "begin", "how to", "roadmap"]):
        return jsonify({"answer": knowledge["prepare"], "ai": "builtin"})
    if any(w in q for w in ["siem", "splunk", "qradar", "sentinel"]):
        return jsonify({"answer": knowledge["siem"], "ai": "builtin"})

    # Default
    default = f"""**I can help you with:**

- **"interview questions"** - Common SOC L1 interview Q&A
- **"skills needed"** - Must-have skills for SOC Analyst
- **"salary"** - Expected salary ranges in India
- **"resume tips"** - How to build a SOC analyst resume
- **"SIEM tools"** - SIEM tools you should learn
- **"how to prepare"** - Study roadmap and free resources
- **"tools"** - Essential SOC tools list

{job_info}

*Tip: Add a Gemini API key in config.json for full AI-powered answers to any question!*
Get free key at: https://aistudio.google.com/apikey"""

    return jsonify({"answer": default, "ai": "builtin"})


# ─── Company HR Finder ────────────────────────────────────────────────────────

def _scrape_emails_from_url(url, timeout=10):
    """Scrape real emails from a URL using requests + BeautifulSoup."""
    emails = set()
    try:
        resp = requests.get(url, headers=get_headers(), timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            found = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', page_text)
            blacklist = ['example.com', 'naukri.com', 'indeed.com', 'linkedin.com', 'glassdoor.com',
                         'google.com', 'facebook.com', 'schema.org', 'w3.org', 'sentry.io',
                         'jquery.com', 'github.com', 'apple.com', 'microsoft.com', 'cloudflare',
                         'amazonaws', 'gstatic.com', 'googleapis.com', 'bootstrapcdn']
            for e in found:
                if not any(b in e.lower() for b in blacklist) and len(e) > 5:
                    emails.add(e.lower())
            # mailto links
            for a in soup.find_all("a", href=re.compile(r"mailto:")):
                email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                if email and "@" in email and len(email) > 5:
                    emails.add(email)
            return emails, soup, resp.url
    except Exception:
        pass
    return emails, None, url


@app.route("/api/company_hr", methods=["POST"])
def api_company_hr():
    """Find REAL HR emails and careers page for a given company name or URL."""
    data = request.json
    company_input = data.get("company", "").strip()
    if not company_input:
        return jsonify({"success": False, "message": "Please enter a company name or URL"})

    log.info(f"[HR Finder] Looking up: {company_input}")

    result = {
        "success": True,
        "company": company_input,
        "hr_emails": [],
        "careers_url": "",
        "website": "",
        "mailto_links": [],
        "sources": [],
    }

    EMAIL_BLACKLIST = ['example.com', 'naukri.com', 'indeed.com', 'linkedin.com', 'glassdoor.com',
                       'google.com', 'facebook.com', 'schema.org', 'w3.org', 'sentry.io',
                       'jquery.com', 'github.com', 'apple.com', 'microsoft.com', 'cloudflare',
                       'amazonaws', 'gstatic.com', 'googleapis.com', 'bootstrapcdn', 'twitter.com',
                       'youtube.com', 'instagram.com', 'sentry-next.wixpress.com', 'wix.com',
                       'cloudfront.net', 'akamai', 'cdn.']

    def _is_valid_email(e):
        return e and "@" in e and len(e) > 5 and not any(b in e.lower() for b in EMAIL_BLACKLIST)

    def _extract_emails_from_text(text):
        found = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        return {e.lower() for e in found if _is_valid_email(e)}

    def _find_careers_link(soup, base_url):
        for a in soup.find_all("a", href=True):
            lt = a.get_text(strip=True).lower()
            lh = a["href"].lower()
            if any(k in lt or k in lh for k in ["career", "jobs", "hiring", "join us", "join-us",
                                                  "openings", "work with us", "work-with-us", "apply"]):
                cl = a["href"]
                if not cl.startswith("http"):
                    cl = urlparse(base_url).scheme + "://" + urlparse(base_url).netloc + "/" + cl.lstrip("/")
                return cl
        return None

    # Determine if input is URL or company name
    is_url = company_input.startswith("http://") or company_input.startswith("https://")

    if is_url:
        parsed = urlparse(company_input)
        domain = parsed.netloc.replace("www.", "")
        company_name = domain.split(".")[0].title()
        result["company"] = company_name
        result["website"] = f"https://{domain}"
        domains = [domain]
    else:
        company_name = company_input
        clean = re.sub(r'\s*(pvt|private|ltd|limited|inc|corp|llp|solutions|technologies|tech|services|india|global)\s*\.?\s*', '', company_name.lower())
        clean = re.sub(r'[^a-z0-9\s]', '', clean).strip()
        domain_base = clean.replace(" ", "")
        domains = [f"{domain_base}.com", f"{domain_base}.in", f"{domain_base}.co.in", f"{domain_base}.io"]

    all_emails = set()

    # ── Step 1: Use DuckDuckGo to find the REAL company website ──
    # (Google blocks scraping, DuckDuckGo works reliably)
    if not is_url:
        log.info(f"[HR Finder] Searching DuckDuckGo for {company_name} website...")
        ddg_queries = [
            f"{company_name} official website careers",
            f"{company_name} company careers contact email",
        ]
        for ddg_q in ddg_queries:
            try:
                resp = requests.get(
                    f"https://html.duckduckgo.com/html/?q={quote_plus(ddg_q)}",
                    headers=get_headers(), timeout=10
                )
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    # Extract result links
                    for a in soup.find_all("a", class_="result__a", href=True):
                        href = a.get("href", "")
                        # DuckDuckGo wraps URLs in redirects
                        if "uddg=" in href:
                            from urllib.parse import parse_qs as _pq
                            params = _pq(urlparse(href).query)
                            if "uddg" in params:
                                href = unquote(params["uddg"][0])
                        if not href.startswith("http"):
                            continue
                        href_lower = href.lower()
                        # Skip job portals
                        if any(s in href_lower for s in ["linkedin.com", "indeed.com", "naukri.com",
                                                          "glassdoor.com", "wikipedia.org", "youtube.com"]):
                            continue
                        parsed_href = urlparse(href)
                        site = parsed_href.netloc.replace("www.", "")
                        # Check if it's a careers page
                        if any(k in href_lower for k in ["career", "jobs", "hiring", "openings", "apply"]):
                            if not result["careers_url"]:
                                result["careers_url"] = href
                                log.info(f"[HR Finder] Found careers page: {href}")
                        # Set as website if looks like a company site
                        if not result["website"] and site and "." in site:
                            result["website"] = f"https://{site}"
                            if site not in [d for d in domains]:
                                domains.insert(0, site)
                            log.info(f"[HR Finder] Found website: https://{site}")
                            break  # Found a website, stop
                    # Also extract emails from search results text
                    page_text = soup.get_text(" ", strip=True)
                    all_emails.update(_extract_emails_from_text(page_text))
                time.sleep(0.5)
            except Exception as e:
                log.error(f"[HR Finder] DuckDuckGo error: {e}")

    # ── Step 2: Scrape company website pages directly (FAST & RELIABLE) ──
    urls_to_scrape = []
    if result["careers_url"]:
        urls_to_scrape.append(result["careers_url"])
    if result["website"]:
        base = result["website"].rstrip("/")
        urls_to_scrape.extend([base, f"{base}/careers", f"{base}/jobs", f"{base}/contact",
                               f"{base}/contact-us", f"{base}/about", f"{base}/about-us"])
    # Also try guessed domains with www prefix
    for d in domains[:3]:
        for prefix in ["https://www.", "https://"]:
            for path in ["", "/careers", "/contact", "/jobs", "/about"]:
                urls_to_scrape.append(f"{prefix}{d}{path}")

    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for u in urls_to_scrape:
        normalized = u.rstrip("/").lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_urls.append(u)
    urls_to_scrape = unique_urls

    scraped_emails = set()
    for url in urls_to_scrape[:12]:
        found, soup, final_url = _scrape_emails_from_url(url)
        scraped_emails.update(found)
        if soup:
            if not result["website"]:
                result["website"] = urlparse(final_url).scheme + "://" + urlparse(final_url).netloc
            # Find careers link on this page
            if not result["careers_url"]:
                cl = _find_careers_link(soup, final_url)
                if cl:
                    result["careers_url"] = cl
                    log.info(f"[HR Finder] Found careers link on page: {cl}")
                    # Also scrape the careers page we just found
                    cf, cs, _ = _scrape_emails_from_url(cl)
                    scraped_emails.update(cf)

    if scraped_emails:
        all_emails.update(scraped_emails)
        result["sources"].append({"method": "Website Scraping", "count": len(scraped_emails)})

    # ── Step 3: Try Selenium for JS-heavy sites (only if nothing found yet) ──
    if SELENIUM_AVAILABLE and len(all_emails) == 0:
        log.info(f"[HR Finder] No emails found yet, trying Selenium for {company_name}...")
        try:
            selenium_urls = []
            if result["careers_url"]:
                selenium_urls.append((result["careers_url"], 5))
            if result["website"]:
                base = result["website"].rstrip("/")
                selenium_urls.append((f"{base}/careers", 4))
                selenium_urls.append((f"{base}/contact", 3))
            elif domains:
                selenium_urls.append((f"https://www.{domains[0]}/careers", 4))
                selenium_urls.append((f"https://www.{domains[0]}/contact", 3))

            if selenium_urls:
                scraped_results = selenium_scrape_multi(selenium_urls[:3])
                sel_found = set()
                for url, soup in scraped_results:
                    if soup:
                        page_text = soup.get_text(" ", strip=True)
                        sel_found.update(_extract_emails_from_text(page_text))
                        # mailto links
                        for a in soup.find_all("a", href=re.compile(r"mailto:")):
                            email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                            if _is_valid_email(email):
                                sel_found.add(email)
                        # careers link
                        if not result["careers_url"]:
                            cl = _find_careers_link(soup, url)
                            if cl:
                                result["careers_url"] = cl
                        if not result["website"]:
                            result["website"] = urlparse(url).scheme + "://" + urlparse(url).netloc
                if sel_found:
                    all_emails.update(sel_found)
                    result["sources"].append({"method": "Selenium Deep Scrape", "count": len(sel_found)})
        except Exception as e:
            log.error(f"[HR Finder] Selenium error: {e}")

    # ── Step 4: Google fallback (only if still nothing found) ──
    if len(all_emails) == 0:
        log.info(f"[HR Finder] Trying Google search as last resort for {company_name}...")
        try:
            encoded = quote_plus(f'"{company_name}" careers contact email hr')
            resp = requests.get(
                f"https://www.google.com/search?q={encoded}&num=10",
                headers=get_headers(), timeout=10,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                page_text = soup.get_text(" ", strip=True)
                google_emails = _extract_emails_from_text(page_text)
                if google_emails:
                    all_emails.update(google_emails)
                    result["sources"].append({"method": "Google Search", "count": len(google_emails)})
                # Find website/careers from Google results
                for a in soup.find_all("a", href=True):
                    href = clean_google_url(a["href"]) if a["href"].startswith("/url?") else a["href"]
                    if not href or not href.startswith("http"):
                        continue
                    href_lower = href.lower()
                    if any(s in href_lower for s in ["linkedin.com", "indeed.com", "naukri.com", "glassdoor.com"]):
                        continue
                    if any(k in href_lower for k in ["career", "jobs", "hiring"]):
                        if not result["careers_url"]:
                            result["careers_url"] = href
                    if not result["website"]:
                        result["website"] = urlparse(href).scheme + "://" + urlparse(href).netloc
        except Exception as e:
            log.error(f"[HR Finder] Google fallback error: {e}")

    # ── Build response with only REAL verified emails ──
    real_emails = list(all_emails)
    result["hr_emails"] = real_emails

    for email in real_emails:
        subject = quote_plus(f"REG : Applying for Cyber Security Analyst - Application")
        body = quote_plus(f"Dear Hiring Manager,\n\nI am writing to express my interest in cybersecurity positions at {company_name}.\n\nI have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation.\n\nPlease find my resume attached.\n\nKind regards")
        gmail_link = f"https://mail.google.com/mail/?view=cm&to={email}&su={subject}&body={body}"
        result["mailto_links"].append({
            "email": email,
            "gmail_link": gmail_link,
            "guessed": False,
            "source": "verified",
        })

    if not real_emails:
        result["message"] = f"No verified emails found for {company_name}. Try the company's full URL (e.g. https://{company_name.lower().replace(' ','')}.com)"

    if not result["careers_url"] and result["website"]:
        result["careers_url"] = result["website"].rstrip("/") + "/careers"

    log.info(f"[HR Finder] Found {len(real_emails)} verified emails for {company_name}")
    return jsonify(result)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    load_jobs_db()
    load_users()

    # Kill any zombie browsers from previous crashed sessions
    kill_zombie_browsers()

    # Get ngrok auth token from environment or config
    ngrok_token = CONFIG.get("ngrok_token", os.environ.get("NGROK_AUTH_TOKEN", ""))

    global PUBLIC_URL
    PUBLIC_URL = None
    public_url = None

    # Detect Codespace public URL
    codespace_name = os.environ.get("CODESPACE_NAME")
    github_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    if codespace_name:
        PUBLIC_URL = f"https://{codespace_name}-5050.{github_domain}"
        public_url = PUBLIC_URL
        log.info(f"[Codespace] Public URL: {public_url}")
    elif ngrok_token:
        try:
            from pyngrok import ngrok, conf
            # Kill stale ngrok processes
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/IM", "ngrok.exe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    )
                    time.sleep(2)
                except Exception:
                    pass
            else:
                try:
                    subprocess.run(["pkill", "-f", "ngrok"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    time.sleep(1)
                except Exception:
                    pass
            try:
                ngrok.kill()
            except Exception:
                pass
            time.sleep(1)
            conf.get_default().auth_token = ngrok_token
            tunnel = ngrok.connect(5050, "http")
            PUBLIC_URL = tunnel.public_url
            public_url = PUBLIC_URL
            log.info(f"[ngrok] Public URL: {public_url}")
        except Exception as e:
            log.warning(f"ngrok failed: {e}")
            log.info("Dashboard will only be available locally.")
            public_url = None

    port = 5050
    print("\n" + "=" * 60)
    print("  SOC ANALYST L1 - AI JOB HUNTER DASHBOARD")
    print("=" * 60)
    print(f"\n  Local URL:  http://localhost:{port}")
    if public_url:
        print(f"  Public URL: {public_url}   <-- Share this link!")
    else:
        print("  Public URL: Not available (add ngrok_token to config.json)")
    print(f"\n  Auto-scan every 30 minutes")
    print("  Press Ctrl+C to stop\n")
    print("=" * 60 + "\n")

    # Initial scan — delayed 30s so dashboard loads instantly with existing jobs
    def initial_scan():
        time.sleep(30)
        if not SCAN_STATUS["running"]:
            SCAN_STATUS["running"] = True
            run_scan_subprocess()
    threading.Thread(target=initial_scan, daemon=True).start()

    # Periodic auto-scan every 30 minutes
    def periodic_scan():
        while True:
            time.sleep(1800)  # 30 minutes
            if not SCAN_STATUS["running"]:
                log.info("[AutoScan] Starting scheduled scan...")
                SCAN_STATUS["running"] = True
                run_scan_subprocess()
            else:
                log.info("[AutoScan] Scan already running, skipping scheduled scan.")
    threading.Thread(target=periodic_scan, daemon=True).start()

    # Use waitress for production-grade serving (handles concurrent requests properly)
    try:
        from waitress import serve
        log.info("Using Waitress WSGI server (production mode)")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        log.info("Waitress not found, using Flask dev server")
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


# Auto-init for gunicorn/Render (not running main server loop, just load data)
if os.environ.get("RENDER") or os.environ.get("GUNICORN_INIT"):
    load_jobs_db()
    load_users()
    log.info("[Init] Data loaded for production WSGI server")

    # Start background scan threads
    def _render_initial_scan():
        time.sleep(30)
        if not SCAN_STATUS["running"]:
            SCAN_STATUS["running"] = True
            run_scan_subprocess()
    threading.Thread(target=_render_initial_scan, daemon=True).start()

    def _render_periodic_scan():
        while True:
            time.sleep(1800)
            if not SCAN_STATUS["running"]:
                log.info("[AutoScan] Starting scheduled scan...")
                SCAN_STATUS["running"] = True
                run_scan_subprocess()
    threading.Thread(target=_render_periodic_scan, daemon=True).start()

    # Periodic reload from disk so all gunicorn workers stay in sync
    def _sync_jobs_from_disk():
        while True:
            time.sleep(120)  # Every 2 minutes
            try:
                load_jobs_db()
                log.info(f"[Sync] Reloaded {len(ALL_JOBS)} jobs from disk")
            except Exception:
                pass
    threading.Thread(target=_sync_jobs_from_disk, daemon=True).start()

    # Keep-alive self-ping to prevent Render free tier from sleeping
    def _keep_alive():
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        while True:
            time.sleep(600)  # Every 10 minutes
            try:
                if render_url:
                    requests.get(f"{render_url}/api/jobs", timeout=10)
                    log.info("[KeepAlive] Self-ping sent")
                else:
                    requests.get("http://localhost:10000/api/jobs", timeout=5)
                    log.info("[KeepAlive] Local ping sent")
            except Exception:
                pass
    threading.Thread(target=_keep_alive, daemon=True).start()


if __name__ == "__main__":
    main()
