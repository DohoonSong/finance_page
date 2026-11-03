# Personal Finance Web

`CODEX_HANDOFF_FINANCE_WEB.md` v2.0을 기준으로 구현한 개인용 Flask 가계부입니다. 재무 원본 저장소는 새 Google Sheet `Personal Finance Web DB v2`이며, 기존 가계부 및 ClevMoney 파일을 읽거나 수정하거나 가져오는 코드가 없습니다.

## 구현 범위

- Flask application factory, 인증/API/화면 Blueprint, Pydantic 입력 검증
- 인계 문서의 15개 탭 및 헤더 순서 검증. 불일치 시 503으로 중단
- 단일 소유자 비밀번호 로그인, 해시 저장, CSRF, 8시간 세션, 로그인 속도 제한
- 대시보드, 월별 예산, 자산·부채, 투자, 배당, 월마감, 설정의 한국어 반응형 UI
- 예산 복사, 반복 예산 템플릿 관리, 자산·대출·보유종목 스냅샷, 배당·실현 손익
- UUID, 낙관적 버전 검사, 프로세스 간 파일 잠금, 영속 요청 저널, 불확실한 쓰기 재시도
- 월마감 미리보기/해시 검사/상세 저장/검증/완료, 새 리비전 재개 및 중단 복구
- 짧은 읽기 캐시, 다른 워커의 변경 감지, JSON 백업 다운로드
- 계산 탭 수식 설치 CLI, PythonAnywhere WSGI 설정 예제

실제 Google Sheets 연결, 수식 실행 검증, PythonAnywhere 배포는 아직 수행하지 않았습니다. 서비스 계정과 호스팅 설정이 필요합니다. 앱 기본값은 읽기 전용이고, 시작 시 시트를 생성하거나 데이터를 쓰지 않습니다.

## 프로젝트 구조

```text
finance/
  __init__.py       앱 설정, 보안 헤더, 예외 처리, CLI
  auth.py           로그인, 공유 로그인 시도 제한
  api.py            /api/v1 라우트
  web.py            7개 화면
  schema.py         정확한 시트 스키마
  models.py         입력 및 업무 규칙 검증
  store.py          Sheets/메모리 어댑터, 캐시, 잠금, 요청 저널
  services.py       CRUD, 합계, 순자산, 월마감
  formulas.py       calc_ 수식 및 명시적 설치
  templates/       Jinja2 페이지
  static/          CSS, JavaScript, 외부 CDN 없는 SVG 차트
deploy/pythonanywhere_wsgi.py
tests/             가짜 Sheets 기반 테스트
wsgi.py
.env.example
requirements.txt / requirements.lock.txt / requirements-dev.txt
```

## 로컬 실행

Python 3.11 이상 권장, 현재 Python 3.12에서 검증했습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
flask --app wsgi password-hash
```

출력된 시크릿과 비밀번호 해시를 `.env`의 `SECRET_KEY`, `ADMIN_PASSWORD_HASH`에 넣습니다. 해시는 `$`를 포함하므로 셸에서 직접 export하지 말고 `.env`에 작은따옴표로 감싸 저장하세요. 비밀번호 원문은 저장하지 않습니다.

Google 연결 없이 확인하려면 `.env`를 아래처럼 설정합니다.

```dotenv
APP_ENV=development
DATA_BACKEND=memory
STATE_DIR=/absolute/path/to/finance/instance
SESSION_COOKIE_SECURE=false
WRITES_ENABLED=true
```

```bash
flask --app wsgi run --host 127.0.0.1 --port 5000
```

`http://127.0.0.1:5000`에서 설정한 아이디와 비밀번호로 로그인합니다. 메모리 모드는 항상 빈 데이터로 시작하며 재시작하면 사라집니다. 테스트 데이터는 Google로 전송되지 않습니다. 운영에서는 이 모드를 사용할 수 없습니다.

## Google Sheets 연결

