import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import json
from sqlalchemy import text
from streamlit.testing.v1 import AppTest
from storage import create_database, projects_frame, news_frame
from project_scope import migrate_moravia, MORAVIA_NAME, visible_news, validate_scope, scope_instructions
from political_events import countdown, events_for_countries, load_events


class ContextTests(unittest.TestCase):
    def test_countdown(self):
        e = {'date': '2026-11-07', 'status': 'confirmed'}
        self.assertEqual(countdown(e, date(2026,10,9)), 'D-29')
        self.assertEqual(countdown(e, date(2026,11,7)), 'D-DAY')
        self.assertIn('경과', countdown(e, date(2026,11,8)))
        self.assertEqual(countdown(e | {'status':'unverified'}, date(2026,10,9)), '일정 확인 필요')
        self.assertEqual(countdown(e | {'status':'scheduled'}, date(2026,10,9)), '예정 D-29')
        self.assertEqual(countdown({'date':'2026-10-09','end_date':'2026-10-10','status':'confirmed'},date(2026,10,10)), '행사 기간')

    def test_country_coverage_and_staleness(self):
        from seed_data import PROJECTS
        countries = {p[2] for p in PROJECTS}
        self.assertEqual({e['country'] for e in load_events()}, countries)
        events = events_for_countries(['뉴질랜드','뉴질랜드','체코'],date(2026,10,9))
        self.assertEqual(len(events),2)
        self.assertTrue(all(e['stale'] for e in events_for_countries(['체코'],date(2026,12,1))))
        self.assertEqual(events_for_countries(['신규 국가'])[0]['countdown'],'일정 확인 필요')

    def test_scope_guard(self):
        good = {'found':True,'scope_match':True,'scope_evidence':'VRT Moravská brána 토지 매입 공고','title':'Moravia Gate 토지 매입'}
        self.assertTrue(validate_scope(good, MORAVIA_NAME, '체코')['found'])
        self.assertFalse(validate_scope(good | {'scope_match':False}, MORAVIA_NAME, '체코')['found'])
        self.assertFalse(validate_scope(good | {'scope_evidence':'체코 철도 전체 예산'}, MORAVIA_NAME, '체코')['found'])
        self.assertIn('다른 구간',scope_instructions('High-Speed Rail (고속철도)','체코'))
        self.assertEqual(scope_instructions('다른 철도','캐나다'),'')

    def test_collector_requests_and_rejects_broad_czech_news(self):
        from ai_pipeline import collect_update
        candidate = {'found': True, 'scope_match': False, 'scope_evidence':'',
                     'title':'체코 철도 예산', 'source_url':'https://example.org/news',
                     'published_at':'2026-10-01', 'event_date':'',
                     'source_type':'정부·발주처 공식'}
        response = SimpleNamespace(status='completed', output_text=json.dumps({'articles':[candidate], 'reason':''}),
            model_dump=lambda: {'output':[{'type':'web_search_call', 'action':{'sources':[{'url':candidate['source_url']}]}}]})
        with patch('ai_pipeline.OpenAI') as client:
            client.return_value.responses.create.return_value=response
            result=collect_update(api_key='test-only',model='test-only',project_name=MORAVIA_NAME,
                                  country='체코',start_date=date(2026,10,1),end_date=date(2026,10,1))
            self.assertFalse(result['found'])
            request=client.return_value.responses.create.call_args.kwargs
            self.assertIn('다른 구간',request['instructions'])
            self.assertEqual(request['tools'],[{'type':'web_search'}])

    def test_migration_and_historical_preservation(self):
        with TemporaryDirectory() as d:
            engine,_=create_database(f'sqlite:///{Path(d)/"db.sqlite"}')
            with engine.begin() as c:
                c.execute(text("UPDATE projects SET name='High-Speed Rail (고속철도)',aliases='사용자 별칭',active=0 WHERE id=1003"))
                c.execute(text("UPDATE news_items SET project_id=1003 WHERE id=2101"))
            before=len(news_frame(engine))
            migrate_moravia(engine); migrate_moravia(engine)
            p=projects_frame(engine).set_index('id').loc[1003]
            self.assertEqual(p['name'],MORAVIA_NAME)
            self.assertEqual(p['active'],0)
            self.assertEqual(p['aliases'].count('사용자 별칭'),1)
            self.assertEqual(p['aliases'].count('VRT Moravská brána'),1)
            self.assertEqual(len(news_frame(engine)),before)
            self.assertNotIn(2101,visible_news(news_frame(engine))['id'].tolist())
            engine.dispose()

    def test_dashboard_sources_and_calendar(self):
        import streamlit as st
        with TemporaryDirectory() as d:
            engine,_=create_database(f'sqlite:///{Path(d)/"ui.sqlite"}')
            with patch('storage.create_database',return_value=(engine,True)):
                st.cache_resource.clear()
                app=AppTest.from_file(str(Path(__file__).with_name('streamlit_app.py')))
                app.secrets['OPENAI_API_KEY']=''
                app.secrets['OPENAI_MODEL']=''
                app.run(timeout=30)
                self.assertFalse(app.exception)
                self.assertTrue(any(t.value.startswith('출처: ') for t in app.text))
                self.assertTrue(any(h.value=='국가별 정치 일정' for h in app.subheader))
                app.session_state['project_filter']=[1012,1013]
                app.run(timeout=30)
                self.assertFalse(app.exception)
                self.assertEqual(sum(m.value.startswith('**뉴질랜드 · 총선') for m in app.markdown),1)
                self.assertFalse(any('체코 · 지방의회' in m.value for m in app.markdown))
                st.cache_resource.clear()
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
