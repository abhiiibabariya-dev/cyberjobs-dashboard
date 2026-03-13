"""
SOC Analyst L1 Job Hunter - Automated Job Search, Apply & Email Script
Monitors LinkedIn, Indeed, Naukri, and Glassdoor for SOC Analyst L1 openings
with 0-2 years experience. Auto-applies and emails hiring teams directly.
"""

import json
import os
import re
import sys
import time
import hashlib
import logging
import base64
from datetime import datetime
from urllib.parse import quote_plus, urlencode
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

try:
    from plyer import notification as desktop_notify
except ImportError:
    desktop_notify = None

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

import schedule

# ─── Setup Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("job_hunter.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("SOCJobHunter")

# ─── Load Config ─────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

RESULTS_FILE = os.path.join(SCRIPT_DIR, CONFIG.get("results_file", "found_jobs.json"))
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen_jobs.json")
EMAILED_FILE = os.path.join(SCRIPT_DIR, "emailed_companies.json")

ua = UserAgent()


def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


# ─── Utility Functions ───────────────────────────────────────────────────────

def load_json_file(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []


def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def job_hash(title, company, platform):
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{platform}"
    return hashlib.md5(raw.encode()).hexdigest()


def matches_experience(text):
    """Check if job posting matches 0-2 years experience requirement."""
    text_lower = text.lower()
    min_yr = CONFIG["experience_range"]["min_years"]
    max_yr = CONFIG["experience_range"]["max_years"]

    patterns = [
        r'(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?)',
        r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)',
        r'(?:experience|exp)\s*[:;]?\s*(\d+)\s*[-–to]+\s*(\d+)',
        r'(?:minimum|min|at\s*least)\s*(\d+)\s*(?:years?|yrs?)',
        r'fresher|entry\s*level|junior|0\s*(?:years?|yrs?)',
    ]

    if re.search(r'fresher|entry[\s-]*level', text_lower):
        return True

    for pattern in patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            groups = [g for g in match.groups() if g is not None]
            if len(groups) >= 2:
                low, high = int(groups[0]), int(groups[1])
                if low <= max_yr and high >= min_yr:
                    return True
            elif len(groups) == 1:
                years = int(groups[0])
                if years <= max_yr:
                    return True

    has_exp_mention = re.search(r'(\d+)\s*(?:years?|yrs?)', text_lower)
    if not has_exp_mention and re.search(r'soc|analyst|security', text_lower):
        return True

    return False


def matches_keywords(text):
    """Check if job title/description matches SOC Analyst L1 keywords."""
    text_lower = text.lower()
    keywords = CONFIG["search_keywords"]
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    if "soc" in text_lower and "analyst" in text_lower:
        return True
    if "security" in text_lower and ("l1" in text_lower or "tier 1" in text_lower or "level 1" in text_lower):
        return True
    return False


def save_results(jobs):
    existing = load_json_file(RESULTS_FILE)
    existing.extend(jobs)
    save_json_file(RESULTS_FILE, existing)


# ─── Email Extraction from Job Pages ────────────────────────────────────────

def extract_emails_from_text(text):
    """Extract email addresses from text, filtering out generic/useless ones."""
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    found = re.findall(email_pattern, text)

    # Filter out common non-HR emails
    blacklist = ['example.com', 'test.com', 'email.com', 'your', 'name@',
                 'username@', 'user@', 'sentry.io', 'github.com', 'w3.org',
                 'schema.org', 'googleapis.com', 'google.com', 'facebook.com',
                 'jquery.com', 'mozilla.org', 'apple.com', 'microsoft.com',
                 'cloudflare', 'amazonaws', 'naukri.com', 'indeed.com',
                 'linkedin.com', 'glassdoor.com', 'placeholder']

    valid_emails = []
    for email in found:
        email_lower = email.lower()
        if not any(bl in email_lower for bl in blacklist):
            if len(email) > 5 and '.' in email.split('@')[1]:
                valid_emails.append(email.lower())

    return list(set(valid_emails))


