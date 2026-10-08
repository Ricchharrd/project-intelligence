import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from ai_pipeline import select_articles, BATCH_SCHEMA, IMPORTANCE_RULES
from storage import create_database, news_frame
from update_jobs import update_project


def article(n, **changes):
    return dict(found=True, title=f'사업 변화 {n}', summary='원문 요약', source_name='발주처',
        source_url=f'https://example.org/{n}', published_at='2026-10-09', event_date='',
        topic='입찰·계약', source_type='정부·발주처 공식', source_quality='High', severity='Material',
        context='', reason='', scope_match=True, scope_evidence='사업 직접 관련',
        impact_level='main_contract', impact_evidence='본사업 계약 체결', event_key=f'event-{n}') | changes


class NewsSelectionTests(unittest.TestCase):
    def select(self, items, existing=()):
        return select_articles(items, date(2026,10,3), date(2026,10,9),
            {i['source_url'] for i in items if i.get('source_url')}, '사업','국가',existing)

    def test_three_material_events_and_delay_before_newer_minor_work(self):
        items = [article(0, title='대체 연못 조성 입찰', impact_level='routine'),
                 article(1), article(2), article(3),
                 article(4, title='토지수용으로 착공 지연', published_at='2026-10-03',
                         severity='Critical', impact_level='project', impact_evidence='착공이 연기됨')]
        selected = self.select(items)
        self.assertEqual(len(selected),3)
        self.assertEqual(selected[0]['title'],'토지수용으로 착공 지연')
        self.assertNotIn('대체 연못 조성 입찰',[a['title'] for a in selected])

    def test_low_relevance_or_low_importance_cannot_fill_quota(self):
        for changes in [dict(severity='Watch'),dict(impact_level='routine'),
                        dict(impact_evidence=''),dict(scope_match=False)]:
            self.assertEqual(self.select([article(1,**changes)]),[])

    def test_duplicates_dates_and_invalid_item(self):
        items = [article(1), article(2,event_key='event-1'), article(3),
                 article(4,published_at='2026-09-01'),article(5,published_at='not-a-date'),
                 article(6,scope_match=False)]
        selected = self.select(items, ['https://example.org/3'])
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['source_url'],'https://example.org/1')

    def test_multi_save_and_existing_candidates_sent_to_search(self):
        with TemporaryDirectory() as folder:
            engine,_=create_database(f"sqlite:///{Path(folder)/'news.db'}")
            before=len(news_frame(engine))
            batch={'found':True,'articles':[article(1),article(2),article(3)]}
            with patch('update_jobs.collect_update',return_value=batch) as collect:
                result=update_project(engine,{'id':1001,'name':'사업','country':'베트남'},'test','test',date(2026,10,3),date(2026,10,9))
                self.assertEqual(result,'새 중요 기사 3건 저장')
                self.assertEqual(len(news_frame(engine)),before+3)
                result=update_project(engine,{'id':1001,'name':'사업','country':'베트남'},'test','test',date(2026,10,3),date(2026,10,9))
                self.assertIn('기존 기사 제외',result)
                self.assertEqual(len(news_frame(engine)),before+3)
                self.assertEqual(len(collect.call_args.kwargs['existing_articles']),3)
            engine.dispose()

    def test_contract_and_policy(self):
        self.assertEqual(BATCH_SCHEMA['properties']['articles']['type'],'array')
        self.assertIn('토지',IMPORTANCE_RULES)
        self.assertIn('사소한 기사로 수를 채우지',IMPORTANCE_RULES)
