import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from streamlit.testing.v1 import AppTest
from ai_pipeline import validate_candidate
from storage import create_database, news_frame, projects_frame, save_news, save_approvals


class DeploymentTests(unittest.TestCase):
    def test_upgrade_cached_old_database(self):
        from sqlalchemy import text
        from storage import manual_update_remaining, claim_manual_update
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'old.db'}")
            before = news_frame(engine).to_dict('records')
            # Only this temporary test database is modified to simulate the old release.
            with engine.begin() as conn:
                conn.execute(text('DROP TABLE manual_update_gate'))
            self.assertEqual(manual_update_remaining(engine, 10000), 0)
            self.assertTrue(claim_manual_update(engine, 10000))
            self.assertEqual(manual_update_remaining(engine, 10001), 3599)
            self.assertEqual(manual_update_remaining(engine, 10002), 3598)
            self.assertEqual(news_frame(engine).to_dict('records'), before)
            engine.dispose()

    def test_gate_failure_keeps_dashboard_visible(self):
        from sqlalchemy.exc import OperationalError
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'error.db'}")
            with patch('storage.create_database', return_value=(engine, True)), patch('storage.manual_update_remaining', side_effect=OperationalError('test', {}, Exception('unavailable'))):
                app = AppTest.from_file(str(Path(__file__).parent / 'streamlit_app.py'))
                app.secrets['OPENAI_API_KEY'] = 'test-only'
                app.secrets['OPENAI_MODEL'] = 'test-only'
                app.run(timeout=30)
                self.assertFalse(app.exception)
                self.assertTrue(next(b for b in app.button if b.label == '업데이트').disabled)
                self.assertTrue(any(s.value == '주요 업데이트' for s in app.subheader))
                import streamlit as st
                st.cache_resource.clear()
            engine.dispose()

    def test_storage_and_duplicate(self):
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'test.db'}")
            self.assertEqual(len(projects_frame(engine)), 17)
            row = news_frame(engine).iloc[0].to_dict()
            for key in ['id', 'project_name', 'country']:
                row.pop(key)
            with self.assertRaises(ValueError):
                save_news(engine, row)
            save_approvals(engine, {})
            engine.dispose()

    def test_evidence_and_date_validation(self):
        candidate = dict(found=True, source_url='https://example.com/news', published_at='2026-09-20', event_date='', source_type='정부·발주처 공식', source_quality='Low')
        start, end = date(2026, 9, 1), date(2026, 9, 27)
        with self.assertRaises(ValueError):
            validate_candidate(candidate.copy(), start, end, set())
        with self.assertRaises(ValueError):
            validate_candidate(candidate.copy(), start, date(2026, 9, 10), {candidate['source_url']})
        self.assertEqual(validate_candidate(candidate, start, end, {candidate['source_url']})['source_quality'], 'High')

    def test_pages_without_api(self):
        with TemporaryDirectory() as folder:
            engine, persistent = create_database(f"sqlite:///{Path(folder) / 'app.db'}")
            mock_database = patch('storage.create_database', return_value=(engine, persistent))
            mock_database.start()
            app = AppTest.from_file(str(Path(__file__).parent / 'streamlit_app.py'))
            app.secrets['DATABASE_URL'] = f"sqlite:///{Path(folder) / 'app.db'}"
            app.secrets['OPENAI_API_KEY'] = ''
            app.secrets['OPENAI_MODEL'] = ''
            app.run(timeout=30)
            self.assertFalse(app.exception)
            self.assertIn('대시보드', [b.label for b in app.sidebar.button])
            self.assertNotIn('서비스 구조', [b.label for b in app.sidebar.button])
            self.assertEqual(len(app.sidebar.radio), 0)
            self.assertTrue(any(b.label == '업데이트' for b in app.button))
            self.assertTrue(any('토큰이 소모되니' in c.value for c in app.caption))
            self.assertFalse(any('판단 맥락과 원문' in e.label for e in app.expander))
            self.assertEqual(len(app.sidebar.date_input), 0)
            stored = news_frame(engine)
            expected = stored[stored['severity'].isin(['Critical', 'Material'])]
            headlines = [s.value for s in app.subheader]
            self.assertTrue(all(title in headlines for title in expected['title']))
            self.assertFalse(any(title in headlines for title in stored[stored['severity'] == 'Watch']['title']))
            # Selecting a project shows its Watch articles as well, and no other projects.
            watched = stored[stored['severity'] == 'Watch'].iloc[0]
            app.selectbox[0].select(f"{watched['project_name']} — {watched['country']}").run()
            headlines = [s.value for s in app.subheader]
            self.assertIn(watched['title'], headlines)
            self.assertFalse(any(title in headlines for title in stored[stored['project_id'] != watched['project_id']]['title']))
            for page in ['뉴스 기사 선택', '사업·기사 관리']:
                next(b for b in app.sidebar.button if b.label == page).click().run(timeout=30)
                self.assertFalse(app.exception, page)
            import streamlit as st
            st.cache_resource.clear()
            engine.dispose()
            mock_database.stop()

    def test_daily_job_once_per_kst_date(self):
        from datetime import datetime
        from update_jobs import run_daily, KST
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'daily.db'}")
            clock = datetime(2026, 9, 28, 0, 0, tzinfo=KST)
            with patch('update_jobs.update_project', return_value='새 기사 없음') as update:
                self.assertEqual(run_daily(engine, 'unused', 'unused', clock), 0)
                self.assertEqual(update.call_count, 17)
                self.assertEqual(update.call_args.args[-1], date(2026, 9, 27))
                run_daily(engine, 'unused', 'unused', clock)
                self.assertEqual(update.call_count, 17)
            engine.dispose()

    def test_dashboard_update_and_plain_text(self):
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'ui.db'}")
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("UPDATE news_items SET summary='<p>본문 테스트</p>', published_at=:day"), {'day': date.today().isoformat()})
            with patch('storage.create_database', return_value=(engine, True)), patch('update_jobs.update_project', return_value='새 기사 없음') as updater:
                app = AppTest.from_file(str(Path(__file__).parent / 'streamlit_app.py'))
                app.secrets['OPENAI_API_KEY'] = 'test-only'
                app.secrets['OPENAI_MODEL'] = 'test-only'
                app.run(timeout=30)
                self.assertTrue(any(t.value == '본문 테스트' for t in app.text))
                self.assertFalse(any('<p>' in t.value for t in app.text))
                app.selectbox[0].select_index(1).run()
                next(b for b in app.button if b.label == '업데이트').click().run(timeout=30)
                self.assertEqual(updater.call_count, 0)
                next(b for b in app.button if b.label == '확인 후 실행').click().run(timeout=30)
                self.assertFalse(app.exception)
                self.assertEqual(updater.call_count, 1)
                self.assertTrue(next(b for b in app.button if b.label == '업데이트').disabled)
                import streamlit as st
                st.cache_resource.clear()
            engine.dispose()

    def test_shared_hourly_gate(self):
        from storage import claim_manual_update, manual_update_remaining
        from concurrent.futures import ThreadPoolExecutor
        with TemporaryDirectory() as folder:
            engine, _ = create_database(f"sqlite:///{Path(folder) / 'gate.db'}")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: claim_manual_update(engine, 10000), range(2)))
            self.assertEqual(sum(results), 1)
            self.assertFalse(claim_manual_update(engine, 13599))
            self.assertEqual(manual_update_remaining(engine, 13599), 1)
            self.assertTrue(claim_manual_update(engine, 13600))
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