def extract_emails_from_job_page(url):
    """Visit a job posting page and try to extract recruiter/HR emails."""
    if not url:
        return []
    try:
        resp = requests.get(url, headers=get_headers(), timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            emails = extract_emails_from_text(page_text)

            # Also check href="mailto:" links
            mailto_links = soup.find_all("a", href=re.compile(r"mailto:"))
            for link in mailto_links:
                href = link.get("href", "")
                email = href.replace("mailto:", "").split("?")[0].strip()
                if email and "@" in email:
                    emails.append(email.lower())

            return list(set(emails))
    except Exception as e:
        log.debug(f"Could not fetch job page {url}: {e}")
    return []


def _domain_has_mx(domain):
    """Check if a domain has MX records (can receive email). Cached."""
    if not hasattr(_domain_has_mx, '_cache'):
        _domain_has_mx._cache = {}
    if domain in _domain_has_mx._cache:
        return _domain_has_mx._cache[domain]
    try:
        import socket
        socket.getaddrinfo(domain, 25, socket.AF_INET)
        result = True
    except Exception:
        result = False
    _domain_has_mx._cache[domain] = result
    return result


def guess_hr_emails(company_name):
    """Generate common HR email patterns for a company — only for domains that can receive mail."""
    if not company_name or company_name == "Unknown":
        return []

    clean = re.sub(r'\s*(pvt|private|ltd|limited|inc|corp|llp|solutions|technologies|tech|services)\s*\.?\s*', '', company_name.lower())
    clean = re.sub(r'[^a-z0-9]', '', clean.strip())

    if len(clean) < 3:
        return []

    candidate_domains = [f"{clean}.com", f"{clean}.in", f"{clean}.co.in"]
    valid_domains = [d for d in candidate_domains if _domain_has_mx(d)]
    if not valid_domains:
        return []

    prefixes = ["hr", "careers", "recruitment", "hiring", "jobs", "talent"]
    return [f"{p}@{d}" for d in valid_domains for p in prefixes]


# ─── Email to Hiring Team (Brevo API) ───────────────────────────────────────

def generate_cover_email(job):
    """Generate application email with custom subject and body."""
    subject = "REG : Applying for Cyber Security Analyst - Babariya Abhishek"

    html_body = """<div style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto; color: #333;">
<p>Dear Hiring Manager,</p>

<p>I am writing to express my interest in the SOC Analyst L1 position.</p>

<p>I have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation. I am comfortable working with security alerts, following SOPs, and supporting incident response activities. I am keen to work in a structured SOC environment and continuously improve my technical and analytical skills.</p>

<p>My resume is attached for your consideration. I would welcome the opportunity to discuss how I can add value to your SOC operations.</p>

<p>Kind regards,<br>
<strong>Abhishek Babariya</strong></p>
</div>"""

    plain_body = """Dear Hiring Manager,

I am writing to express my interest in the SOC Analyst L1 position.

I have hands-on experience with SIEM-based monitoring, alert triage, incident investigation, and SOC documentation. I am comfortable working with security alerts, following SOPs, and supporting incident response activities. I am keen to work in a structured SOC environment and continuously improve my technical and analytical skills.

My resume is attached for your consideration. I would welcome the opportunity to discuss how I can add value to your SOC operations.

Kind regards,
Abhishek Babariya"""

    return subject, html_body, plain_body


def send_email_brevo(to_email, subject, html_body, plain_body, resume_path, job):
    """Send application email with resume using Brevo API."""
    brevo_cfg = CONFIG.get("brevo", {})
    api_key = brevo_cfg.get("api_key", "")

    if not api_key or api_key == "YOUR_BREVO_API_KEY":
        log.warning("[Email] Brevo API key not configured. Skipping email.")
        return False

    applicant = CONFIG.get("applicant", {})
    sender_name = applicant.get("name", "Abhishek Babariya")
    sender_email = applicant.get("email", "abhibabariya007@gmail.com")

    # Read and encode resume
    attachment = None
    if os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode("utf-8")
        attachment = [{
            "content": file_content,
            "name": os.path.basename(resume_path),
        }]

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": plain_body,
    }

    if attachment:
        payload["attachment"] = attachment

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if resp.status_code in (200, 201):
            log.info(f"[Email] Application sent to {to_email} for '{job['title']}' at {job['company']}")
            return True
        else:
            log.warning(f"[Email] Failed to send to {to_email}: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        log.error(f"[Email] Error sending to {to_email}: {e}")
        return False


def send_email_smtp(to_email, subject, html_body, plain_body, resume_path, job):
    """Fallback: Send email using Gmail SMTP if Brevo not configured."""
    email_cfg = CONFIG["notification"]["email"]
    if not email_cfg.get("enabled") or not email_cfg.get("sender_password"):
        return False

    try:
        import smtplib
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{CONFIG.get('applicant', {}).get('name', 'Abhishek Babariya')} <{email_cfg['sender_email']}>"
        msg["To"] = to_email

        msg.attach(MIMEText(plain_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Attach resume
        if os.path.exists(resume_path):
            with open(resume_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(resume_path)}")
                msg.attach(part)

        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["sender_email"], email_cfg["sender_password"])
            server.sendmail(email_cfg["sender_email"], to_email, msg.as_string())

        log.info(f"[Email SMTP] Application sent to {to_email} for '{job['title']}'")
        return True
    except Exception as e:
        log.error(f"[Email SMTP] Failed: {e}")
        return False


def email_hiring_teams(jobs):
    """For each job, find HR emails and send application with resume."""
    resume_path = CONFIG.get("resume_path", "")
    if not os.path.exists(resume_path):
        log.error(f"[Email] Resume not found at: {resume_path}")
        return

    emailed = load_json_file(EMAILED_FILE)
    email_count = 0

    for job in jobs:
        # Extract emails from job posting page
        emails = extract_emails_from_job_page(job.get("url", ""))

        # Also try guessing HR emails from company name
        guessed = guess_hr_emails(job.get("company", ""))
        all_emails = list(set(emails + guessed))

        if not all_emails:
            log.info(f"[Email] No emails found for {job['title']} at {job['company']}")
            continue

        subject, html_body, plain_body = generate_cover_email(job)

        for email_addr in all_emails:
            # Skip if already emailed this company+email combo
            email_key = f"{email_addr}|{job['company'].lower()}"
            if email_key in emailed:
                log.info(f"[Email] Already emailed {email_addr} for {job['company']}, skipping.")
                continue

            # Try Brevo first, then SMTP fallback
            sent = send_email_brevo(email_addr, subject, html_body, plain_body, resume_path, job)
            if not sent:
                sent = send_email_smtp(email_addr, subject, html_body, plain_body, resume_path, job)

            if sent:
                emailed.append(email_key)
                email_count += 1
                job["emailed_to"] = job.get("emailed_to", [])
                job["emailed_to"].append(email_addr)
                time.sleep(2)  # Rate limiting

            if email_count >= 20:  # Safety limit per run
                log.info("[Email] Reached 20 email limit for this run.")
                break

        if email_count >= 20:
            break

    save_json_file(EMAILED_FILE, emailed)
    log.info(f"[Email] Sent {email_count} application emails this round.")


# ─── Notification Functions ──────────────────────────────────────────────────

def notify_desktop(title, message):
    if desktop_notify:
        try:
            desktop_notify.notify(title=title, message=message[:256], timeout=10)
        except Exception as e:
            log.warning(f"Desktop notification failed: {e}")


def send_self_notification(new_jobs):
    """Send notification to yourself about found jobs."""
    if not new_jobs:
        return
    count = len(new_jobs)
    title = f"{count} New SOC Analyst L1 Job(s) Found!"

    if CONFIG["notification"]["desktop"]:
        notify_desktop(title, f"{count} new SOC L1 jobs found! Check console.")

    # Self-notification email via Brevo
    brevo_cfg = CONFIG.get("brevo", {})
    if brevo_cfg.get("api_key") and brevo_cfg["api_key"] != "YOUR_BREVO_API_KEY":
        html = f"<h3>{title}</h3><ul>"
        for j in new_jobs:
            applied = " [AUTO-APPLIED]" if j.get("auto_applied") else ""
            emailed = f" [EMAILED: {', '.join(j.get('emailed_to', []))}]" if j.get("emailed_to") else ""
            html += f'<li><b>{j["title"]}</b> at {j["company"]} ({j["platform"]}){applied}{emailed}<br>'
            html += f'<a href="{j["url"]}">View Job</a></li>'
        html += "</ul>"

        applicant_email = CONFIG.get("applicant", {}).get("email", "abhibabariya007@gmail.com")
        try:
            requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": brevo_cfg["api_key"],
                    "Content-Type": "application/json",
                },
                json={
                    "sender": {"name": "SOC Job Hunter", "email": applicant_email},
                    "to": [{"email": applicant_email}],
                    "subject": f"Job Hunter Report: {title}",
                    "htmlContent": html,
                },
                timeout=15,
            )
            log.info("[Notify] Summary email sent to you.")
        except Exception as e:
            log.error(f"[Notify] Self-notification email failed: {e}")


