"""Opt-in weekly collection. No full-portfolio fallback and no implicit scheduler."""
from datetime import timedelta
from sqlalchemy import text
from storage import projects_frame
from manual_jobs import start_job

DEFAULT_PROJECTS = ('이즈미르 외곽 고속도로', '도로 민영화 (보스포러스 1·2교)')


def preferences(engine):
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(8092702)'))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS weekly_preferences (
            project_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0)'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS weekly_runs (
            week_start TEXT NOT NULL, project_id INTEGER NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(week_start, project_id))'''))
        rows = conn.execute(text('SELECT id,name,country FROM projects')).mappings().all()
        for row in rows:
            conn.execute(text('''INSERT INTO weekly_preferences (project_id,enabled) VALUES (:id,:enabled)
                ON CONFLICT (project_id) DO NOTHING'''),
                {'id':row['id'], 'enabled':int(row['country']=='튀르키예' and row['name'] in DEFAULT_PROJECTS)})
        return {row[0]:bool(row[1]) for row in conn.execute(text('SELECT project_id,enabled FROM weekly_preferences'))}


def set_preference(engine, project_id, enabled):
    preferences(engine)
    with engine.begin() as conn:
        conn.execute(text('UPDATE weekly_preferences SET enabled=:enabled WHERE project_id=:id'),
                     {'enabled':int(enabled), 'id':int(project_id)})


def run_weekly(engine, api_key, model, clock=None, email_config=None):
    from update_jobs import now_kst, update_project
    clock = clock or now_kst()
    week = (clock.date()-timedelta(days=clock.weekday())).isoformat()
    end = clock.date()-timedelta(days=1)
    selected = preferences(engine)
    targets = [p for p in projects_frame(engine).to_dict('records') if p['active'] and selected.get(p['id'])]
    with engine.connect() as conn:
        done = set(conn.execute(text('SELECT project_id FROM weekly_runs WHERE week_start=:week'),{'week':week}).scalars())
    targets = [p for p in targets if p['id'] not in done]
    if not targets:
        print('No unprocessed weekly targets. No API calls made.')
        return 0
    failures = []

    def collect(project):
        with engine.begin() as conn:
            claimed = conn.execute(text('''INSERT INTO weekly_runs VALUES (:week,:pid,'running')
                ON CONFLICT (week_start,project_id) DO NOTHING'''),{'week':week,'pid':project['id']}).rowcount
        if not claimed:
            return '이번 주 처리 완료'
        try:
            result = update_project(engine,project,api_key,model,end-timedelta(days=6),end,email_config)
        except Exception:
            failures.append(project['id'])
            result = '실패 · 수동 확인 필요'
        with engine.begin() as conn:
            conn.execute(text('UPDATE weekly_runs SET status=:status WHERE week_start=:week AND project_id=:pid'),
                         {'status':result,'week':week,'pid':project['id']})
        print(f"Project {project['id']}: {result}")
        return result

    # Same shared lock/cooldown as the UI; never interrupt a manual collection.
    if not start_job(engine, targets, collect, background=False):
        print('Another update is running or within the hourly cooldown. No API calls made.')
        return 1
    return len(failures)


if __name__ == '__main__':
    import os
    from storage import create_database
    required = ['DATABASE_URL','OPENAI_API_KEY','OPENAI_MODEL']
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise SystemExit('Missing settings: ' + ', '.join(missing))
    if not os.environ['DATABASE_URL'].startswith(('postgres://','postgresql://','postgresql+psycopg://')):
        raise SystemExit('Weekly automation requires shared PostgreSQL storage; no API calls made.')
    engine,_=create_database(os.environ['DATABASE_URL'])
    try:
        raise SystemExit(1 if run_weekly(engine,os.environ['OPENAI_API_KEY'],os.environ['OPENAI_MODEL'],email_config=os.environ) else 0)
    finally:
        engine.dispose()