1. Google Cloud 프로젝트에서 Sheets API를 활성화하고 서비스 계정 JSON 키를 발급합니다.
2. 키를 웹 루트 밖(예: `/home/USERNAME/secrets/finance-service-account.json`)에 저장합니다. `chmod 600`으로 접근을 제한합니다.
3. **새 운영용 시트만** 서비스 계정 이메일에 공유합니다. 읽기 검증 시 뷰어, 쓰기 운영 시 편집자가 필요합니다.
4. `.env`에 다음을 설정합니다.

```dotenv
DATA_BACKEND=sheets
SPREADSHEET_ID=1MQ29rkoZKE02srltVNDd7Hs9i7igfEyPiAR5lOUelhs
GOOGLE_APPLICATION_CREDENTIALS=/home/USERNAME/secrets/finance-service-account.json
WRITES_ENABLED=false
```

```bash
flask --app wsgi check-schema
```

이 명령은 헤더 검증만 합니다. ChatGPT Drive 연결과 서버 서비스 계정 인증은 별개입니다. 서비스 계정 키, `.env`, `instance/`는 Git 및 정적 파일 공개 대상에서 제외합니다.

기존 `settings`의 6개 값은 그대로 읽어 표시합니다. 정확한 키 목록이 인계 문서에 없으므로 임의로 해석하거나 덮어쓰지 않습니다. 현재 구현의 정책은 KRW 기준, Asia/Seoul, 문서의 5가지 흐름 분류로 고정합니다. 설정 API에서 수정 가능한 값은 `display_name`뿐입니다.

## 쓰기 및 계산 수식

운영 연결 확인 후 `WRITES_ENABLED=true`로 전환하고 웹앱을 Reload합니다. 모든 입력값은 `RAW`로 기록하여 `=`로 시작하는 메모가 수식으로 실행되지 않습니다. `calc_budget`, `calc_portfolio`는 일반 수정 API에 노출하지 않습니다.

```bash
# 계획 출력만: 운영 시트 접근/쓰기 없음
flask --app wsgi install-formulas
# 운영자가 준비 후 실행: 두 calc_ 탭의 수식 앵커만 설치
flask --app wsgi install-formulas --apply
```

수식은 `SUMIFS`, `MAP`, `SORTN` 등으로 예산과 계좌별 최신 보유종목을 집계합니다. 수식 설치 후 두 탭에서 `#REF!`, `#ERROR!` 및 로케일 호환성을 직접 확인하세요. 기존 수식 앵커가 다르면 덮어쓰지 않고 중단합니다. 시트 locale의 함수 구분자에 따라 수식 조정이 필요할 수 있습니다. UI/API 합계는 Python에서 계산하므로 수식 설치 전에도 사용 가능합니다. 실제 Sheets 수식 평가 결과는 로컬 테스트 대상이 아닙니다.

## 계산 정책과 현재 경계

- 금액은 정수 단위입니다. USD도 정수 달러로 입력합니다. 센트 지원은 현 스키마 범위 밖입니다.
- 예산 잔액 = 수입 − 소비 − 투자 배분 − 원금 상환. 계좌 이체는 잔액을 줄이지 않습니다. 반복 항목은 템플릿으로만 관리하며 자동 월별 반영은 아직 없습니다.
- 자산·부채는 기준일 이전의 항목별 최신 스냅샷을 사용합니다. 값 0도 유효한 스냅샷입니다.
- 은행/현금 등은 자산 항목, 증권/퇴직연금은 보유종목으로만 계산합니다. 증권 계좌에 자산 항목을 생성하지 못하게 하여 중복 평가를 방지합니다. 증권 계좌 현금은 `CASH` 보유종목으로 입력하고, 청산 종목은 원가와 평가액 0의 새 스냅샷을 남깁니다.
- 순배당 = 총배당 − 세금. 순배당은 투자 성과에는 포함하지만 순자산에 다시 더하지 않습니다. 실제 현금 잔액 스냅샷에서 반영하세요.
- 투자 수익 = 최신 평가액 − 최신 원가 + 누적 순배당 + 누적 실현 손익. 단순 수익률이며 기간/시간가중 수익률이 아닙니다.
- KRW와 USD는 분리합니다. 환율 입력·정규화는 미구현이며 외화가 포함된 월은 마감할 수 없습니다. 대출·예산은 스키마에 통화가 없으므로 KRW 전용입니다.
- 단일 소유자 인증입니다. 회원가입, 다중 사용자, MFA, 비밀번호 웹 변경, 자동 백업/복원은 포함하지 않습니다. 백업은 인증 후 JSON으로 다운로드합니다.
- 마감된 기간의 입력 변경은 차단합니다. 누적 스냅샷/배당/실현 손익은 이후 마감에도 영향을 주므로 관련 후속 월을 모두 재개해야 과거 변경이 가능합니다. 기준정보 수정은 마감이 남아 있으면 보수적으로 차단하며 신규 계좌/자산/대출 추가는 가능합니다.