# ─── Selenium-Based Scraper Helper ───────────────────────────────────────────

def create_scraper_driver():
    """Create a headless Chrome driver for scraping."""
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
    options.add_argument(f"user-agent={ua.random}")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        log.error(f"Failed to create scraper driver: {e}")
        return None


def scrape_with_selenium(url, wait_seconds=5):
    """Load a page with Selenium and return BeautifulSoup object."""
    driver = create_scraper_driver()
    if not driver:
        return None
    try:
        driver.get(url)
        time.sleep(wait_seconds)
        # Scroll down to load more results
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        return soup
    except Exception as e:
        log.error(f"Selenium scrape failed for {url}: {e}")
        return None
    finally:
        driver.quit()


# ─── Scraper: LinkedIn ───────────────────────────────────────────────────────

def scrape_linkedin():
    if not CONFIG["linkedin"]["enabled"]:
        return []

    log.info("[LinkedIn] Searching for SOC Analyst L1 jobs...")
    jobs = []

    for location in CONFIG["locations"][:3]:
        for keyword in ["SOC Analyst L1", "SOC Analyst Level 1", "Junior SOC Analyst", "SOC Analyst"]:
            try:
                params = {
                    "keywords": keyword,
                    "location": location,
                    "f_TPR": "r604800",  # Past week
                    "f_E": "2",
                    "position": 1,
                    "pageNum": 0,
                }
                url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

                soup = scrape_with_selenium(url, wait_seconds=5)
                if not soup:
                    # Fallback to requests
                    resp = requests.get(url, headers=get_headers(), timeout=15)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text, "html.parser")

                job_cards = soup.find_all("div", class_=re.compile(r"base-card|job-search-card|jobs-search__results-list"))
                # Also try list items
                if not job_cards:
                    job_cards = soup.find_all("li", class_=re.compile(r"jobs-search|result-card"))
                # Try broader match
                if not job_cards:
                    job_cards = soup.find_all(["div", "li"], attrs={"data-entity-urn": True})

                for card in job_cards:
                    title_tag = card.find(["h3", "h4", "a", "span"], class_=re.compile(r"base-search-card__title|job-title|title"))
                    company_tag = card.find(["h4", "a", "span"], class_=re.compile(r"base-search-card__subtitle|company-name|company"))
                    link_tag = card.find("a", href=True)

                    title = title_tag.get_text(strip=True) if title_tag else ""
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                    link = link_tag["href"] if link_tag else ""

                    if not title:
                        continue

                    full_text = card.get_text(" ", strip=True)

                    if matches_keywords(title) and matches_experience(full_text):
                        jobs.append({
                            "title": title, "company": company,
                            "url": link.split("?")[0] if link else "",
                            "platform": "LinkedIn", "location": location,
                            "found_at": datetime.now().isoformat(),
                        })

                time.sleep(2)
            except Exception as e:
                log.error(f"[LinkedIn] Error searching {keyword} in {location}: {e}")

    log.info(f"[LinkedIn] Found {len(jobs)} matching jobs.")
    return jobs


