import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from streamlit.testing.v1 import AppTest
from ai_pipeline import validate_candidate
from storage import create_database, news_frame, projects_frame, save_news, save_approvals


class DeploymentTests(unittest.TestCase):
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
            for page in ['브리핑 선택', '사업·기사 관리', 'AI 업데이트', '서비스 구조']:
                app.sidebar.radio[0].set_value(page).run(timeout=30)
                self.assertFalse(app.exception, page)
            import streamlit as st
            st.cache_resource.clear()
            engine.dispose()
            mock_database.stop()


if __name__ == '__main__':
    unittest.main()
