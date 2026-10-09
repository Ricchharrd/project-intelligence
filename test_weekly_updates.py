import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from unittest.mock import patch
from sqlalchemy import text
from storage import create_database
from update_jobs import KST
from weekly_updates import preferences, set_preference, run_weekly


class WeeklyTests(unittest.TestCase):
    def test_only_opted_in_active_projects_once_a_week(self):
        with TemporaryDirectory() as folder:
            engine,_=create_database(f"sqlite:///{Path(folder)/'weekly.db'}")
            self.assertEqual({p for p,v in preferences(engine).items() if v},{1002,1017})
            clock=datetime(2026,10,12,6,tzinfo=KST)
            with patch('update_jobs.update_project',return_value='새 중요 기사 없음') as update:
                self.assertEqual(run_weekly(engine,'test','test',clock),0)
                self.assertEqual({c.args[1]['id'] for c in update.call_args_list},{1002,1017})
                self.assertEqual(run_weekly(engine,'test','test',clock),0)
                self.assertEqual(update.call_count,2)
                set_preference(engine,1002,False)
                self.assertFalse(preferences(engine)[1002])
                with engine.begin() as conn:
                    conn.execute(text('UPDATE manual_update_gate SET started_at=0'))
                self.assertEqual(run_weekly(engine,'test','test',datetime(2026,10,19,6,tzinfo=KST)),0)
                self.assertEqual(update.call_count,3)
                self.assertEqual(update.call_args.args[1]['id'],1017)
            engine.dispose()

    def test_inactive_and_no_targets_no_calls(self):
        with TemporaryDirectory() as folder:
            engine,_=create_database(f"sqlite:///{Path(folder)/'off.db'}")
            set_preference(engine,1002,False)
            with engine.begin() as conn:
                conn.execute(text('UPDATE projects SET active=0 WHERE id=1017'))
            with patch('update_jobs.update_project') as update:
                self.assertEqual(run_weekly(engine,'test','test'),0)
                update.assert_not_called()
            engine.dispose()

    def test_failure_not_silently_retried_in_same_week(self):
        with TemporaryDirectory() as folder:
            engine,_=create_database(f"sqlite:///{Path(folder)/'fail.db'}")
            with patch('update_jobs.update_project',side_effect=RuntimeError('test')) as update:
                self.assertEqual(run_weekly(engine,'test','test'),2)
                self.assertEqual(run_weekly(engine,'test','test'),0)
                self.assertEqual(update.call_count,2)
            engine.dispose()
