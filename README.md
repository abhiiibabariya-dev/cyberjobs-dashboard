<h1 align="center">CyberJobs Dashboard</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-3.0+-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
</p>

<p align="center"><b>Live SOC Analyst job hunting dashboard with real-time multi-board scanning</b></p>

---

## Overview

A Flask-based web dashboard that aggregates SOC Analyst L1 job listings from multiple job boards in real-time. Features one-click applications, job tracking, user authentication, and automated notifications.

## Features

- **Real-time job scanning** from multiple job boards
- **One-click automated applications** to matching positions
- **Web dashboard** with filtering, sorting, and tracking
- **User authentication** and personalized job feeds
- **Notifications** for new matching positions
- **Dockerized deployment** with Fly.io and Render support
- **Public URL exposure** via ngrok for sharing

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python, Flask 3.0+ |
| Frontend | HTML/CSS/JS (Jinja2 templates) |
| Scraping | BeautifulSoup4, requests, fake-useragent |
| Auth | bcrypt |
| Deployment | Docker, Fly.io, Render, Gunicorn |

## Quick Start

```bash
# Clone
git clone https://github.com/abhiiibabariya-dev/cyberjobs-dashboard.git
cd cyberjobs-dashboard

# Setup
cp config.example.json config.json    # Edit with your settings
pip install -r requirements.txt

# Run
python dashboard.py
```

### Docker

```bash
docker build -t cyberjobs-dashboard .
docker run -p 5000:5000 cyberjobs-dashboard
```

## Project Structure

```
cyberjobs-dashboard/
├── dashboard.py          # Main Flask application
├── soc_job_hunter.py     # Job scraping engine
├── run_scan.py           # Manual scan trigger
├── service_runner.py     # Background service manager
├── config.example.json   # Configuration template
├── requirements.txt      # Python dependencies
├── templates/
│   └── index.html        # Dashboard UI
├── Dockerfile            # Container config
├── fly.toml              # Fly.io deployment
└── render.yaml           # Render deployment
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
