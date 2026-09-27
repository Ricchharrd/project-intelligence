from __future__ import annotations

from datetime import date
import os
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from openai import AuthenticationError, RateLimitError, APIConnectionError, APIStatusError

from ai_pipeline import collect_update
from storage import create_database, news_frame, projects_frame, save_approvals, save_news, save_project


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
    st.markdown("## 📡 해외 PPP\n**뉴스 인텔리전스**")
    page = st.radio("메뉴", ["경영진 대시보드", "브리핑 선택", "사업·기사 관리", "AI 업데이트", "서비스 구조"], label_visibility="collapsed")
    st.divider()
    report_start = st.date_input("조회 시작일", date.today().replace(day=1))
    report_end = st.date_input("조회 종료일", date.today())
    if persistent:
        st.success("영구 DB 연결")
    else:
        st.warning("로컬 DB 모드")


projects = projects_frame(engine)
news = news_frame(engine)
if report_start > report_end:
    st.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
    st.stop()
september = news[news["published_at"].between(report_start.isoformat(), report_end.isoformat())].copy()
period_label = f"{report_start:%Y.%m.%d} ~ {report_end:%Y.%m.%d}"


def header(title: str, subtitle: str) -> None:
    st.markdown(f'<section class="hero"><div class="eyebrow">PROJECT MARKET INTELLIGENCE</div><h1>{title}</h1><p>{subtitle}</p></section>', unsafe_allow_html=True)


def source_badge(quality: str, source_type: str) -> str:
    return f'<span class="badge {quality.lower()}">{quality} · {source_type}</span>'


def render_news_card(row: pd.Series) -> None:
    row = row.copy()
    for field in ["title", "project_name", "summary", "country", "source_name", "topic", "published_at", "source_type"]:
        row[field] = escape(str(row[field]))
    severity = str(row["severity"]).lower()
    label = {"Critical":"긴급", "Material":"중요", "Watch":"관찰"}.get(row["severity"], row["severity"])
    st.markdown(
        f'''<article class="news-card {severity}">
        <div>{source_badge(str(row['source_quality']), str(row['source_type']))} <span class="meta">{label} · {row['published_at']}</span></div>
        <h3>{row['project_name']} — {row['title']}</h3>
        <p>{row['summary']}</p><div class="meta">{row['country']} · {row['source_name']} · {row['topic']}</div>
        </article>''', unsafe_allow_html=True,
    )
    with st.expander("판단 맥락과 원문"):
        st.write(row["context"] or "별도 맥락 없음")
        st.link_button("공개 원문 보기 ↗", row["source_url"])


if page == "경영진 대시보드":
    header(f"해외 PPP 뉴스 MI · {period_label}", "등록된 공개 원문과 출처 품질을 한 화면에서 검토합니다.")
    covered = september["project_id"].nunique()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("기간 내 기사", f"{len(september)}건")
    c2.metric("확인 사업", f"{covered}/{len(projects)}")
    c3.metric("High 출처", f"{(september['source_quality']=='High').sum()}건")
    c4.metric("중요·긴급", f"{september['severity'].isin(['Critical','Material']).sum()}건")
    c5.metric("기사 미확인", f"{max(0, len(projects)-covered)}개")
    st.markdown('<div class="notice"><b>INDICATIVE</b> — 중요도·요약·판단 맥락은 시연용 판단입니다. 기사 미확인은 사업 변화가 없다는 뜻이 아니며, 의사결정 전 원문과 담당자 검토가 필요합니다.</div>', unsafe_allow_html=True)

    f1, f2, f3 = st.columns([2,1,1])
    project_names = ["전체 사업", *projects["name"].tolist()]
    selected_project = f1.selectbox("사업", project_names)
    selected_severity = f2.selectbox("중요도", ["전체", "Critical", "Material", "Watch"])
    selected_quality = f3.selectbox("출처 품질", ["전체", "High", "Medium", "Low"])
    visible = september
    if selected_project != "전체 사업": visible = visible[visible["project_name"] == selected_project]
    if selected_severity != "전체": visible = visible[visible["severity"] == selected_severity]
    if selected_quality != "전체": visible = visible[visible["source_quality"] == selected_quality]
    st.subheader(f"선택 기간 업데이트 · {len(visible)}건")
    for _, item in visible.iterrows():
        render_news_card(item)
    if visible.empty:
        st.info("선택한 기간과 조건에 등록된 공개 원문이 없습니다.")