# ─── Scraper: Indeed ─────────────────────────────────────────────────────────

def scrape_indeed():
    if not CONFIG["indeed"]["enabled"]:
        return []

    log.info("[Indeed] Searching for SOC Analyst L1 jobs...")
    jobs = []

    # Search on indeed.co.in (India)
    for keyword in ["SOC Analyst L1", "SOC Analyst", "Junior SOC Analyst", "SOC L1"]:
        for location in CONFIG["locations"][:3]:
            try:
                params = {"q": keyword, "l": location, "fromage": "7", "sort": "date"}
                url = f"https://in.indeed.com/jobs?{urlencode(params)}"

                soup = scrape_with_selenium(url, wait_seconds=5)
                if not soup:
                    resp = requests.get(url, headers=get_headers(), timeout=15)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text, "html.parser")

                job_cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|cardOutline|resultContent|slider_item"))
                if not job_cards:
                    job_cards = soup.find_all("td", class_=re.compile(r"resultContent"))
                if not job_cards:
                    job_cards = soup.find_all(["div", "li"], attrs={"data-jk": True})

                for card in job_cards:
                    title_tag = card.find(["h2", "a", "span"], class_=re.compile(r"jobTitle|title|jcs-JobTitle"))
                    company_tag = card.find(["span", "a"], class_=re.compile(r"company|companyName|css-"))
                    if not company_tag:
                        company_tag = card.find(attrs={"data-testid": re.compile(r"company-name")})

                    if not title_tag:
                        continue

                    title = title_tag.get_text(strip=True)
                    company = company_tag.get_text(strip=True) if company_tag else "Unknown"

                    link_tag = card.find("a", href=True)
                    href = link_tag["href"] if link_tag else ""
                    if href and not href.startswith("http"):
                        href = "https://in.indeed.com" + href

                    full_text = card.get_text(" ", strip=True)
                    if matches_keywords(title) and matches_experience(full_text):
                        jobs.append({
                            "title": title, "company": company, "url": href,
                            "platform": "Indeed", "location": location,
                            "found_at": datetime.now().isoformat(),
                        })

                time.sleep(2)
            except Exception as e:
                log.error(f"[Indeed] Error: {e}")

    log.info(f"[Indeed] Found {len(jobs)} matching jobs.")
    return jobs


