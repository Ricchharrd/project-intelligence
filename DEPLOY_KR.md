# Streamlit 배포 안내

## 1. GitHub에 파일 올리기

배포 ZIP을 풀어 GitHub 비공개 저장소에 올립니다. 저장소 최상위에 streamlit_app.py와 requirements.txt가 있어야 합니다. .streamlit 폴더와 assets도 함께 올립니다. 실제 API 키, secrets.toml, data 폴더는 올리지 않습니다.

## 2. 데이터 저장소 준비

Supabase 또는 Neon에서 PostgreSQL 데이터베이스를 생성하고 연결 문자열을 복사합니다. Supabase는 IPv4 연결을 지원하는 Session pooler 주소를 사용할 수 있습니다. 비밀번호의 특수문자는 URL 인코딩하고 SSL 설정은 제공업체 안내를 따릅니다.

초기 데이터는 프로젝트 17개와 2026년 9월 기사 9건입니다. 기존 Sites 웹사이트에서 이후 수정한 내용은 자동 동기화되지 않습니다.

## 3. Streamlit 배포

https://share.streamlit.io 에 로그인 → Create app → GitHub 저장소 선택.

- Branch: 파일을 올린 브랜치
- Main file path: streamlit_app.py
- Python: 3.12

Advanced settings 또는 앱 Settings → Secrets에 다음을 입력합니다.

```toml
DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres?sslmode=require"
REQUIRE_DATABASE = "true"
OPENAI_API_KEY = "실제 OpenAI API 키"
OPENAI_MODEL = "계정에서 사용 가능한 모델 ID"
```

모델은 Responses API의 웹 검색 및 Structured Outputs를 지원하는 정확한 API 모델 ID를 입력합니다. API 키는 채팅이나 GitHub 소스에 넣지 않습니다. API 설정 없이도 기사 조회와 편집은 사용할 수 있습니다.

## 4. 이메일 접근

앱을 비공개로 설정하고 Sharing에서 허용할 사람의 이메일을 초대합니다. API를 사용하기 전에 비공개 상태를 확인합니다. 현재 앱 안에서는 초대된 이용자 모두가 편집·검색·브리핑 선택을 할 수 있습니다. 조회 전용 이용자와 편집자를 나누는 별도 권한 기능은 없습니다.

## 5. 배포 후 확인

1. 대시보드와 프로젝트 목록을 확인합니다.
2. 프로젝트를 수정한 뒤 재시작하여 저장이 유지되는지 확인합니다.
3. 첫 화면에는 전체 사업의 중요·긴급 기사만 최신순으로 표시됩니다. 사업을 선택하면 그 사업의 전체 기사를 볼 수 있습니다. 날짜 입력은 없으며, 업데이트 버튼은 최근 7일을 검색합니다. 이때 API 사용료가 발생합니다.
4. 새 기사는 확인 전 상태로 저장됩니다. 원문·발행일·사업 관련성을 확인하고 사업·기사 관리에서 수정·저장할 수 있습니다.
5. 브리핑 선택에서 기사를 선택해 CSV를 내려받습니다.

## 매일 한국시간 00:00 자동 업데이트

수동 업데이트는 버튼을 누른 뒤 확인 후 실행을 선택해야 시작합니다. 취소 시 검색 비용이 발생하지 않습니다. 실행 시작 시점부터 모든 사용자가 1시간 동안 수동 업데이트를 실행할 수 없습니다. 다른 사업을 선택하거나 페이지를 새로 열어도 공용 DB의 제한이 적용됩니다. 실행 오류가 발생해도 제한은 유지됩니다. 자정 예약 작업은 별도로 실행됩니다.

`.github/workflows/daily-update.yml`을 저장소 기본 브랜치에 함께 올립니다. GitHub 저장소의 Settings → Secrets and variables → Actions에 Streamlit과 동일한 DATABASE_URL, OPENAI_API_KEY, OPENAI_MODEL을 등록합니다. DATABASE_URL은 반드시 같은 PostgreSQL 저장소를 가리켜야 합니다.

Actions에서 Daily project update를 활성화합니다. 예약은 UTC 15:00(한국시간 다음 날 00:00)이며 GitHub 혼잡 시 실행이 지연되거나 누락될 수 있습니다. 정시 실행 보장은 아닙니다. 현재 로컬 파일만으로는 예약이 활성화되지 않습니다.

모든 활성 사업을 대상으로 전날까지 최근 7일을 검색해 사업당 최신 원문 1건을 찾습니다. 매일 토큰·검색 사용료가 발생합니다. 중복 원문은 저장하지 않고 새 기사는 확인 전 상태로 저장합니다. 브리핑 선택은 자동 승인하지 않습니다. 같은 한국 날짜의 재실행은 이미 처리 시도한 사업을 건너뜁니다. 실패한 사업은 대시보드에서 수동 업데이트하거나 다음 날 예약 실행에서 다시 확인합니다. 실패 내역은 Actions 실행 결과에서 확인합니다.

고정 CEO 양식 PPT 자동 생성은 포함하지 않습니다.

예약 문서: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

DB 오류는 연결 문자열과 서비스 상태를, API 인증 오류는 키를, 한도 오류는 결제 잔액 및 사용 한도를 확인합니다. 모델 오류는 모델 ID와 계정 권한을 확인합니다. 기사 미확인은 사업 변화가 없다는 의미가 아닙니다.

공식 배포 문서: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app

OpenAI 웹 검색: https://developers.openai.com/api/docs/guides/tools-web-search
