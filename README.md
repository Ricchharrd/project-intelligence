# CEO Project Intelligence — Streamlit

회사 네트워크에서 접근 가능한 Streamlit 기반 해외 PPP 프로젝트 뉴스 대시보드입니다.

배포 순서는 [한국어 배포 안내](DEPLOY_KR.md)를 참고하세요. Python 3.12를 사용합니다. API 키와 모델 ID는 Streamlit Secrets에 입력합니다. 기존 Sites의 이후 변경 사항은 자동 동기화되지 않습니다.

## 제공 기능

- 17개 관심 사업과 2026년 9월 검증 기사 9건
- 출처 품질 High / Medium / Low 분류
- 사업·기사 웹 편집과 CEO 브리핑 선택
- 사업별 대시보드와 첫 화면 업데이트, 브리핑 CSV 다운로드
- OpenAI Responses API 웹 검색을 이용한 검토 후보 생성
- Streamlit Community Cloud 비공개 앱의 이메일 viewer 초대

## 로컬 실행

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

비밀값이 없으면 로컬 SQLite로 실행됩니다. Streamlit Community Cloud의 로컬 파일은 영구 저장이 보장되지 않으므로 운영 배포에서는 PostgreSQL 연결이 필요합니다.

## Streamlit Cloud Secrets

`.streamlit/secrets.example.toml`을 참고하되 실제 키를 GitHub에 커밋하지 마세요. Streamlit Cloud의 **App settings → Secrets**에 직접 등록합니다.

```toml
DATABASE_URL = "postgresql+psycopg://..."
OPENAI_API_KEY = "..."
OPENAI_MODEL = "사용 가능한 Responses API 모델 ID"
```

## 배포

1. 이 폴더를 GitHub 저장소에 푸시합니다.
2. `share.streamlit.io`에서 저장소와 `streamlit_app.py`를 선택합니다.
3. Advanced settings에서 Secrets와 Python 버전을 설정합니다.
4. 앱을 private으로 설정하고 Sharing에서 허용 이메일을 viewer로 초대합니다.

OpenAI API 결과는 확인 전 상태로 저장됩니다. 브리핑 반영은 담당자가 선택합니다. 매일 한국시간 00:00 예약 업데이트용 GitHub Actions 파일을 포함하며, 활성화 방법은 배포 안내에 있습니다.