elif page == "브리핑 선택":
    header("CEO 브리핑 선택", "브리핑에 넣을 기사만 선택하고 CEO 장표용 CSV로 내려받습니다.")
    edit = september[["id","approved","project_name","title","published_at","source_quality","severity"]].copy()
    edit["approved"] = edit["approved"].astype(bool)
    edited = st.data_editor(edit, hide_index=True, width="stretch", disabled=["id","project_name","title","published_at","source_quality","severity"], column_config={"approved": st.column_config.CheckboxColumn("브리핑")})
    if st.button("선택 상태 저장", type="primary"):
        save_approvals(engine, {int(row.id): bool(row.approved) for row in edited.itertuples()})
        st.success("브리핑 선택을 저장했습니다.")
        st.rerun()
    chosen = edited[edited["approved"]]
    if not chosen.empty:
        export = september[september["id"].isin(chosen["id"])][["project_name","country","published_at","topic","severity","source_quality","title","summary","source_name","source_url","context"]]
        st.download_button("선택 뉴스 CSV 다운로드", export.to_csv(index=False).encode("utf-8-sig"), f"CEO_PPP_Briefing_{report_start}_{report_end}.csv", "text/csv")

elif page == "사업·기사 관리":
    header("사업·기사 관리", "웹에서 관심 사업과 공개 뉴스 항목을 추가하거나 수정합니다.")
    if not persistent:
        st.warning("현재 로컬 SQLite 모드입니다. Streamlit Cloud 운영 배포에서는 DATABASE_URL을 연결해야 변경 내용이 영구 보존됩니다.")
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
            published = d1.date_input("발행일", date.today() if current_news is None else date.fromisoformat(str(current_news["published_at"])))
            event = d2.date_input("사건일", published if current_news is None else date.fromisoformat(str(current_news["event_date"] or current_news["published_at"])))
            t1, t2, t3 = st.columns(3)
            source_type = t1.selectbox("출처 유형", ["정부·발주처 공식","기업 공시","전국·산업 매체","지역·전문 매체"], index=0 if current_news is None else ["정부·발주처 공식","기업 공시","전국·산업 매체","지역·전문 매체"].index(current_news["source_type"]))
            quality = {"정부·발주처 공식":"High","기업 공시":"High","전국·산업 매체":"Medium","지역·전문 매체":"Low"}[source_type]
            topic = t2.selectbox("분류", ["사업동향","입찰·계약","금융종결","계획·인허가","공사·운영"], index=0 if current_news is None else ["사업동향","입찰·계약","금융종결","계획·인허가","공사·운영"].index(current_news["topic"]))
            severity = t3.selectbox("중요도", ["Watch","Material","Critical"], index=0 if current_news is None else ["Watch","Material","Critical"].index(current_news["severity"]))
            context = st.text_area("판단 맥락", "" if current_news is None else current_news["context"])
            if st.form_submit_button("기사 저장", type="primary"):
                parsed = urlparse(source_url)
                if not title.strip() or not summary.strip() or not source_name.strip(): st.error("제목·요약·출처명을 입력해 주세요.")
                elif parsed.scheme not in ("http","https") or not parsed.netloc: st.error("유효한 공개 원문 URL을 입력해 주세요.")
                else:
                    values = {"project_id":int(projects.iloc[selected_idx]["id"]),"title":title.strip(),"summary":summary.strip(),"source_name":source_name.strip(),"source_url":source_url.strip(),"published_at":published.isoformat(),"event_date":event.isoformat(),"topic":topic,"source_type":source_type,"source_quality":quality,"severity":severity,"context":context.strip(),"verified_at":date.today().isoformat(),"approved":0 if current_news is None else int(current_news["approved"])}
                    try:
                        save_news(engine, values, None if current_news is None else int(current_news["id"]))
                    except ValueError as exc:
                        st.error(str(exc)); st.stop()
                    st.success("기사를 저장했습니다."); st.rerun()

