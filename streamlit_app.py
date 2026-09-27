from __future__ import annotations

from datetime import date, timedelta
import os
from html import unescape
from html.parser import HTMLParser
from update_jobs import now_kst, update_project
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from openai import AuthenticationError, RateLimitError, APIConnectionError, APIStatusError

from ai_pipeline import collect_update
from storage import create_database, news_frame, projects_frame, save_approvals, save_news, save_project
from storage import manual_update_remaining, claim_manual_update


st.set_page_config(page_title="CEO Project Intelligence", page_icon="📡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    :root { --navy:#0d2938; --cyan:#20a9c6; --ink:#16222d; --muted:#687781; }
    .stApp { background:#f3f6f6; color:var(--ink); }
    [data-testid="stSidebar"] { background:var(--navy); }
    [data-testid="stSidebar"] * { color:#e8f2f4; }
    [data-testid="stSidebar"] [data-testid="stRadio"] label { padding:.42rem .55rem; border-radius:.45rem; }
    .hero { background:white; border:1px solid #dce3e6; border-radius:12px; padding:22px 26px; margin-bottom:18px; }
    .hero h1 { margin:0; font-size:1.85rem; color:var(--ink); }
    .hero p { color:var(--muted); margin:.45rem 0 0; }
    .eyebrow { color:#71838c; letter-spacing:.13em; font-size:.72rem; font-weight:800; }
    .badge { display:inline-block; padding:.2rem .55rem; border-radius:999px; font-size:.72rem; font-weight:800; }
    .high { color:#21764d; background:#e4f3e9; } .medium { color:#956210; background:#fff0cf; } .low { color:#a6423c; background:#f9e6e4; }
    .critical { border-left:4px solid #c9473c; } .material { border-left:4px solid #d59b32; } .watch { border-left:4px solid #6c8793; }
    .news-card { background:white; border-top:1px solid #dce3e6; border-right:1px solid #dce3e6; border-bottom:1px solid #dce3e6; border-radius:9px; padding:16px 18px; margin:8px 0; }
    .news-card h3 { margin:.35rem 0; font-size:1.05rem; }
    .news-card p { margin:.25rem 0; color:#526770; line-height:1.55; }
    .meta { color:#7b8990; font-size:.78rem; }
    .notice { background:#fff7e6; border:1px solid #ecd59b; border-radius:8px; padding:12px 14px; color:#6f571f; margin:10px 0 18px; }
    .flow { background:white; border:1px solid #dce3e6; border-radius:10px; padding:18px; min-height:170px; }
    div[data-testid="stMetric"] { background:white; border:1px solid #dce3e6; padding:14px; border-radius:10px; }
</style>
""", unsafe_allow_html=True)


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.environ.get(name, default)))
    except FileNotFoundError:
        return os.environ.get(name, default)


@st.cache_resource
def database():
    if secret("REQUIRE_DATABASE", "false").lower() == "true" and not secret("DATABASE_URL"):
        raise ValueError("DATABASE_URL required")
    return create_database(secret("DATABASE_URL") or None)


try:
    engine, persistent = database()
except Exception:
    st.error("데이터베이스에 연결할 수 없습니다.")
    st.info("Streamlit 설정의 Secrets에서 DATABASE_URL을 확인하고 데이터베이스가 실행 중인지 확인해 주세요.")
    st.stop()

with st.sidebar:
    st.markdown("## 해외 사업 현황")
    page = st.radio("메뉴", ["대시보드", "브리핑 선택", "사업·기사 관리"], label_visibility="collapsed")


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
    st.caption(" · ".join(clean(row[k]) for k in ["country", "source_name", "topic"]))
    if clean(row.get("context", "")):
        st.text(clean(row["context"]))
    quality = {"High": "높음", "Medium": "보통", "Low": "낮음"}.get(row["source_quality"], row["source_quality"])
    severity = {"Critical": "긴급", "Material": "중요", "Watch": "참고"}.get(row["severity"], row["severity"])
    review = "확인 전" if not row.get("verified_at") else "확인됨"
    st.caption(f"{row['published_at']} · 출처 신뢰도 {quality} · {severity} · {review}")
    url = str(row["source_url"])
    if urlparse(url).scheme in ("http", "https"):
        st.link_button("원문 보기", url)


if page == "대시보드":
    header("대시보드", "주요 업데이트를 확인하거나 사업을 선택하세요.")
    active = projects[projects["active"] == 1]
    options = {"전체 사업 · 주요 업데이트": None}
    options.update({f"{r['name']} — {r['country']}": int(r['id']) for _, r in active.iterrows()})
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    selected = left.selectbox("사업", list(options))
    selected_id = options[selected]
    api_key, model = secret("OPENAI_API_KEY"), secret("OPENAI_MODEL")
    st.caption("토큰이 소모되니 필요할 때만 업데이트 하십시오")
    remaining = manual_update_remaining(engine)
    update = right.button("업데이트", type="primary", width="stretch", disabled=not(api_key and model) or remaining > 0 or active.empty)
    if remaining:
        st.caption(f"다른 사용자의 실행을 포함해 업데이트는 1시간에 한 번 가능합니다. 약 {(remaining + 59) // 60}분 후 다시 실행할 수 있습니다.")
        if st.button("실행 가능 시간 확인"):
            st.rerun()
    if not api_key or not model:
        st.caption("업데이트 연결을 준비 중입니다.")
    if update:
        st.session_state["confirm_update"] = selected
    if st.session_state.get("confirm_update") != selected or remaining:
        st.session_state.pop("confirm_update", None)
    confirmed = False
    if st.session_state.get("confirm_update"):
        with st.container(border=True):
            st.write(f"{selected} 업데이트를 실행하시겠습니까?")
            st.caption("토큰이 소모되니 필요할 때만 업데이트 하십시오")
            st.caption("최근 7일의 기사를 검색합니다. 실행 후에는 모든 사용자의 업데이트가 1시간 동안 제한됩니다.")
            yes, no = st.columns(2)
            confirmed = yes.button("확인 후 실행", type="primary")
            if no.button("취소"):
                st.session_state.pop("confirm_update", None)
                st.rerun()
    if confirmed:
        st.session_state.pop("confirm_update", None)
        if not claim_manual_update(engine):
            st.session_state["update_results"] = ["다른 사용자가 먼저 업데이트를 실행했습니다. 1시간 후 다시 시도해 주세요."]
            st.rerun()
        targets = active if selected_id is None else active[active["id"] == selected_id]
        results = []
        progress = st.progress(0, text="기사를 확인하고 있습니다.")
        for i, project in enumerate(targets.to_dict("records")):
            try:
                today = now_kst().date()
                result = update_project(engine, project, api_key, model, today - timedelta(days=6), today)
            except AuthenticationError:
                result = "연결 인증에 실패했습니다."
            except RateLimitError:
                result = "사용 한도를 확인해 주세요."
            except Exception:
                result = "업데이트하지 못했습니다. 잠시 후 다시 시도해 주세요."
            results.append(f"{project['name']}: {result}")
            progress.progress((i + 1) / len(targets), text=f"{i + 1}/{len(targets)}개 사업 확인")
        st.session_state["update_results"] = results
        st.rerun()
    if st.session_state.get("update_results"):
        for result in st.session_state.pop("update_results"):
            st.text(result)
    quality_filter = st.selectbox("출처 신뢰도", ["전체", "높음", "보통", "낮음"])
    quality_value = {"높음": "High", "보통": "Medium", "낮음": "Low"}.get(quality_filter)
    articles = news[news["project_id"].isin(active["id"])]
    if selected_id is None:
        articles = articles[articles["severity"].isin(["Critical", "Material"])]
        st.subheader("주요 업데이트")
        st.caption("전체 사업의 중요·긴급 소식 · 최신순")
    else:
        articles = articles[articles["project_id"] == selected_id]
        st.subheader(clean(selected))
        st.caption("이 사업의 전체 소식 · 최신순")
    if quality_value:
        articles = articles[articles["source_quality"] == quality_value]
    if articles.empty:
        st.info("등록된 주요 업데이트가 없습니다." if selected_id is None else "이 사업에 등록된 기사가 없습니다.")
    for _, item in articles.iterrows():
        with st.container(border=True):
            if selected_id is None:
                st.caption(f"{clean(item['project_name'])} — {clean(item['country'])}")
            render_news_card(item)

elif page == "브리핑 선택":
    header("CEO 브리핑 선택", "브리핑에 넣을 기사만 선택하고 CEO 장표용 CSV로 내려받습니다.")
    edit = news[["id","approved","project_name","title","published_at","source_quality","severity"]].copy()
    edit["approved"] = edit["approved"].astype(bool)
    edited = st.data_editor(edit, hide_index=True, width="stretch", disabled=["id","project_name","title","published_at","source_quality","severity"], column_config={"approved": st.column_config.CheckboxColumn("브리핑")})
    if st.button("선택 상태 저장", type="primary"):
        save_approvals(engine, {int(row.id): bool(row.approved) for row in edited.itertuples()})
        st.success("브리핑 선택을 저장했습니다.")
        st.rerun()
    chosen = edited[edited["approved"]]
    if not chosen.empty:
        export = news[news["id"].isin(chosen["id"])][["project_name","country","published_at","topic","severity","source_quality","title","summary","source_name","source_url","context"]]
        st.download_button("선택 뉴스 CSV 다운로드", export.to_csv(index=False).encode("utf-8-sig"), f"CEO_PPP_Briefing_{now_kst().date()}.csv", "text/csv")

elif page == "사업·기사 관리":
    header("사업·기사 관리", "웹에서 관심 사업과 공개 뉴스 항목을 추가하거나 수정합니다.")
    project_tab, news_tab = st.tabs(["관심 사업", "기사 등록·수정"])
    with project_tab:
        st.dataframe(projects[["id","name","country","active","news_count"]], hide_index=True, width="stretch")
        choices = ["새 사업 추가", *projects.apply(lambda r: f"{r['id']} · {r['name']}", axis=1).tolist()]
        choice = st.selectbox("편집 대상", choices)
        current = None if choice == "새 사업 추가" else projects[projects["id"] == int(choice.split(" · ")[0])].iloc[0]
        with st.form("project_form"):
            name = st.text_input("사업명", "" if current is None else current["name"])
            country = st.text_input("국가", "" if current is None else current["country"])
            aliases = st.text_input("검색 별칭", "" if current is None else current["aliases"])
            active = st.checkbox("활성", True if current is None else bool(current["active"]))
            if st.form_submit_button("사업 저장", type="primary"):
                if not name.strip() or not country.strip(): st.error("사업명과 국가를 입력해 주세요.")
                else:
                    save_project(engine, {"name":name.strip(),"country":country.strip(),"aliases":aliases.strip(),"active":1 if active else 0}, None if current is None else int(current["id"]))
                    st.success("사업을 저장했습니다."); st.rerun()
    with news_tab:
        news_choices = ["새 기사 등록", *news.apply(lambda r: f"{r['id']} · {r['title']}", axis=1).tolist()]
        news_choice = st.selectbox("기사 선택", news_choices)
        current_news = None if news_choice == "새 기사 등록" else news[news["id"] == int(news_choice.split(" · ")[0])].iloc[0]
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
