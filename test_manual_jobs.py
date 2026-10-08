import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from storage import create_database, manual_update_remaining
from manual_jobs import start_job, job_state


class ManualJobTests(unittest.TestCase):
    def test_running_blocks_even_password_and_keeps_original_cooldown(self):
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder)/'jobs.db'}")
            entered, finish = threading.Event(), threading.Event()
            calls = []
            def collect(project):
                calls.append(project)
                entered.set()
                finish.wait(5)
                return '완료'
            try:
                self.assertTrue(start_job(engine, [{'name':'사업'}], collect))
                self.assertTrue(entered.wait(2))
                before = manual_update_remaining(engine)
                self.assertTrue(job_state(engine)['running'])
                self.assertFalse(start_job(engine, [{'name':'중복'}], collect,
                    override_password='test', configured_password='test'))
                self.assertLessEqual(manual_update_remaining(engine), before)
                self.assertEqual(len(calls), 1)
            finally:
                finish.set()
                for _ in range(100):
                    if not job_state(engine)['running']:
                        break
                    time.sleep(.02)
            self.assertEqual(job_state(engine)['results'], ['사업: 완료'])
            self.assertFalse(start_job(engine, [{'name':'차단'}], collect))
            self.assertTrue(start_job(engine, [{'name':'예외'}], collect,
                override_password='test', configured_password='test'))
            for _ in range(100):
                if not job_state(engine)['running']:
                    break
                time.sleep(.02)
            self.assertEqual(job_state(engine)['results'], ['예외: 완료'])
            engine.dispose()

    def test_worker_failure_releases_lock(self):
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder)/'failure.db'}")
            def fail(project):
                raise RuntimeError('do not display internal details')
            self.assertTrue(start_job(engine, [{'name':'사업'}], fail))
            for _ in range(100):
                if not job_state(engine)['running']:
                    break
                time.sleep(.02)
            self.assertFalse(job_state(engine)['running'])
            self.assertNotIn('internal', str(job_state(engine)['results']))
            engine.dispose()