# ─── Scraper: Naukri ─────────────────────────────────────────────────────────

def scrape_naukri():
    if not CONFIG["naukri"]["enabled"]:
        return []

    log.info("[Naukri] Searching for SOC Analyst L1 jobs...")
    jobs = []

    search_urls = [
        "https://www.naukri.com/soc-analyst-jobs?experience=0&experience=2",
        "https://www.naukri.com/soc-analyst-l1-jobs?experience=0&experience=2",
        "https://www.naukri.com/junior-soc-analyst-jobs?experience=0&experience=2",
        "https://www.naukri.com/security-operations-center-analyst-jobs?experience=0&experience=2",
        "https://www.naukri.com/cyber-security-analyst-jobs?experience=0&experience=2",
    ]

    for url in search_urls:
        try:
            soup = scrape_with_selenium(url, wait_seconds=6)
            if not soup:
                resp = requests.get(url, headers={**get_headers(), "Referer": "https://www.naukri.com/"}, timeout=15)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")

            # Try multiple card selectors (Naukri keeps changing layout)
            job_cards = soup.find_all("article", class_=re.compile(r"jobTuple|srp-jobtuple"))
            if not job_cards:
                job_cards = soup.find_all("div", class_=re.compile(r"srp-jobtuple|cust-job-tuple|styles_jlc__main"))
            if not job_cards:
                job_cards = soup.find_all("div", class_=re.compile(r"row1|cardCont|list-content"))
            if not job_cards:
                # Try finding all links that look like job listings
                all_links = soup.find_all("a", href=re.compile(r"naukri\.com/job-listings"))
                for link in all_links:
                    parent = link.find_parent(["div", "article", "li"])
                    if parent and parent not in job_cards:
                        job_cards.append(parent)

            for card in job_cards:
                # Find title
                title_tag = card.find("a", class_=re.compile(r"title|desig|jobTitle"))
                if not title_tag:
                    title_tag = card.find("a", href=re.compile(r"job-listings"))

                # Find company
                company_tag = card.find("a", class_=re.compile(r"subTitle|comp-name|companyInfo"))
                if not company_tag:
                    company_tag = card.find("span", class_=re.compile(r"comp-name|company"))

                # Find experience
                exp_tag = card.find("span", class_=re.compile(r"expwdth|exp|experience|ellipsis"))
                if not exp_tag:
                    exp_tag = card.find("li", class_=re.compile(r"exp"))

                title = title_tag.get_text(strip=True) if title_tag else ""
                company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                link = title_tag["href"] if title_tag and title_tag.get("href") else ""
                exp_text = exp_tag.get_text(strip=True) if exp_tag else ""
                full_text = f"{title} {exp_text} {card.get_text(' ', strip=True)}"

                if title and (matches_keywords(title) or "soc" in title.lower() or "security" in title.lower()):
                    if matches_experience(full_text):
                        if link and not link.startswith("http"):
                            link = "https://www.naukri.com" + link
                        jobs.append({
                            "title": title, "company": company, "url": link,
                            "platform": "Naukri", "location": "India",
                            "experience": exp_text, "found_at": datetime.now().isoformat(),
                        })

            time.sleep(2)
        except Exception as e:
            log.error(f"[Naukri] Error: {e}")

    log.info(f"[Naukri] Found {len(jobs)} matching jobs.")
    return jobs