elif page == "AI 업데이트":
    header("AI 업데이트 검토", "OpenAI Responses API의 웹 검색과 구조화 출력을 사용해 후보를 만들고, 사람이 검토한 뒤 저장합니다.")
    api_key, model = secret("OPENAI_API_KEY"), secret("OPENAI_MODEL")
    if not api_key or not model:
        st.info("API는 아직 연결되지 않았습니다. Streamlit Secrets에 OPENAI_API_KEY와 OPENAI_MODEL을 등록하면 활성화됩니다. 키는 코드나 채팅에 입력하지 마세요.")
    pidx = st.selectbox("검색할 사업", range(len(projects)), format_func=lambda i: f"{projects.iloc[i]['country']} · {projects.iloc[i]['name']}")
    d1, d2 = st.columns(2)
    start = d1.date_input("시작일", date.today().replace(day=1), key="ai_start")
    end = d2.date_input("종료일", date.today(), max_value=date.today(), key="ai_end")
    st.caption("버튼을 누를 때만 유료 검색을 실행합니다. 한 번에 선택한 사업의 최신 후보 1건을 찾습니다.")
    if st.button("최신 공개 원문 검색", type="primary", disabled=not(api_key and model)):
        with st.spinner("공개 원문을 검색하고 직접 연관성과 출처 품질을 판단하는 중입니다…"):
            try:
                st.session_state.pop("ai_candidate", None)
                st.session_state["ai_candidate"] = collect_update(api_key=api_key, model=model, project_name=projects.iloc[pidx]["name"], country=projects.iloc[pidx]["country"], aliases=projects.iloc[pidx]["aliases"], start_date=start, end_date=end) | {"project_id":int(projects.iloc[pidx]["id"])}
            except AuthenticationError:
                st.error("API 키를 인증하지 못했습니다. Secrets의 OPENAI_API_KEY를 확인해 주세요.")
            except RateLimitError:
                st.error("API 사용 한도 또는 결제 잔액을 확인해 주세요. 잠시 후 다시 실행할 수 있습니다.")
            except APIConnectionError:
                st.error("API 연결이 지연되거나 실패했습니다. 잠시 후 다시 실행해 주세요.")
            except APIStatusError:
                st.error("API 요청이 거절되었습니다. 모델 접근 권한과 웹 검색 지원 여부를 확인해 주세요.")
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                st.error("검색 결과를 처리하지 못했습니다. 설정을 확인하고 다시 실행해 주세요.")
    candidate = st.session_state.get("ai_candidate")
    if candidate:
        if not candidate.get("found"):
            st.warning(f"직접 관련 원문 미확인: {candidate.get('reason','')}")
        else:
            st.subheader("검토 대기 후보")
            st.link_button("후보 원문 열기", candidate["source_url"])
            st.json(candidate, expanded=True)
            st.caption("AI 결과는 자동 승인되지 않습니다. 원문 링크와 날짜를 직접 확인한 뒤 저장하세요.")
            reviewed = st.checkbox("원문과 발행일, 해당 사업과의 관련성을 확인했습니다.", key=f"review_{candidate['project_id']}_{candidate['source_url']}")
            if st.button("검토한 후보 저장", disabled=not reviewed):
                values = {k:candidate[k] for k in ["project_id","title","summary","source_name","source_url","published_at","event_date","topic","source_type","source_quality","severity","context"]}
                values |= {"verified_at":date.today().isoformat(),"approved":0}
                try:
                    save_news(engine, values)
                except ValueError as exc:
                    st.error(str(exc)); st.stop()
                st.success("후보를 저장했습니다."); del st.session_state["ai_candidate"]; st.rerun()

else:
    header("서비스 아키텍처", "OpenAI API의 판단과 사람의 최종 승인을 분리한 Streamlit 운영 구조입니다.")
    cols = st.columns(5)
    stages = [("01","수집","공개 원문과 지정 기간의 기사 후보 수집"),("02","전처리","날짜·중복·사업 별칭 확인"),("03","AI 판단","중요도·경영진 영향·출처 품질 구조화"),("04","사람 승인","원문 검토 후 브리핑 선택"),("05","CEO 출력","승인 항목만 CSV·고정 슬라이드에 반영")]
    for col, (num, title, body) in zip(cols, stages):
        col.markdown(f'<div class="flow"><div class="eyebrow">{num}</div><h3>{title}</h3><p>{body}</p></div>', unsafe_allow_html=True)
    st.subheader("운영 계층")
    a,b,c = st.columns(3)
    a.info("**DATA**\n\nPostgreSQL(Supabase/Neon) — 사업·기사·승인 상태 영구 저장")
    b.info("**AI**\n\nOpenAI Responses API — 웹 검색·구조화 판단, 자동 승인 금지")
    c.info("**ACCESS**\n\nStreamlit 비공개 앱 — 이메일 viewer 초대 및 GitHub 관리자 분리")
    deck = Path(__file__).resolve().parent / "assets/CEO_Project_Intelligence_Architecture_KR.pptx"
    if deck.exists(): st.download_button("아키텍처 PowerPoint 다운로드", deck.read_bytes(), deck.name, "application/vnd.openxmlformats-officedocument.presentationml.presentation")

st.divider()
st.caption("CEO Project Intelligence · indicative decision support · 공개 원문과 사람의 최종 검토를 우선합니다.")
