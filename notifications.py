"""Opt-in project alerts. Secrets are supplied by the host, never stored in the DB."""
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import re
import smtplib
import ssl
import uuid

from sqlalchemy import text


def valid_email(value):
    return bool(re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', value)) and parseaddr(value)[1] == value


def ensure_notifications(engine):
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(8092702)'))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS project_alerts (
            project_id INTEGER PRIMARY KEY, owner_name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS email_outbox (
            news_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
            recipient TEXT NOT NULL, message_id TEXT NOT NULL,
            created_at TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT ''
        )'''))


def get_alert(engine, project_id):
    ensure_notifications(engine)
    with engine.connect() as conn:
        row = conn.execute(text('SELECT * FROM project_alerts WHERE project_id=:id'), {'id': project_id}).mappings().first()
    return dict(row) if row else {'owner_name': '', 'email': '', 'enabled': 0}


def save_alert(engine, project_id, owner_name, email, enabled):
    email = email.strip()
    if (email or enabled) and not valid_email(email):
        raise ValueError('올바른 담당자 이메일을 입력해 주세요.')
    if enabled and not owner_name.strip():
        raise ValueError('담당자 이름을 입력해 주세요.')
    ensure_notifications(engine)
    with engine.begin() as conn:
        conn.execute(text('''INSERT INTO project_alerts (project_id, owner_name, email, enabled)
            VALUES (:id,:name,:email,:enabled) ON CONFLICT (project_id) DO UPDATE SET
            owner_name=:name, email=:email, enabled=:enabled'''),
            {'id': project_id, 'name': owner_name.strip(), 'email': email, 'enabled': int(enabled)})


def queue_alert(engine, project_id, source_url):
    ensure_notifications(engine)
    with engine.begin() as conn:
        row = conn.execute(text('''SELECT n.id, a.email FROM news_items n
            JOIN projects p ON p.id=n.project_id JOIN project_alerts a ON a.project_id=p.id
            WHERE n.project_id=:pid AND n.source_url=:url AND p.active=1 AND a.enabled=1
            AND n.severity IN ('Critical','Material')'''), {'pid': project_id, 'url': source_url}).mappings().first()
        if row and valid_email(row['email']):
            conn.execute(text('''INSERT INTO email_outbox (news_id, recipient, message_id, created_at)
                VALUES (:id,:email,:mid,:at) ON CONFLICT (news_id) DO NOTHING'''),
                {'id': row['id'], 'email': row['email'], 'mid': f'<{uuid.uuid4()}@project-alerts.local>',
                 'at': datetime.now(timezone.utc).isoformat()})


def smtp_ready(config):
    return all(config.get(k) for k in ['SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM'])


def send_message(message, config):
    mode = config.get('SMTP_SECURITY') or 'starttls'
    if mode not in ('ssl', 'starttls'):
        raise ValueError('SMTP_SECURITY must be ssl or starttls')
    port = int(config.get('SMTP_PORT') or (465 if mode == 'ssl' else 587))
    context = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if mode == 'ssl' else smtplib.SMTP
    kwargs = {'context': context} if mode == 'ssl' else {}
    with cls(config['SMTP_HOST'], port, timeout=30, **kwargs) as server:
        if mode == 'starttls':
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(config['SMTP_USERNAME'], config['SMTP_PASSWORD'])
        refused = server.send_message(message)
        if refused:
            raise RuntimeError('Recipient refused')


def deliver_pending(engine, config, sender=None):
    if not smtp_ready(config):
        return {'sent': 0, 'failed': 0, 'configured': False}
    sender = sender or send_message
    ensure_notifications(engine)
    result = {'sent': 0, 'failed': 0, 'configured': True}
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text('''SELECT o.*, n.project_id, n.title, n.summary,
            n.source_name, n.source_url, n.published_at, n.severity, n.source_quality,
            p.name AS project_name, p.country FROM email_outbox o
            JOIN news_items n ON n.id=o.news_id JOIN projects p ON p.id=n.project_id
            WHERE o.status='pending' ORDER BY o.created_at''')).mappings()]
    for row in rows:
        with engine.begin() as conn:
            # Re-check opt-in and current recipient immediately before claiming a send.
            claimed = conn.execute(text('''UPDATE email_outbox SET status='sending'
                WHERE news_id=:id AND status='pending' AND EXISTS (
                SELECT 1 FROM project_alerts a JOIN projects p ON p.id=a.project_id
                JOIN news_items n ON n.project_id=p.id WHERE n.id=:id AND p.active=1
                AND a.enabled=1 AND a.email=:email AND n.severity IN ('Critical','Material'))'''),
                {'id': row['news_id'], 'email': row['recipient']}).rowcount
        if not claimed:
            continue
        try:
            message = EmailMessage()
            label = '긴급' if row['severity'] == 'Critical' else '중요'
            message['Subject'] = f"[{label}] {row['country']} · {row['project_name']} 새 기사"
            message['From'] = config['SMTP_FROM']
            message['To'] = row['recipient']
            message['Message-ID'] = row['message_id']
            message.set_content(f"{row['project_name']} — {row['country']}\n\n{row['title']}\n\n"
                f"{row['summary']}\n\n출처: {row['source_name']} · {row['published_at']}\n"
                f"출처 등급: {row['source_quality']}\n원문: {row['source_url']}\n\n"
                '자동 수집한 기사입니다. 원문과 중요도는 담당자 확인이 필요합니다.')
            sender(message, config)
            status = 'sent'
            result['sent'] += 1
        except Exception:
            # A timeout may occur after the mail server accepted the message.
            # Never retry ambiguous sends automatically (prevents duplicate emails).
            status = 'needs_review'
            result['failed'] += 1
        with engine.begin() as conn:
            conn.execute(text('UPDATE email_outbox SET status=:status, sent_at=:at WHERE news_id=:id'),
                         {'status': status, 'at': datetime.now(timezone.utc).isoformat() if status == 'sent' else '', 'id': row['news_id']})
    return result


def alert_status(engine, project_id):
    ensure_notifications(engine)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text('''SELECT n.title, o.status, o.created_at, o.sent_at
            FROM email_outbox o JOIN news_items n ON n.id=o.news_id
            WHERE n.project_id=:id ORDER BY o.created_at DESC LIMIT 10'''), {'id': project_id}).mappings()]
