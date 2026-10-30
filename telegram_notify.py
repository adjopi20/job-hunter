import html
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jobs import clean, remote_status, safe_url

def message(job):
    def esc(key, size=180):
        return html.escape(clean(job.get(key))[:size] or 'Unknown')
    url = job.get('job_url_direct') or job['job_url']
    if not safe_url(url):
        url = job['job_url']
    if not safe_url(url) or len(url) > 1800:
        raise ValueError('Invalid application URL')
    return (f'<b>{esc("title")}</b>\n{esc("company")} · {esc("location")}\n'
            f'{html.escape(remote_status(job))}\nPosted: {esc("date_posted", 10)} · {esc("site", 40)}\n'
            f'<a href="{html.escape(url, quote=True)}">Open job / apply manually</a>')

class Telegram:
    def __init__(self, token, chat_id):
        if not token or not chat_id:
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID')
        self.url = f'https://api.telegram.org/bot{token}/sendMessage'
        self.chat_id = chat_id

    def send(self, job):
        payload = json.dumps(dict(chat_id=self.chat_id, text=message(job), parse_mode='HTML', link_preview_options={'is_disabled': True})).encode()
        for attempt in range(3):
            try:
                with urlopen(Request(self.url, data=payload, headers={'Content-Type': 'application/json'}), timeout=20) as response:
                    data = json.load(response)
                if not data.get('ok'):
                    raise RuntimeError('Telegram rejected message')
                return
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(f'Telegram HTTP {exc.code}') from None
                delay = 2 ** attempt
                if exc.code == 429:
                    try:
                        delay = int(json.loads(exc.read()).get('parameters', {}).get('retry_after', delay))
                    except (ValueError, TypeError):
                        pass
                if delay > 60:
                    raise RuntimeError('Telegram rate limit; retry on next run') from None
            except (URLError, TimeoutError, OSError):
                delay = 2 ** attempt
            if attempt < 2:
                time.sleep(delay)
        raise RuntimeError('Telegram delivery failed after 3 attempts') from None
