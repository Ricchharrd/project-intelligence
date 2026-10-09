"""Background collection independent of Streamlit reruns, with a shared DB lease."""
import json
import threading
import time
import uuid
from sqlalchemy import text
from storage import claim_manual_update


def job_state(engine):
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE IF NOT EXISTS manual_job (
            id INTEGER PRIMARY KEY, token TEXT NOT NULL, lease DOUBLE PRECISION NOT NULL,
            completed INTEGER NOT NULL, total INTEGER NOT NULL, results TEXT NOT NULL
        )'''))
        conn.execute(text("INSERT INTO manual_job VALUES (1, '', 0, 0, 0, '[]') ON CONFLICT (id) DO NOTHING"))
        row = dict(conn.execute(text('SELECT * FROM manual_job WHERE id=1')).mappings().one())
    row['running'] = row['lease'] > time.time()
    row['results'] = json.loads(row['results'])
    return row


def start_job(engine, targets, collect, *, override_password='', configured_password='', background=True):
    job_state(engine)
    token = uuid.uuid4().hex
    with engine.begin() as conn:
        claimed = conn.execute(text('''UPDATE manual_job SET token=:token, lease=:lease,
            completed=0, total=:total, results='[]' WHERE id=1 AND lease<=:now'''),
            dict(token=token, lease=time.time()+600, total=len(targets), now=time.time())).rowcount
    if not claimed:
        return False
    try:
        if not claim_manual_update(engine, override_password=override_password, configured_password=configured_password):
            return False
        if not background:
            _run(engine, token, targets, collect)
            return True
        worker = threading.Thread(target=_run, args=(engine, token, targets, collect), daemon=True)
        worker.start()
        return True
    finally:
        if 'worker' not in locals() or not worker.is_alive():
            _release(engine, token)


def _release(engine, token):
    with engine.begin() as conn:
        conn.execute(text('UPDATE manual_job SET lease=0 WHERE id=1 AND token=:token'), {'token': token})


def _run(engine, token, targets, collect):
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            try:
                with engine.begin() as conn:
                    conn.execute(text('UPDATE manual_job SET lease=:lease WHERE id=1 AND token=:token'),
                                 {'lease': time.time()+600, 'token': token})
            except Exception:
                stopped.set()

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    results = []
    try:
        for project in targets:
            if stopped.is_set():
                break
            # Never start another paid call after losing ownership of the job.
            state = job_state(engine)
            if state['token'] != token or not state['running']:
                break
            try:
                result = collect(project)
            except Exception:
                result = '업데이트하지 못했습니다. 연결 및 사용 한도를 확인해 주세요.'
            results.append(f"{project['name']}: {result}")
            with engine.begin() as conn:
                conn.execute(text('''UPDATE manual_job SET completed=:done, results=:results
                    WHERE id=1 AND token=:token'''),
                    {'done':len(results), 'results':json.dumps(results, ensure_ascii=False), 'token':token})
    finally:
        stopped.set()
        _release(engine, token)
