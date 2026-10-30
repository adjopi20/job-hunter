# Cybersecurity Job Discovery

Small Python CLI: JobSpy → title/date filters → SQLite → Telegram. Finds jobs only; applying is manual. No AI, browsers, paid APIs, or services. Python 3.11+ recommended.

## Windows local testing

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python jobs.py --dry-run --limit 1
```

Dry run prints retained jobs and does not write SQLite or contact Telegram. Remove `--limit 1` to search all configured terms. Run from the project directory. If venv creation fails at ensurepip, use `python -m pip --python .venv/Scripts/python.exe install -r requirements.txt`.

Create a bot using Telegram's @BotFather, start a private conversation with it, and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` (or environment variables). Obtain the chat ID from the Bot API `getUpdates` response after sending your bot a message; don't share your token. Then run:

```powershell
.\.venv\Scripts\python jobs.py
```

### Windows dependency import failures

If a search reports `JobSpy dependency import failed`, show the original dependency error with:

```powershell
.\.venv\Scripts\python -c "from jobspy import scrape_jobs; print('JobSpy import OK')"
```

`An Application Control policy has blocked this file` means Windows blocked a native dependency (observed here: pandas). The search has not reached LinkedIn. Run the project on Ubuntu VPS using the instructions below, or in an already configured Ubuntu WSL environment. For WSL, create a separate Linux virtual environment; do not reuse Windows `.venv`. An administrator can review the policy for Windows use. This project does not change Windows security policies. Unit tests mock the scraper/Telegram and can pass while this live import remains blocked.

## Configuration

Edit `config.json`. Defaults include all twelve requested terms, Indonesia, Singapore, Malaysia, Australia, Europe, US, plus a worldwide remote search. LinkedIn is the single MVP board: its location interface supports global searches without Indeed country mappings. Regional searches include both onsite and remote results as returned by the source. A worldwide search does not guarantee worldwide eligibility.

84 sequential searches, at most 10 results each, 15-second gaps, and 90-second per-search timeout. No additional description fetches. Results are bounded, so coverage is deliberately incomplete. Searches are not retried by this application; JobSpy can retry internally. A failed search disables that board for the remainder of the run and produces exit status 1 after pending alerts are processed. JobSpy errors reported in dataframe metadata are treated as failures. Some upstream releases may return an empty frame on a blocked request; inspect upstream warnings in the log.

Title matching excludes internships, director, VP, head, sales, and obvious unrelated roles. Junior/mid roles are delivered first; senior individual contributors remain eligible. Known old dates are excluded with day-level tolerance; missing dates are retained and displayed as Unknown. Source `hours_old=48` is also passed. Remote labels distinguish explicit worldwide statements, restrictions, locations, and unknown eligibility. Text heuristics are advisory, not work authorization checks. LinkedIn often omits descriptions, so eligibility may remain unknown.

SQLite deduplicates canonical URLs and matching normalized title/company/location across sources. The latter can merge separate openings with identical fields; a previously seen URL is never announced again, including reposts. Keep the database indefinitely for durable deduplication. Pending messages survive failure and restart. A message is marked delivered only after Telegram confirms it. Delivery is **at least once**: a lost response or crash immediately after a successful send can cause a duplicate on retry. Telegram retries transient failures at most 3 times; 429 retry delays over 60 seconds defer to the next run. No daily "zero jobs" messages.

## Ubuntu VPS: daily at 07:00 Asia/Jakarta

Copy this project to `/opt/job-hunter`, owned by your deployment user. A small VPS is sufficient; pandas/JobSpy is the main memory cost. Measure on your VPS rather than assuming a fixed RAM budget.

```bash
sudo apt update
sudo apt install python3-venv util-linux
cd /opt/job-hunter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# Edit .env, then test:
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python jobs.py --dry-run --limit 1
.venv/bin/python jobs.py
```

Ubuntu cron typically uses the host timezone. On a dedicated VPS, set it explicitly, then add this with `crontab -e`:

```bash
sudo timedatectl set-timezone Asia/Jakarta
```

```cron
0 7 * * * cd /opt/job-hunter && /usr/bin/flock -n .run.lock /usr/bin/timeout 3h .venv/bin/python jobs.py >> jobs.log 2>&1
```

If the VPS must stay UTC, use `0 0 * * *` instead: Jakarta is UTC+7 year-round. Do not install both entries. `flock` prevents overlapping runs; avoid concurrent manual runs against the same database. Protect and back up `.env` and `jobs.sqlite3`. Rotate `jobs.log` using logrotate. Check exit codes/logs after deployment; no monitoring service is included.

## Source verification and access

Upstream: https://github.com/speedyapply/JobSpy (MIT), package `python-jobspy`. Integration uses the published `scrape_jobs(site_name, search_term, location, is_remote, results_wanted, hours_old, verbose)` interface and returned dataframe fields. Job Forager is not needed for this MVP. See `VERIFICATION.md` for actual local verification results.

Only use boards where your access and their terms permit it. Stop or reduce searches on blocking/rate warnings; do not bypass CAPTCHAs, authentication, or IP restrictions. This app adds no proxies. Public job board endpoints can change or block a VPS, so unattended discovery cannot guarantee daily results. Original board or employer URLs are used for manual review.

GitHub Actions is omitted: SQLite needs durable state across ephemeral runners, while cron keeps it local without extra infrastructure. Scheduling is installed on your VPS using the commands above; it is not installed on your computer by this repository.