## 동시성 및 장애 복구

Sheets에는 조건부 원자 갱신이 없습니다. **한 배포의 모든 워커가 같은 `STATE_DIR`와 파일 잠금을 사용하고, 모든 쓰기는 이 앱을 통해 이루어져야 합니다.** Google Sheets UI 직접 수정, 다른 배포, 별도 스크립트 쓰기와의 경쟁은 보장하지 않습니다. 운영 워커는 우선 1개를 사용하고 공유 파일 잠금 동작을 검증한 뒤 늘리세요. 분산 배포는 지원하지 않습니다.

재무 원본은 Sheets에 있습니다. `instance/journal.sqlite3`는 요청 ID, 입력 해시, 저장 계획 및 결과를 담는 보조 저널이고 재무 데이터 복사본을 포함합니다. 파일 권한을 제한하고 함께 백업하세요. 저널을 삭제하면 멱등성 기록과 미완료 복구 계획을 잃습니다.

변경 계획을 먼저 로컬에 확정한 뒤 절대 셀 주소에 기록하고 재조회하여 검증합니다. 응답 유실 후 같은 ID 요청을 다시 보내면 중복 추가 없이 복구합니다. 다른 본문에 같은 ID를 사용하면 409입니다. 미완료 요청은 다음 저장소 접근 시 먼저 복구하며 실패하면 503으로 중단합니다. 따라서 `WRITES_ENABLED=false` 전환은 **새 변경 요청**을 차단하지만 이미 접수한 미완료 요청의 복구까지 취소하지 않습니다.

월마감은 `open → preparing → closing_details 저장/검증 → closed` 순서로 저널에 계획됩니다. 재개 시 이전 마감과 상세는 보존하고 별도 UUID/리비전의 `open` 행을 생성합니다. 조회 이력은 모든 리비전을, 순자산 추이는 월별 최신 확정 리비전만 보여줍니다.

잠금 시간 초과나 Google 장애는 503으로 반환합니다. 변경 요청을 무작정 새 ID로 다시 보내지 마세요. 브라우저는 불확실한 요청의 ID를 현재 페이지 메모리에서 유지합니다. 페이지를 닫거나 새로고침했다면 데이터 조회 후 저장 여부를 확인하세요.

## 테스트

```bash
pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
node --check finance/static/app.js
```

테스트는 실제 Google에 연결하지 않습니다. 헤더 검증, 인증/CSRF, 금액과 날짜 검증, API 빈 상태, 통화 분리, 배당 중복 방지, 버전 충돌, 재시도/장애 복구, 월마감 리비전을 검증합니다. 시스템 ROS의 pytest 플러그인 영향을 피하려고 자동 플러그인 로딩을 비활성화합니다.

배포 절차는 [PythonAnywhere 가이드](docs/DEPLOYMENT.md), API 규약은 [API 문서](docs/API.md)를 참고하세요.

선택적 브라우저 검증: Playwright/Chromium이 설치된 별도 환경에서 `python tests/browser_smoke.py`를 실행합니다. 프로젝트 `.venv`로 임시 메모리 서버를 띄워 1440px/390px 화면, 입력·수정, 월마감·재개를 검증합니다. 결과 스크린샷은 `docs/screenshots/`에 저장하며 운영 시트는 사용하지 않습니다.
