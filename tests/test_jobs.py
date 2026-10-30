import json
import unittest
import tempfile
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from jobs import Store, accepted, remote_status, priority, run, scrape_worker
from telegram_notify import Telegram, message

def job(**updates):
    return dict(title='SOC Analyst', company='Example', location='Indonesia', job_url='https://example.com/jobs/1', date_posted=date.today().isoformat(), site='linkedin', **updates)

class ScraperWorker(unittest.TestCase):
    def test_dependency_failure_preserves_actionable_detail(self):
        import builtins
        original_import = builtins.__import__
        def blocked_import(name, *args, **kwargs):
            if name == 'jobspy':
                raise ImportError('DLL load failed: An Application Control policy has blocked this file.')
            return original_import(name, *args, **kwargs)
        output = MagicMock()
        with patch('builtins.__import__', side_effect=blocked_import):
            scrape_worker(output, {})
        status, detail = output.put.call_args.args[0]
        self.assertEqual('error', status)
        self.assertIn('JobSpy dependency import failed: ImportError', detail)
        self.assertIn('Application Control policy', detail)
        self.assertIn('Ubuntu/WSL', detail)
        output.put.assert_called_once()

    def test_success_normalizes_records(self):
        import types
        frame = MagicMock()
        frame.attrs = {}
        frame.to_dict.return_value = [job()]
        scraper = MagicMock(return_value=frame)
        output = MagicMock()
        with patch.dict('sys.modules', {'jobspy': types.SimpleNamespace(scrape_jobs=scraper)}):
            scrape_worker(output, {'location': 'Indonesia'})
        scraper.assert_called_once_with(location='Indonesia')
        self.assertEqual(('ok', [job()]), output.put.call_args.args[0])

class Filters(unittest.TestCase):
    def test_roles(self):
        for title in ('Junior Penetration Tester', 'VAPT Engineer', 'Red Team Operator', 'Blue Team Analyst', 'Cyber GRC Analyst', 'Application Security Engineer'):
            j = job(); j['title'] = title
            self.assertTrue(accepted(j), title)
        for title in ('SOC Analyst Intern', 'Security Sales Engineer', 'VP Security', 'Head of Security', 'Security Director', 'Security Guard', 'Software Developer'):
            j = job(); j['title'] = title
            self.assertFalse(accepted(j), title)

    def test_dates_and_urls(self):
        j = job(); j['date_posted'] = (date.today()-timedelta(days=4)).isoformat()
        self.assertFalse(accepted(j))
        j['date_posted'] = ''; self.assertTrue(accepted(j))
        j['job_url'] = 'javascript:alert(1)'; self.assertFalse(accepted(j))

    def test_remote(self):
        self.assertIn('unknown', remote_status({'is_remote': True}))
        self.assertIn('worldwide stated', remote_status({'description': 'Remote worldwide; work from anywhere'}))
        self.assertIn('restricted', remote_status({'is_remote': True, 'description': 'Remote worldwide but must reside in US'}))
        self.assertIn('location listed', remote_status(job(is_remote=True)))
        self.assertEqual('Hybrid', remote_status({'description': 'hybrid work'}))

    def test_priority(self):
        junior = job(); junior['title'] = 'Junior SOC Analyst'
        senior = job(); senior['title'] = 'Senior SOC Analyst'
        self.assertLess(priority(junior), priority(senior))

class Persistence(unittest.TestCase):
    @patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_CHAT_ID': 'test'})
    @patch('jobs.search', return_value=[job()])
    @patch('telegram_notify.Telegram.send')
    def test_failed_delivery_is_retried_on_next_run(self, send, search):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            cfg = dict(database=str(Path(folder)/'jobs.sqlite3'), locations=['Indonesia'], remote_locations=[], keywords=['SOC Analyst'], sites=['linkedin'], delay_seconds=5, results_wanted=1, hours_old=48, query_timeout_seconds=5)
            send.side_effect = RuntimeError('offline')
            with self.assertRaises(RuntimeError):
                run(cfg)
            send.side_effect = None
            self.assertEqual(0, run(cfg))
            self.assertEqual(0, run(cfg))
            self.assertEqual(2, send.call_count)

    def test_restart_preserves_pending_and_sent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = str(Path(folder) / 'jobs.sqlite3')
            first = Store(path); first.add(job()); first.db.close()
            second = Store(path)
            key, _ = second.pending()[0]
            second.mark(key); second.db.close()
            third = Store(path)
            self.assertFalse(third.add(job()))
            self.assertEqual([], third.pending()); third.db.close()

    def test_dedup_and_delivery(self):
        store = Store(':memory:')
        self.addCleanup(store.db.close)
        self.assertTrue(store.add(job()))
        j = job(); j['job_url'] += '?utm_source=test'
        self.assertFalse(store.add(j))
        j['job_url'] = 'https://other.example/job/2'
        self.assertFalse(store.add(j))
        key, _ = store.pending()[0]
        self.assertEqual(1, len(store.pending()))
        store.mark(key)
        self.assertEqual([], store.pending())

class Notifications(unittest.TestCase):
    def test_escape_and_length(self):
        j = job(); j['title'] = '<Security & Analyst>'
        text = message(j)
        self.assertIn('&lt;Security &amp; Analyst&gt;', text)
        self.assertIn('href="https://example.com/jobs/1"', text)
        self.assertLess(len(text), 4096)

    @patch('telegram_notify.urlopen')
    def test_success(self, opener):
        response = MagicMock(); response.read.return_value = b'{"ok": true}'
        opener.return_value.__enter__.return_value = response
        Telegram('token', 'chat').send(job())
        payload = json.loads(opener.call_args.args[0].data)
        self.assertEqual('chat', payload['chat_id'])

    @patch('telegram_notify.time.sleep')
    @patch('telegram_notify.urlopen', side_effect=URLError('offline'))
    def test_retry_limit(self, opener, sleep):
        with self.assertRaises(RuntimeError):
            Telegram('token', 'chat').send(job())
        self.assertEqual(3, opener.call_count)

    @patch('telegram_notify.urlopen', side_effect=HTTPError('hidden', 401, 'no', {}, None))
    def test_no_retry_auth_failure(self, opener):
        with self.assertRaisesRegex(RuntimeError, '401'):
            Telegram('token', 'chat').send(job())
        self.assertEqual(1, opener.call_count)

if __name__ == '__main__':
    unittest.main()