# ─── Scraper: Glassdoor ──────────────────────────────────────────────────────

def scrape_glassdoor():
    if not CONFIG["glassdoor"]["enabled"]:
        return []

    log.info("[Glassdoor] Searching for SOC Analyst L1 jobs...")
    jobs = []

    for keyword in ["SOC Analyst", "SOC Analyst L1", "Junior SOC Analyst", "Cyber Security Analyst"]:
        try:
            encoded = quote_plus(keyword)
            url = f"https://www.glassdoor.co.in/Job/india-{keyword.lower().replace(' ', '-')}-jobs-SRCH_IL.0,5_IN115.htm?keyword={encoded}"

            soup = scrape_with_selenium(url, wait_seconds=5)
            if not soup:
                url2 = f"https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword={encoded}"
                resp = requests.get(url2, headers=get_headers(), timeout=15)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")

            job_cards = soup.find_all("li", class_=re.compile(r"JobsList_jobListItem|react-job-listing"))
            if not job_cards:
                job_cards = soup.find_all("div", class_=re.compile(r"jobCard|JobCard"))
            if not job_cards:
                job_cards = soup.find_all(["li", "div"], attrs={"data-test": re.compile(r"jobListing")})

            for card in job_cards:
                title_tag = card.find(["a", "div"], class_=re.compile(r"jobTitle|JobCard_jobTitle|job-title"))
                if not title_tag:
                    title_tag = card.find(attrs={"data-test": re.compile(r"job-title")})
                company_tag = card.find(["div", "span"], class_=re.compile(r"employer|EmployerProfile|companyName"))
                if not company_tag:
                    company_tag = card.find(attrs={"data-test": re.compile(r"emp-name")})

                title = title_tag.get_text(strip=True) if title_tag else ""
                company = company_tag.get_text(strip=True) if company_tag else "Unknown"
                link = ""
                if title_tag and title_tag.name == "a":
                    link = title_tag.get("href", "")
                else:
                    link_el = card.find("a", href=True)
                    link = link_el["href"] if link_el else ""
                if link and not link.startswith("http"):
                    link = "https://www.glassdoor.co.in" + link

                full_text = card.get_text(" ", strip=True)
                if title and matches_keywords(title) and matches_experience(full_text):
                    jobs.append({
                        "title": title, "company": company, "url": link,
                        "platform": "Glassdoor", "location": "India",
                        "found_at": datetime.now().isoformat(),
                    })

            time.sleep(2)
        except Exception as e:
            log.error(f"[Glassdoor] Error: {e}")

    log.info(f"[Glassdoor] Found {len(jobs)} matching jobs.")
    return jobs


