from __future__ import annotations

from datetime import date, timedelta
import os
from html import unescape, escape
from html.parser import HTMLParser
from update_jobs import now_kst, update_project
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from openai import AuthenticationError, RateLimitError, APIConnectionError, APIStatusError
from sqlalchemy.exc import SQLAlchemyError

from ai_pipeline import collect_update
from storage import create_database, news_frame, projects_frame, save_approvals, save_news, save_project
from storage import manual_update_remaining, claim_manual_update
from notifications import get_alert, save_alert, smtp_ready, alert_status
from political_events import events_for_countries
from project_scope import visible_news, is_moravia_project
from manual_jobs import job_state, start_job
from weekly_updates import preferences as weekly_preferences, set_preference


st.set_page_config(page_title="CEO Project Intelligence", page_icon="📡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background:#f7f8fa; color:#202d3a; }
    [data-testid="stMainBlockContainer"] {
        max-width:1120px; padding-top:2.5rem; padding-bottom:3rem;
    }
    [data-testid="stSidebar"] {
        background:#eef1f5; border-right:1px solid #dde3eb;
    }
    [data-testid="stSidebar"] h2 {
        font-size:1.05rem; letter-spacing:-.03em; margin-bottom:1.6rem;
    }
    [data-testid="stSidebar"] button {
        justify-content:flex-start; padding:.75rem 1rem; border-radius:8px;
        box-shadow:none; min-height:46px;
    }
    [data-testid="stSidebar"] button[kind="secondary"] {
        border:1px solid transparent; background:transparent; color:#526174;
    }
    [data-testid="stSidebar"] button[kind="secondary"]:hover {
        background:#e2e8f0; color:#23374e;
    }
    [data-testid="stSidebar"] button[kind="primary"] {
        background:#dce6f2; color:#1b3d65; border:1px solid #cbd9e9;
    }
    h1 { font-size:2rem !important; letter-spacing:-.04em; }
    h2 { font-size:1.3rem !important; letter-spacing:-.025em; }
    h3 { font-size:1.1rem !important; line-height:1.6 !important; }
    [data-testid="stText"] { font-family:inherit; line-height:1.75; color:#384858; }
    [data-testid="stCaptionContainer"] { color:#667588; }
    [data-testid="stVerticalBlockBorderWrapper"] > div {
        border-color:#e0e5ec !important; border-radius:12px !important;
        background:#fff;
    }
    [data-testid="stSelectbox"] [data-baseweb="select"] > div {
        background:#fff; border-color:#d8e0e9; border-radius:8px;
    }
    [data-testid="stButton"] button { min-height:40px; border-radius:8px; }
    [data-testid="stLinkButton"] a { border-radius:7px; font-size:.85rem; }
    .project-identity { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
        padding:4px 0 12px; margin-bottom:8px; border-bottom:1px solid #e3e9f0; }
    .project-country { background:#dce6f2; color:#163e67; border-radius:6px;
        padding:5px 10px; font-weight:700; font-size:.9rem; }
    .project-name { color:#172f49; font-size:1.14rem; font-weight:750; }
    @media (max-width:640px) {
        [data-testid="stMainBlockContainer"] { padding:1.3rem 1rem; }
    }
</style>
""", unsafe_allow_html=True)


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.environ.get(name, default)))
    except FileNotFoundError:
        return os.environ.get(name, default)


def email_settings():
    return {key: secret(key) for key in ['SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURITY',
            'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM']} | {'SMTP_SECURITY': secret('SMTP_SECURITY', 'starttls')}


@st.cache_resource
def database(schema_version=3):
    if secret("REQUIRE_DATABASE", "false").lower() == "true" and not secret("DATABASE_URL"):
        raise ValueError("DATABASE_URL required")
    return create_database(secret("DATABASE_URL") or None)


try:
    engine, persistent = database(schema_version=3)
except Exception:
    st.error("데이터베이스에 연결할 수 없습니다.")
    st.info("Streamlit 설정의 Secrets에서 DATABASE_URL을 확인하고 데이터베이스가 실행 중인지 확인해 주세요.")
    st.stop()

def navigate(target):
    st.session_state["page"] = target
    st.session_state.pop("confirm_update", None)


page = st.session_state.get("page", "대시보드")
with st.sidebar:
    st.markdown("## 해외 사업 현황")
    for label in ["대시보드", "정치 대시보드", "뉴스 기사 선택", "사업·기사 관리"]:
        st.button(label, key=f"nav_{label}", width="stretch",
                  type="primary" if page == label else "secondary",
                  on_click=navigate, args=(label,))


projects = projects_frame(engine)
news = news_frame(engine)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean(value):
    parser = PlainText()
    parser.feed(unescape(str(value or "")))
    return "".join(parser.parts).strip()


def header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def render_news_card(row) -> None:
    st.subheader(clean(row["title"]))
    st.text(clean(row["summary"]))
    st.caption(" · ".join(clean(row[k]) for k in ["country", "topic"]))
    url = str(row.get('source_url') or '')
    parsed = urlparse(url)
    st.text(f"출처: {clean(row.get('source_name')) or '미등록'}")
    st.caption(f"발행일: {row['published_at']} · 출처 유형: {clean(row.get('source_type'))}")
    if parsed.scheme in ('http', 'https') and parsed.hostname:
        st.caption(f"원문 사이트: {parsed.hostname}")
        st.link_button("기사 원문 보기", url)
    else:
        st.caption('원문 링크 미등록')
    if clean(row.get("context", "")):
        st.text(clean(row["context"]))
    quality = {"High": "높음", "Medium": "보통", "Low": "낮음"}.get(row["source_quality"], row["source_quality"])
    severity = {"Critical": "긴급", "Material": "중요", "Watch": "참고"}.get(row["severity"], row["severity"])
    review = "확인 전" if not row.get("verified_at") else "확인됨"
    st.caption(f"{row['published_at']} · 출처 신뢰도 {quality} · {severity} · {review}")


@st.fragment(run_every=60)
def render_political_calendar(countries):
    st.subheader('국가별 정치 일정')
    st.caption('선택한 사업의 국가 기준 · D-day는 한국시간 날짜 기준 · 공식 일정 변경은 별도 확인이 필요합니다.')
    events = events_for_countries(countries)
    dated = [event for event in events if event['date']]
    for event in dated[:3]:
        st.write(f"{event['country']} · {event['title']} · {event['countdown']}")
    if not dated:
        st.caption('선택한 국가의 정확한 정치 일정은 확인이 필요합니다. 아래에서 이벤트 설명과 출처를 볼 수 있습니다.')
    with st.expander('선거 일정과 설명', expanded=len(countries) <= 2):
        for event in events:
            with st.container(border=True):
                st.markdown(f"**{event['country']} · {event['title']} · {event['countdown']}**")
                if event['date']:
                    end = event.get('end_date')
                    st.caption('일정: ' + event['date'] + (f' ~ {end}' if end else '')
                               + (' · 법정 예정일' if event['status'] == 'scheduled' else ' · 공식 발표'))
                st.text(event['description'])
                st.caption(event['note'])
                if event['source_url']:
                    st.link_button(f"출처: {event['source_name']}", event['source_url'])
                st.caption('자료 확인일: ' + (event['checked_at'] or '확인 필요'))
                if event['stale']:
                    st.warning('자료 확인 후 30일이 지났습니다. 일정 변경 여부를 다시 확인해 주세요.')


def clear_project_filter():
    st.session_state["project_filter"] = []
    st.session_state.pop("confirm_update", None)


if page == "대시보드":
    header("대시보드", "주요 업데이트를 확인하거나 사업을 선택하세요.")
    active = projects[projects["active"] == 1]
    labels = {int(r['id']): f"{r['country']} · {r['name']}" for _, r in active.iterrows()}
    if "project_filter" in st.session_state:
        valid = [pid for pid in st.session_state["project_filter"] if pid in labels]
        if valid != st.session_state["project_filter"]:
            st.session_state["project_filter"] = valid
    selected_ids = st.pills("사업 선택", list(labels), selection_mode="multi",
                            format_func=lambda pid: labels[pid], key="project_filter")
    selected_ids = selected_ids or []
    selected = " / ".join(labels[pid] for pid in selected_ids) if selected_ids else "전체 사업"
    st.caption("여러 사업을 함께 선택할 수 있습니다. 선택한 버튼을 다시 누르면 해제됩니다.")
    calendar_projects = active[active['id'].isin(selected_ids)]
    if selected_ids:
        render_political_calendar(tuple(sorted(calendar_projects['country'].unique())))
    if any(is_moravia_project(r['name'], r['country']) for r in calendar_projects.to_dict('records')):
        st.caption('체코 검색 범위: Moravia Gate (VRT Moravská brána). 기존 전국 단위 기사는 관리 페이지에 보존됩니다.')
    with st.container(width=420):
        left, right = st.columns([1.5, 1], vertical_alignment="bottom", gap="small")
    left.button("전체 주요 업데이트", on_click=clear_project_filter, width="stretch")
    api_key, model = secret("OPENAI_API_KEY"), secret("OPENAI_MODEL")
    st.caption("토큰이 소모되니 필요할 때만 업데이트 하십시오")
    st.caption("최근 7일 · 사업별 중요 변화 최대 3건 · 일정·토지수용·본계약·재원 우선, 단순 부대공사 제외")
    gate_available = True
    try:
        remaining = manual_update_remaining(engine)
        running = job_state(engine)['running']
    except SQLAlchemyError:
        gate_available, remaining = False, 0
        running = False
        st.warning("업데이트 가능 시간을 확인하지 못했습니다. 기사 조회는 계속 사용할 수 있습니다. 잠시 후 다시 시도해 주세요.")
    override_secret = secret("UPDATE_OVERRIDE_PASSWORD")
    update = right.button("업데이트 중" if running else "업데이트", type="primary", width="stretch", disabled=running or not(api_key and model) or not gate_available or (remaining > 0 and not override_secret) or active.empty)
    if running:
        st.info("업데이트가 진행 중입니다. 완료될 때까지 다시 실행할 수 없습니다. 화면을 이동해도 작업은 계속됩니다.")
    @st.fragment(run_every=3)
    def show_job_progress():
        state = job_state(engine)
        if state['running']:
            st.progress(state['completed'] / max(1, state['total']), text=f"{state['completed']}/{state['total']}개 사업 확인 중")
        elif running:
            st.rerun()
        elif state['results']:
            with st.expander('최근 업데이트 결과'):
                for result in state['results']:
                    st.text(result)
    if gate_available:
        show_job_progress()
    if remaining:
        st.caption(f"다른 사용자의 실행을 포함해 업데이트는 1시간에 한 번 가능합니다. 약 {(remaining + 59) // 60}분 후 다시 실행할 수 있습니다.")
        if st.button("실행 가능 시간 확인"):
            st.rerun()
    if not api_key or not model:
        st.caption("업데이트 연결을 준비 중입니다.")
    if update:
        st.session_state["confirm_update"] = selected
    if st.session_state.get("confirm_update") != selected or not gate_available or running:
        st.session_state.pop("confirm_update", None)
    confirmed = False
    if st.session_state.get("confirm_update"):
        with st.container(border=True):
            st.write(f"{selected} 업데이트를 실행하시겠습니까?")
            st.caption("토큰이 소모되니 필요할 때만 업데이트 하십시오")
            st.caption("최근 7일의 기사를 검색합니다. 실행 후에는 모든 사용자의 업데이트가 1시간 동안 제한됩니다.")
            if remaining:
                st.info("관리자 비밀번호를 입력하면 이번 실행에 한해 1시간 제한을 건너뜁니다. 실행 후 제한 시간이 다시 시작됩니다.")
                st.text_input("관리자 비밀번호", type="password", key="update_override_input")
            yes, no = st.columns(2)
            confirmed = yes.button("확인 후 실행", type="primary")
            if no.button("취소"):
                st.session_state.pop("confirm_update", None)
                st.session_state.pop("update_override_input", None)
                st.rerun()
    if confirmed:
        st.session_state.pop("confirm_update", None)
        override_input = st.session_state.pop("update_override_input", "")
        targets = active if not selected_ids else active[active["id"].isin(selected_ids)]
        today = now_kst().date()
        settings = email_settings()
        # Capture immutable arguments; the worker must not call Streamlit APIs.
        def collect(project, db=engine, key=api_key, selected_model=model, end=today, mail=settings):
            return update_project(db, project, key, selected_model, end - timedelta(days=6), end, email_config=mail)
        try:
            claimed = start_job(engine, targets.to_dict('records'), collect,
                                override_password=override_input, configured_password=override_secret)
        except SQLAlchemyError:
            st.session_state["update_results"] = ["업데이트 가능 시간을 확인하지 못해 실행하지 않았습니다. 잠시 후 다시 시도해 주세요."]
            st.rerun()
        if not claimed:
            st.session_state["update_results"] = ["업데이트 제한 중입니다. 관리자 비밀번호를 확인하거나 1시간 후 다시 시도해 주세요."]
            st.rerun()
        st.rerun()
    if st.session_state.get("update_results"):
        for result in st.session_state.pop("update_results"):
            st.text(result)
    quality_filter = st.selectbox("출처 신뢰도", ["전체", "높음", "보통", "낮음"], width=220)
    quality_value = {"높음": "High", "보통": "Medium", "낮음": "Low"}.get(quality_filter)
    articles = visible_news(news[news["project_id"].isin(active["id"])])
    if not selected_ids:
        articles = articles[articles["severity"].isin(["Critical", "Material"])]
        st.subheader("주요 업데이트")
        st.caption("전체 사업의 중요·긴급 소식 · 최신순")
    else:
        articles = articles[articles["project_id"].isin(selected_ids)]
        st.subheader(f"선택한 사업 소식 · {len(selected_ids)}개 사업")
        st.caption("선택한 사업의 전체 소식 · 최신순")
    if quality_value:
        articles = articles[articles["source_quality"] == quality_value]
    if articles.empty:
        st.info("등록된 주요 업데이트가 없습니다." if not selected_ids else "선택한 사업에 등록된 기사가 없습니다.")
    for _, item in articles.iterrows():
        with st.container(border=True):
            st.markdown('<div class="project-identity"><span class="project-country">'
                        + escape(clean(item['country'])) + '</span><span class="project-name">'
                        + escape(clean(item['project_name'])) + '</span></div>', unsafe_allow_html=True)
            render_news_card(item)

elif page == "정치 대시보드":
    header("정치 대시보드", "국가별 주요 정치 일정과 출처를 확인합니다.")
    st.caption("현재는 등록된 선거 일정입니다. 정치 뉴스 실시간 수집 기능은 아직 연결되지 않았습니다.")
    country_options = sorted(projects[projects['active']==1]['country'].unique())
    countries = st.pills('국가 선택',country_options,selection_mode='multi',key='politics_countries')
    render_political_calendar(tuple(countries or country_options))

elif page == "뉴스 기사 선택":
    header("뉴스 기사 선택", "기사를 선택 후 CSV 형태로 다운로드 받습니다")
    edit = visible_news(news)[["id","approved","project_name","title","published_at","source_name","source_url","source_quality","severity"]].copy()
    edit["approved"] = edit["approved"].astype(bool)
    edited = st.data_editor(edit, hide_index=True, width="stretch", disabled=["id","project_name","title","published_at","source_name","source_url","source_quality","severity"], column_config={
        "id": None, "approved": st.column_config.CheckboxColumn("선택"),
        "project_name": "사업명", "title": "기사 제목", "published_at": "발행일",
        "source_quality": "출처 신뢰도", "severity": "중요도",
        "source_name": "출처", "source_url": st.column_config.LinkColumn('원문 링크'),
    })
    if st.button("선택 상태 저장", type="primary"):
        save_approvals(engine, {int(row.id): bool(row.approved) for row in edited.itertuples()})
        st.success("기사 선택을 저장했습니다.")
        st.rerun()
    chosen = edited[edited["approved"]]
    if not chosen.empty:
        export = news[news["id"].isin(chosen["id"])][["project_name","country","published_at","topic","severity","source_quality","title","summary","source_name","source_url","context"]]
        st.download_button("선택 뉴스 CSV 다운로드", export.to_csv(index=False).encode("utf-8-sig"), f"News_Articles_{now_kst().date()}.csv", "text/csv")

elif page == "사업·기사 관리":
    header("사업·기사 관리", "웹에서 관심 사업과 공개 뉴스 항목을 추가하거나 수정합니다.")
    project_tab, news_tab = st.tabs(["관심 사업", "기사 등록·수정"])
    with project_tab:
        display_projects = projects[["name","country","active","news_count"]].copy()
        display_projects["active"] = display_projects["active"].map({1: "관리 중", 0: "관리 중지"})
        display_projects.columns = ["사업명", "국가", "관리 상태", "기사 수"]
        st.dataframe(display_projects, hide_index=True, width="stretch")
        project_labels = {int(r['id']): f"{r['name']} — {r['country']}" for _, r in projects.iterrows()}
        choice = st.selectbox("편집 대상", [None, *project_labels], format_func=lambda key: "새 사업 추가" if key is None else project_labels[key])
        current = None if choice is None else projects[projects["id"] == choice].iloc[0]
        with st.form("project_form"):
            name = st.text_input("사업명", "" if current is None else current["name"])
            country = st.text_input("국가", "" if current is None else current["country"])
            aliases = st.text_input("검색 별칭", "" if current is None else current["aliases"])
            active = st.checkbox("관리 대상에 포함", True if current is None else bool(current["active"]))
            if st.form_submit_button("사업 저장", type="primary"):
                if not name.strip() or not country.strip(): st.error("사업명과 국가를 입력해 주세요.")
                else:
                    save_project(engine, {"name":name.strip(),"country":country.strip(),"aliases":aliases.strip(),"active":1 if active else 0}, None if current is None else int(current["id"]))
                    st.success("사업을 저장했습니다."); st.rerun()
        if current is not None:
            st.subheader("주간 자동 업데이트")
            st.caption("매주 월요일 오전 6시(KST) · 선택한 주요 사업만 최근 7일 검색 · 사업별 최대 3건")
            if not secret('DATABASE_URL').startswith(('postgres://','postgresql://','postgresql+psycopg://')):
                st.warning("자동 실행 연결 전입니다. 공용 데이터베이스와 GitHub 예약 작업 설정이 필요합니다. 대상 선택은 저장할 수 있습니다.")
            else:
                st.caption("실제 예약 실행 상태와 실패 내역은 GitHub Actions에서 확인하세요.")
            with st.form('weekly_preferences'):
                weekly_enabled = st.checkbox('이 사업을 주간 자동 업데이트에 포함',value=weekly_preferences(engine).get(int(current['id']),False))
                if st.form_submit_button('주간 설정 저장'):
                    set_preference(engine,int(current['id']),weekly_enabled)
                    st.success('주간 대상 설정을 저장했습니다.')
            st.subheader("담당자 이메일 알림")
            st.caption("알림을 켠 사업의 중요·긴급 새 기사만 담당자에게 보냅니다. 기존 기사는 다시 보내지 않습니다.")
            if not smtp_ready(email_settings()):
                st.info("발신 메일 연결 전입니다. 담당자 설정은 저장할 수 있지만 이메일은 아직 발송되지 않습니다.")
            try:
                preference = get_alert(engine, int(current['id']))
                with st.form("project_alert_form"):
                    owner = st.text_input("담당자 이름", preference['owner_name'])
                    email = st.text_input("담당자 이메일", preference['email'])
                    enabled = st.checkbox("이 사업 이메일 알림 받기", bool(preference['enabled']))
                    if st.form_submit_button("알림 설정 저장"):
                        save_alert(engine, int(current['id']), owner, email, enabled)
                        st.success("알림 설정을 저장했습니다.")
                records = alert_status(engine, int(current['id']))
                if records:
                    status_names = {'pending': '발송 대기', 'sending': '발송 중 또는 확인 필요',
                                    'sent': '메일 서버 전달 완료', 'needs_review': '발송 확인 필요'}
                    st.caption("최근 이메일 처리 내역")
                    for record in records:
                        st.text(f"{status_names.get(record['status'], record['status'])} · {record['title']}")
            except ValueError as exc:
                st.error(str(exc))
            except SQLAlchemyError:
                st.warning("알림 설정을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        else:
            st.caption("사업을 저장한 뒤 편집 대상으로 선택하면 담당자와 이메일 알림을 설정할 수 있습니다.")
    with news_tab:
        news_labels = {int(r['id']): f"{r['title']} · {r['project_name']} · {r['published_at']}" for _, r in news.iterrows()}
        news_choice = st.selectbox("기사 선택", [None, *news_labels], format_func=lambda key: "새 기사 등록" if key is None else news_labels[key])
        current_news = None if news_choice is None else news[news["id"] == news_choice].iloc[0]
        project_index = 0 if current_news is None else projects[projects["id"] == int(current_news["project_id"])].index[0]
        with st.form("news_form"):
            selected_idx = st.selectbox("사업", range(len(projects)), index=int(project_index), format_func=lambda i: f"{projects.iloc[i]['country']} · {projects.iloc[i]['name']}")
            title = st.text_input("기사 제목", "" if current_news is None else current_news["title"])
            summary = st.text_area("기사 요약", "" if current_news is None else current_news["summary"])
            source_name = st.text_input("출처명", "" if current_news is None else current_news["source_name"])
            source_url = st.text_input("공개 원문 URL", "" if current_news is None else current_news["source_url"])
            d1, d2 = st.columns(2)
            published = d1.date_input("발행일", now_kst().date() if current_news is None else date.fromisoformat(str(current_news["published_at"])))
            event = d2.date_input("사건일", published if current_news is None else date.fromisoformat(str(current_news["event_date"] or current_news["published_at"])))
            t1, t2, t3 = st.columns(3)
            source_type = t1.selectbox("출처 유형", ["정부·발주처 공식","기업 공시","전국·산업 매체","지역·전문 매체"], index=0 if current_news is None else ["정부·발주처 공식","기업 공시","전국·산업 매체","지역·전문 매체"].index(current_news["source_type"]))
            quality = {"정부·발주처 공식":"High","기업 공시":"High","전국·산업 매체":"Medium","지역·전문 매체":"Low"}[source_type]
            topic = t2.selectbox("분류", ["사업동향","입찰·계약","금융종결","계획·인허가","공사·운영"], index=0 if current_news is None else ["사업동향","입찰·계약","금융종결","계획·인허가","공사·운영"].index(current_news["topic"]))
            severity = t3.selectbox("중요도", ["Watch","Material","Critical"], index=0 if current_news is None else ["Watch","Material","Critical"].index(current_news["severity"]))
            context = st.text_area("추가 내용", "" if current_news is None else current_news["context"])
            if st.form_submit_button("기사 저장", type="primary"):
                parsed = urlparse(source_url)
                if not title.strip() or not summary.strip() or not source_name.strip(): st.error("제목·요약·출처명을 입력해 주세요.")
                elif parsed.scheme not in ("http","https") or not parsed.netloc: st.error("유효한 공개 원문 URL을 입력해 주세요.")
                else:
                    values = {"project_id":int(projects.iloc[selected_idx]["id"]),"title":title.strip(),"summary":summary.strip(),"source_name":source_name.strip(),"source_url":source_url.strip(),"published_at":published.isoformat(),"event_date":event.isoformat(),"topic":topic,"source_type":source_type,"source_quality":quality,"severity":severity,"context":context.strip(),"verified_at":now_kst().date().isoformat(),"approved":0 if current_news is None else int(current_news["approved"])}
                    try:
                        save_news(engine, values, None if current_news is None else int(current_news["id"]))
                    except ValueError as exc:
                        st.error(str(exc)); st.stop()
                    st.success("기사를 저장했습니다."); st.rerun()


st.divider()
st.caption("해외 사업 현황")
