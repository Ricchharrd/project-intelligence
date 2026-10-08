"""Curated political calendar. Dates are never inferred from an election cycle."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

KST = timezone(timedelta(hours=9))


def load_events():
    return json.loads(Path(__file__).with_name('political_events.json').read_text(encoding='utf-8'))


def countdown(event, today=None):
    today = today or datetime.now(KST).date()
    if not event.get('date') or event['status'] not in ('confirmed', 'scheduled'):
        return '일정 확인 필요'
    start = date.fromisoformat(event['date'])
    end = date.fromisoformat(event.get('end_date') or event['date'])
    days = (start - today).days
    prefix = '예정 ' if event['status'] == 'scheduled' else ''
    if days > 0:
        return f'{prefix}D-{days}'
    if today <= end:
        return f'{prefix}D-DAY' if start == end else f'{prefix}행사 기간'
    return '예정일 경과 · 결과 확인 필요'


def events_for_countries(countries, today=None):
    today = today or datetime.now(KST).date()
    by_country = {e['country']: e for e in load_events()}
    result = []
    for country in sorted(set(countries)):
        event = by_country.get(country, {'country': country, 'title': '정치 일정',
            'date': '', 'status': 'unverified', 'description': '이 국가의 주요 정치 일정을 아직 등록하지 않았습니다.',
            'source_name': '', 'source_url': '', 'checked_at': '', 'note': ''})
        result.append(event | {'countdown': countdown(event, today),
            'stale': bool(event.get('checked_at')) and (today-date.fromisoformat(event['checked_at'])).days > 30})
    return sorted(result, key=lambda e: (not bool(e['date']), e['date'] or '9999', e['country']))
