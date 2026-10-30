"""Daily, sequential JobSpy discovery. No applications are submitted."""
import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import os
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LOG = logging.getLogger(__name__)
ROLE = re.compile(r'penetration test|pentest|\bvapt\b|red team|offensive security|\bsoc analyst\b|blue team|security analyst|application security|\bappsec\b|vulnerability management|security engineer|cybersecurity consultant|cyber.?security|\bcyber grc\b|information security|security consultant', re.I)
EXCLUDE = re.compile(r'\bintern(ship)?\b|\bdirector\b|\bvp\b|vice president|\bhead\b|\bsales\b|account executive|business development|physical security|security guard', re.I)

def clean(value):
    return '' if value is None or str(value).lower() in ('nan', 'nat', '<na>') else str(value).strip()

def safe_url(value):
    p = urlsplit(clean(value))
    return bool(p.scheme in ('http', 'https') and p.hostname and not p.username)

def canonical(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('trk', 'trackingid', 'refid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), urlencode(sorted(query)), ''))

def accepted(job, hours=48):
    title = clean(job.get('title'))
    if not ROLE.search(title) or EXCLUDE.search(title) or 'intern' in clean(job.get('job_type')).lower():
        return False
    if not safe_url(job.get('job_url', '')):
        return False
    posted = clean(job.get('date_posted'))[:10]
    try:
        # Date-only source data: retain the entire boundary day.
        if posted and date.fromisoformat(posted) < date.today() - timedelta(days=(hours + 23)//24):
            return False
    except ValueError:
        pass
    return True

def remote_status(job):
    text = ' '.join(clean(job.get(k)) for k in ('title', 'location', 'description'))
    if re.search(r'\bhybrid\b', text, re.I):
        return 'Hybrid'
    remote = clean(job.get('is_remote')).lower() == 'true' or re.search(r'\bremote\b|work from home', text, re.I)
    if not remote:
        return 'Onsite / not specified'
    # shortcut: eligibility is heuristic; review the original posting before applying.
    if re.search(r'\b(only|must reside|must be based|work authorization|eligible countries|us[- ]based|country.restricted)\b', text, re.I):
        return 'Remote — restricted (verify posting)'
    if re.search(r'work from anywhere|remote worldwide|worldwide remote|globally remote|anywhere in the world', text, re.I):
        return 'Remote — worldwide stated (verify posting)'
    location = clean(job.get('location'))
    if location and location.lower() not in ('remote', 'worldwide', 'anywhere'):
        return 'Remote — location listed: ' + location[:100] + ' (verify eligibility)'
    return 'Remote — eligibility unknown'

def priority(job):
    title = clean(job.get('title')).lower()
    try:
        posted = date.fromisoformat(clean(job.get('date_posted'))[:10]).toordinal()
    except ValueError:
        posted = 0
    return (bool(re.search(r'\bsenior\b|\bsr\b|\blead\b|principal|staff|manager', title)), not bool(re.search(r'junior|\bjr\b|associate|entry|mid', title)), -posted)

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, url TEXT UNIQUE, fingerprint TEXT, payload TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE INDEX IF NOT EXISTS fingerprint_idx ON jobs(fingerprint)')

    def add(self, job):
        url = canonical(job['job_url'])
        fp = hashlib.sha256('|'.join(re.sub(r'\s+', ' ', clean(job.get(k)).lower()) for k in ('title', 'company', 'location')).encode()).hexdigest()
        # Avoid cross-board duplicates only when company and location are present.
        if job.get('company') and job.get('location') and self.db.execute('SELECT 1 FROM jobs WHERE fingerprint=?', (fp,)).fetchone():
            return False
        cursor = self.db.execute('INSERT OR IGNORE INTO jobs(id,url,fingerprint,payload) VALUES(?,?,?,?)', (hashlib.sha256(url.encode()).hexdigest(), url, fp, json.dumps(job)))
        self.db.commit()
        return cursor.rowcount == 1

    def pending(self):
        return sorted([(key, json.loads(payload)) for key, payload in self.db.execute('SELECT id,payload FROM jobs WHERE notified=0')], key=lambda pair: priority(pair[1]))

    def mark(self, key):
        self.db.execute('UPDATE jobs SET notified=1 WHERE id=?', (key,))
        self.db.commit()

def scrape_worker(output, kwargs):
    try:
        try:
            from jobspy import scrape_jobs
        except ImportError as exc:
            detail = ' '.join(str(exc).split())[:500]
            hint = ''
            if 'application control policy' in detail.lower():
                hint = ' Use Ubuntu/WSL or ask your administrator to review the Windows Application Control policy.'
            output.put(('error', f'JobSpy dependency import failed: {type(exc).__name__}: {detail}.{hint}'))
            return
        frame = scrape_jobs(**kwargs)
        if frame.attrs.get('errors'):
            raise RuntimeError('Job board rejected search')
        output.put(('ok', [{k: clean(v) for k, v in row.items()} for row in frame.to_dict('records')]))
    except Exception as exc:
        output.put(('error', type(exc).__name__))

def search(kwargs, timeout):
    # A child process bounds upstream network hangs and releases scraper memory.
    import queue
    output = mp.Queue()
    process = mp.Process(target=scrape_worker, args=(output, kwargs))
    process.start()
    try:
        status, rows = output.get(timeout=timeout)
        if status != 'ok':
            raise RuntimeError(rows)
        return rows
    except queue.Empty:
        raise TimeoutError('JobSpy search timed out') from None
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        output.close()

def run(config, dry_run=False, limit=None):
    from telegram_notify import Telegram
    telegram = None if dry_run else Telegram(os.environ.get('TELEGRAM_BOT_TOKEN', ''), os.environ.get('TELEGRAM_CHAT_ID', ''))
    store = None if dry_run else Store(config['database'])
    count = 0
    failures = 0
    blocked = set()
    for remote, locations in ((False, config['locations']), (True, config['remote_locations'])):
        for location in locations:
            for keyword in config['keywords']:
                for site in config['sites']:
                    if site in blocked:
                        continue
                    if limit is not None and count >= limit:
                        break
                    if count:
                        time.sleep(config['delay_seconds'])
                    count += 1
                    LOG.info('Search %s: %s / %s / remote=%s', site, keyword, location, remote)
                    try:
                        rows = search(dict(site_name=[site], search_term=keyword, location=location, is_remote=remote, results_wanted=config['results_wanted'], hours_old=config['hours_old'], verbose=1), config['query_timeout_seconds'])
                        kept = [j for j in rows if accepted(j, config['hours_old'])]
                        LOG.info('Returned %d; retained %d', len(rows), len(kept))
                        for job in kept:
                            if dry_run:
                                print(json.dumps(job, ensure_ascii=False))
                            else:
                                store.add(job)
                    except Exception as exc:
                        failures += 1
                        blocked.add(site)
                        LOG.warning('Search failed: %s (%s)', type(exc).__name__, str(exc))
    if store:
        try:
            for key, job in store.pending():
                telegram.send(job)
                store.mark(key)
                time.sleep(1.1)
        finally:
            store.db.close()
    return 1 if failures else 0

def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--dry-run', action='store_true', help='Print results; no database writes or Telegram')
    parser.add_argument('--limit', type=int, help='Maximum search calls for smoke testing')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if config['sites'] != ['linkedin']:
        parser.error('MVP supports sites=["linkedin"] only; country-specific adapters need explicit country mapping')
    if not config['keywords'] or not config['locations'] or any(config[k] <= 0 for k in ('hours_old', 'results_wanted', 'query_timeout_seconds')) or config['delay_seconds'] < 5:
        parser.error('Invalid config: use positive limits and delay_seconds >= 5')
    try:
        return run(config, args.dry_run, args.limit)
    except Exception as exc:
        LOG.error('Run failed: %s', type(exc).__name__)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
