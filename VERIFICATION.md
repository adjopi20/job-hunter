# Verification — 2026-10-30

- Inspected the empty workspace and upstream JobSpy repository README and Python source before implementation: https://github.com/speedyapply/JobSpy and https://raw.githubusercontent.com/speedyapply/JobSpy/main/jobspy/__init__.py .
- Installed `python-jobspy==1.4.1` and `python-dotenv==1.2.4` into the project virtual environment using PyPI. Requirements pin these installed direct dependencies.
- Inspected the installed Python source interface for the integration's parameters.
- All 13 unit tests passed under Python 3.13.7. Tests cover role/date/URL filters, priority, conservative remote labels, URL and cross-board deduplication, SQLite persistence across restarts, HTML/link formatting, successful Telegram responses, bounded retries, authentication failures, and failed delivery followed by a successful next run without repeat notifications.
- Attempted `jobs.py --dry-run --limit 1` and a direct import/live search diagnostic. Both were blocked before any job board search: Windows Application Control rejects a pandas native DLL (`pandas._libs.writers`). Elevated execution produced the same error. No policy changes or bypasses were attempted.

**Live scraper success is not verified in this environment.** Run the documented one-query smoke test on the Ubuntu deployment host before enabling cron. This local policy error does not establish whether the board currently accepts requests from that VPS. A public board can also return zero matches normally.

Telegram tests use mocked HTTP responses; no live message was sent because no bot credentials were provided. Scheduling files/instructions are supplied; no VPS or system scheduler was configured here.

After relocation to Documents/JOB HUNTER, reproduced the same native DLL policy failure. The worker now preserves ImportError details and displays an Application Control hint. Two added tests cover this diagnostic and successful record normalization with a mocked scraper. All 13 tests passed; a repeated live dry run still stops at the Windows policy blocker.
