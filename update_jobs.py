"""Shared manual and scheduled collection; new articles never auto-approve."""
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import text
from ai_pipeline import collect_update
from storage import create_database, projects_frame, news_frame, save_news
from notifications import queue_alert, deliver_pending

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def update_project(engine, project, api_key, model, start, end, email_config=None):
    existing = news_frame(engine)
    existing = existing[existing['project_id'] == int(project['id'])].sort_values('published_at', ascending=False)
    batch = collect_update(api_key=api_key, model=model, project_name=project['name'],
        country=project['country'], aliases=project.get('aliases', ''), start_date=start, end_date=end,
        existing_articles=existing[['title', 'source_url']].head(50).to_dict('records'))
    if not batch.get('found'):
        return '새 중요 기사 없음'
    fields = ['title', 'summary', 'source_name', 'source_url', 'published_at',
              'event_date', 'topic', 'source_type', 'source_quality', 'severity', 'context']
    saved, mail_failed = 0, False
    for candidate in batch['articles'][:3]:
        values = {key: candidate[key] for key in fields}
        values.update(project_id=int(project['id']), verified_at='', approved=0)
        try:
            save_news(engine, values)
        except ValueError as exc:
            if '이미 등록' in str(exc):
                continue
            raise
        saved += 1
        try:
            queue_alert(engine, int(project['id']), values['source_url'])
        except Exception:
            mail_failed = True
    if not saved:
        return '새 중요 기사 없음 · 기존 기사 제외'
    try:
        if email_config:
            delivery = deliver_pending(engine, email_config)
            if delivery['failed']:
                mail_failed = True
    except Exception:
        mail_failed = True
    return f'새 중요 기사 {saved}건 저장' + (' · 이메일 처리 확인 필요' if mail_failed else '')


def run_daily(engine, api_key, model, clock=None, email_config=None):
    clock = clock or now_kst()
    end = clock.date() - timedelta(days=1)
    start = end - timedelta(days=6)
    failures = 0
    for project in projects_frame(engine).to_dict('records'):
        if not project['active']:
            continue
        # Daily claim is durable, so restarting a job cannot repeat paid calls.
        with engine.begin() as conn:
            claimed = conn.execute(text('''INSERT INTO daily_runs (run_date, project_id, status)
                VALUES (:day,:pid,'running') ON CONFLICT (run_date,project_id) DO NOTHING'''),
                {'day': clock.date().isoformat(), 'pid': int(project['id'])}).rowcount
        if not claimed:
            continue
        try:
            status = update_project(engine, project, api_key, model, start, end, email_config=email_config)
        except Exception:
            status = '업데이트 실패'
            failures += 1
        with engine.begin() as conn:
            conn.execute(text('''UPDATE daily_runs SET status=:status, finished_at=:finished
                WHERE run_date=:day AND project_id=:pid'''),
                {'status': status, 'finished': now_kst().isoformat(),
                 'day': clock.date().isoformat(), 'pid': int(project['id'])})
        print(f"Project {project['id']}: {status}")
    return failures


if __name__ == '__main__':
    required = ['DATABASE_URL', 'OPENAI_API_KEY', 'OPENAI_MODEL']
    if any(not os.environ.get(key) for key in required):
        raise SystemExit('Missing DATABASE_URL / OPENAI_API_KEY / OPENAI_MODEL')
    if not os.environ['DATABASE_URL'].startswith(('postgres://', 'postgresql://', 'postgresql+psycopg://')):
        raise SystemExit('Scheduled updates require shared PostgreSQL storage')
    db, _ = create_database(os.environ['DATABASE_URL'])
    try:
        failures = run_daily(db, os.environ['OPENAI_API_KEY'], os.environ['OPENAI_MODEL'], email_config=os.environ)
        delivery = deliver_pending(db, os.environ)
        raise SystemExit(1 if failures or delivery['failed'] else 0)
    finally:
        db.dispose()
