import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock
from sqlalchemy import text
from storage import create_database, news_frame
from notifications import save_alert, queue_alert, deliver_pending, alert_status


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.db, _ = create_database(f"sqlite:///{Path(self.folder.name) / 'test.db'}")
        self.news = news_frame(self.db)
        self.config = dict(SMTP_HOST='not-used', SMTP_USERNAME='test', SMTP_PASSWORD='test', SMTP_FROM='alerts@example.com')

    def tearDown(self):
        self.db.dispose()
        self.folder.cleanup()

    def test_opt_in_quality_and_deduplication(self):
        row = self.news[self.news.severity == 'Material'].iloc[0]
        pid = int(row.project_id)
        queue_alert(self.db, pid, row.source_url)
        self.assertEqual(alert_status(self.db, pid), [])
        save_alert(self.db, pid, '담당자', 'owner@example.com', True)
        queue_alert(self.db, pid, row.source_url)
        queue_alert(self.db, pid, row.source_url)
        sender = Mock()
        self.assertEqual(deliver_pending(self.db, self.config, sender)['sent'], 1)
        deliver_pending(self.db, self.config, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(sender.call_args.args[0]['To'], 'owner@example.com')
        watched = self.news[self.news.severity == 'Watch'].iloc[0]
        save_alert(self.db, int(watched.project_id), '담당자', 'owner@example.com', True)
        queue_alert(self.db, int(watched.project_id), watched.source_url)
        deliver_pending(self.db, self.config, sender)
        self.assertEqual(sender.call_count, 1)

    def test_disable_before_send_and_ambiguous_failure(self):
        row = self.news[self.news.severity == 'Critical'].iloc[0]
        pid = int(row.project_id)
        save_alert(self.db, pid, '담당자', 'owner@example.com', True)
        queue_alert(self.db, pid, row.source_url)
        save_alert(self.db, pid, '담당자', 'owner@example.com', False)
        sender = Mock(side_effect=TimeoutError())
        deliver_pending(self.db, self.config, sender)
        sender.assert_not_called()
        save_alert(self.db, pid, '담당자', 'owner@example.com', True)
        self.assertEqual(deliver_pending(self.db, self.config, sender)['failed'], 1)
        deliver_pending(self.db, self.config, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(alert_status(self.db, pid)[0]['status'], 'needs_review')

    def test_no_credentials_no_send(self):
        sender = Mock()
        self.assertFalse(deliver_pending(self.db, {}, sender)['configured'])
        sender.assert_not_called()
        with self.assertRaises(ValueError):
            save_alert(self.db, 1003, '담당자', 'invalid', True)


if __name__ == '__main__':
    unittest.main()