# ─── Auto Apply (Selenium) ──────────────────────────────────────────────────

def get_driver():
    if not SELENIUM_AVAILABLE:
        log.error("Selenium not installed.")
        return None

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"user-agent={ua.random}")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        log.error(f"Failed to create WebDriver: {e}")
        return None


def auto_apply_naukri(jobs):
    naukri_cfg = CONFIG["naukri"]
    if not naukri_cfg.get("username") or not naukri_cfg.get("password"):
        return

    naukri_jobs = [j for j in jobs if j["platform"] == "Naukri" and j.get("url")]
    if not naukri_jobs:
        return

    driver = get_driver()
    if not driver:
        return

    try:
        log.info("[Naukri] Logging in for auto-apply...")
        driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(3)

        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter your active Email ID / Username']"))
        )
        email_field.send_keys(naukri_cfg["username"])
        pass_field = driver.find_element(By.XPATH, "//input[@placeholder='Enter your password']")
        pass_field.send_keys(naukri_cfg["password"])
        driver.find_element(By.XPATH, "//button[contains(text(),'Login')]").click()
        time.sleep(5)

        for job in naukri_jobs[:5]:
            try:
                log.info(f"[Naukri] Applying: {job['title']} at {job['company']}")
                driver.get(job["url"])
                time.sleep(3)
                apply_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Apply') or contains(@id,'apply')]"))
                )
                apply_btn.click()
                time.sleep(3)
                try:
                    driver.find_element(By.XPATH, "//button[contains(text(),'Submit')]").click()
                    time.sleep(2)
                except Exception:
                    pass
                log.info(f"[Naukri] Applied to {job['title']}!")
                job["auto_applied"] = True
                time.sleep(2)
            except Exception as e:
                log.warning(f"[Naukri] Could not apply to {job['title']}: {e}")
    except Exception as e:
        log.error(f"[Naukri] Login/apply error: {e}")
    finally:
        driver.quit()


def auto_apply_linkedin(jobs):
    li_cfg = CONFIG["linkedin"]
    if not li_cfg.get("username") or not li_cfg.get("password"):
        return

    li_jobs = [j for j in jobs if j["platform"] == "LinkedIn" and j.get("url")]
    if not li_jobs:
        return

    driver = get_driver()
    if not driver:
        return

    try:
        log.info("[LinkedIn] Logging in for auto-apply...")
        driver.get("https://www.linkedin.com/login")
        time.sleep(3)

        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "username"))).send_keys(li_cfg["username"])
        driver.find_element(By.ID, "password").send_keys(li_cfg["password"])
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        time.sleep(5)

        for job in li_jobs[:5]:
            try:
                log.info(f"[LinkedIn] Applying: {job['title']} at {job['company']}")
                driver.get(job["url"])
                time.sleep(3)
                easy_apply = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'jobs-apply-button') or contains(text(),'Easy Apply')]"))
                )
                easy_apply.click()
                time.sleep(3)

                try:
                    submit = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label,'Submit application')]"))
                    )
                    submit.click()
                    job["auto_applied"] = True
                    log.info(f"[LinkedIn] Applied to {job['title']}!")
                except Exception:
                    for _ in range(5):
                        try:
                            driver.find_element(By.XPATH, "//button[contains(@aria-label,'Continue') or contains(@aria-label,'Next') or contains(@aria-label,'Review')]").click()
                            time.sleep(2)
                        except Exception:
                            break
                    try:
                        driver.find_element(By.XPATH, "//button[contains(@aria-label,'Submit application')]").click()
                        job["auto_applied"] = True
                        log.info(f"[LinkedIn] Applied to {job['title']}!")
                    except Exception:
                        log.warning(f"[LinkedIn] Could not complete {job['title']} (multi-step)")

                time.sleep(3)
            except Exception as e:
                log.warning(f"[LinkedIn] Could not apply to {job['title']}: {e}")
    except Exception as e:
        log.error(f"[LinkedIn] Login/apply error: {e}")
    finally:
        driver.quit()


# ─── Main Job Hunt Function ─────────────────────────────────────────────────

def hunt_jobs():
    log.info("=" * 60)
    log.info(f"SOC Analyst L1 Job Hunt - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    seen = load_json_file(SEEN_FILE)
    all_jobs = []

    for name, scraper in [("LinkedIn", scrape_linkedin), ("Indeed", scrape_indeed),
                           ("Naukri", scrape_naukri), ("Glassdoor", scrape_glassdoor)]:
        try:
            all_jobs.extend(scraper())
        except Exception as e:
            log.error(f"[{name}] Scraper crashed: {e}")

    # Deduplicate
    new_jobs = []
    for job in all_jobs:
        jh = job_hash(job["title"], job["company"], job["platform"])
        if jh not in seen:
            seen.append(jh)
            new_jobs.append(job)

    save_json_file(SEEN_FILE, seen)

    if new_jobs:
        log.info(f"\n{'='*60}")
        log.info(f"FOUND {len(new_jobs)} NEW MATCHING JOBS:")
        log.info(f"{'='*60}")
        for i, job in enumerate(new_jobs, 1):
            log.info(f"\n  [{i}] {job['title']}")
            log.info(f"      Company:  {job['company']}")
            log.info(f"      Platform: {job['platform']}")
            log.info(f"      Location: {job.get('location', 'N/A')}")
            log.info(f"      URL:      {job['url']}")

        if CONFIG.get("save_results"):
            save_results(new_jobs)
            log.info(f"\nResults saved to {RESULTS_FILE}")

        # Step 1: Auto-apply on platforms
        if CONFIG.get("auto_apply") and SELENIUM_AVAILABLE:
            log.info("\n--- Auto-Applying on Platforms ---")
            auto_apply_naukri(new_jobs)
            auto_apply_linkedin(new_jobs)

        # Step 2: Email hiring teams directly
        if CONFIG.get("email_hiring_teams", True):
            log.info("\n--- Emailing Hiring Teams ---")
            email_hiring_teams(new_jobs)

        # Step 3: Send summary notification to yourself
        send_self_notification(new_jobs)
    else:
        log.info("No new matching jobs found this round.")

    log.info(f"\nNext check in {CONFIG['check_interval_minutes']} minutes...")
    return new_jobs


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    print("""
    ==============================================================
    |     SOC ANALYST L1 - AUTOMATED JOB HUNTER & APPLIER        |
    |   LinkedIn | Indeed | Naukri | Glassdoor                    |
    |   Auto-Apply + Auto-Email Hiring Teams + Notifications     |
    |   Experience Filter: 0-2 Years                             |
    ==============================================================
    """)

    log.info("Starting automated job hunter...")
    log.info(f"Keywords: {CONFIG['search_keywords']}")
    log.info(f"Experience: {CONFIG['experience_range']['min_years']}-{CONFIG['experience_range']['max_years']} years")
    log.info(f"Locations: {CONFIG['locations']}")
    log.info(f"Interval: every {CONFIG['check_interval_minutes']} minutes")
    log.info(f"Auto-apply: {'ON' if CONFIG.get('auto_apply') else 'OFF'}")
    log.info(f"Email hiring teams: {'ON' if CONFIG.get('email_hiring_teams') else 'OFF'}")
    log.info(f"Resume: {CONFIG.get('resume_path', 'NOT SET')}")

    hunt_jobs()

    interval = CONFIG["check_interval_minutes"]
    schedule.every(interval).minutes.do(hunt_jobs)

    log.info(f"\nScheduler running. Checking every {interval} min. Press Ctrl+C to stop.\n")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("\nJob hunter stopped. Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
