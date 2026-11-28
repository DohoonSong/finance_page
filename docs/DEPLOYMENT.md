# PythonAnywhere 배포 준비

배포 설정만 준비되어 있습니다. 실제 계정에 앱을 생성하거나 배포하지 않았습니다.

## 1. 코드와 가상환경

프로젝트를 `/home/USERNAME/finance`에 업로드합니다. 로컬 `.venv`, `.env`, 테스트 저널을 업로드하지 않습니다. Bash console에서 Web 탭과 동일한 Python 버전으로 생성합니다.

```bash
cd /home/USERNAME/finance
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
chmod 600 .env
mkdir -p instance
chmod 700 instance
```

Python 3.12 제공 여부는 계정 환경에서 확인하고, 다른 버전이면 콘솔·가상환경·Web 설정을 일치시킵니다.

## 2. 환경 설정

`flask --app wsgi password-hash`로 해시를 생성하고 `secrets.token_hex(32)`로 시크릿을 생성한 후 `.env`에 넣습니다.

```dotenv
APP_ENV=production
SECRET_KEY='생성한-64자리-시크릿'
ADMIN_USERNAME=owner
ADMIN_PASSWORD_HASH='생성한-비밀번호-해시'
DATA_BACKEND=sheets
SPREADSHEET_ID=1MQ29rkoZKE02srltVNDd7Hs9i7igfEyPiAR5lOUelhs
GOOGLE_APPLICATION_CREDENTIALS=/home/USERNAME/secrets/finance-service-account.json
STATE_DIR=/home/USERNAME/finance/instance
CACHE_SECONDS=5
SESSION_COOKIE_SECURE=true
WRITES_ENABLED=false
```

서비스 계정 키는 `/home/USERNAME/secrets/`에 두고 해당 JSON을 `chmod 600`으로 보호합니다. 기존 가계부/백업에는 공유 권한을 부여할 필요가 없습니다. 별도 서비스 계정의 새 시트 접근 및 Sheets API 활성화가 필요합니다.

```bash
flask --app wsgi check-schema
```

PythonAnywhere의 요금제/네트워크 정책에 따라 `oauth2.googleapis.com` 및 `sheets.googleapis.com` HTTPS 연결 허용 여부를 확인하세요. 위 헤더 검사가 통과하기 전에는 쓰기를 활성화하지 않습니다.

## 3. Web 탭

1. Manual configuration으로 Python 웹앱을 생성합니다.
2. Source code / Working directory: `/home/USERNAME/finance`
3. Virtualenv: `/home/USERNAME/finance/.venv`
4. WSGI 파일을 열고 `deploy/pythonanywhere_wsgi.py` 내용을 복사합니다. `YOURUSERNAME`을 실제 사용자명으로 바꿉니다.
5. 정적 경로 `/static/`만 `/home/USERNAME/finance/finance/static/`에 매핑합니다. 프로젝트 루트, secrets, instance는 매핑하지 않습니다.
6. HTTPS를 활성화하고 HTTP → HTTPS 강제 전환을 설정합니다. 첫 로그인도 HTTPS로 접속합니다.
7. Reload합니다. WSGI import 시 `app.run()`을 호출하지 않습니다.

## 4. 읽기 확인과 쓰기 활성화

- 로그인 성공/실패, 보호된 API 401, 로그아웃, 모바일 메뉴, 빈 대시보드를 확인합니다.
- 서버 오류 로그에서 자격증명 경로·헤더·권한 오류를 확인합니다. 앱은 사용자 응답에 비밀정보를 반환하지 않습니다.
- `WRITES_ENABLED=true`로 바꾸고 Reload하면 입력을 사용할 수 있습니다.
- 수식은 `flask --app wsgi install-formulas`로 계획 확인 후 `--apply`를 붙여 설치합니다. 설치 후 실제 시트에서 결과와 오류 여부를 검증합니다. 운영 데이터에 합성 행을 넣는 검증은 하지 않습니다.
- 한 배포와 하나의 `STATE_DIR`를 유지합니다. 다른 호스트/배포에서 같은 시트에 쓰지 않습니다.

## 5. 운영 및 복구

설정 화면의 JSON 백업을 내려받고, 서버 `.env`와 서비스 키는 별도로 안전하게 보관합니다. 운영 저널 백업은 앱 접근을 중지한 상태에서 파일을 복사하거나 SQLite backup API를 이용합니다. JSON 백업에는 재무 데이터가 포함되므로 공개 저장소에 업로드하지 않습니다.

503 응답의 요청 ID는 앱 로그와 대조합니다. 미완료 쓰기는 같은 요청 ID로 재시도하거나 다음 조회에서 복구됩니다. 수동으로 행을 이동/정렬/삭제하거나 저널을 지운 뒤 재시도하면 안 됩니다. 배포 롤백 시에도 저널과 셀 주소를 보존합니다. 복구가 계속 실패하면 쓰기를 중단하고 오류·저널을 보존하여 원인을 확인하세요.

비밀번호 변경은 새 해시 설정과 Reload로 적용합니다. 기존 세션도 모두 무효화하려면 `SECRET_KEY`도 교체합니다. 생산 환경에서는 debug/reloader를 켜지 않습니다.

공식 참고: [PythonAnywhere Flask 설정](https://help.pythonanywhere.com/pages/Flask/), [Google Sheets RAW 값 쓰기](https://developers.google.com/workspace/sheets/api/guides/values), [gspread API](https://docs.gspread.org/en/latest/api/models/worksheet.html).

## 설정 화면에서 코드 업데이트

최초 1회는 Bash에서 코드를 업데이트하고 Web 탭에서 Reload합니다. PythonAnywhere **Account → API token**에서 발급한 토큰을 서버의 `.env`에만 저장합니다.

```dotenv
DEPLOY_UPDATE_BRANCH=feat/finance-category-filters
PYTHONANYWHERE_API_TOKEN='발급한 API 토큰'
```

운영 서버가 `main`으로 전환되면 이 값도 `main`으로 바꿉니다. 두 설정을 저장하고 Reload한 뒤 로그인하면 설정 탭에서 **업데이트 및 Reload** 버튼을 사용할 수 있습니다. 확인창에서 취소하면 서버에 요청을 보내지 않습니다.

버튼은 현재 체크아웃된 지정 브랜치에서 GitHub의 최신 커밋으로 빠르게 전진할 수 있을 때만 코드를 반영합니다. 작업 폴더에 미커밋 변경이 있거나 원격과 분기한 커밋이 있으면 Bash에서 해결합니다. Git 업데이트에 성공하면 PythonAnywhere API로 Reload를 요청합니다. API가 Reload를 거부하더라도 가져온 코드는 서버에 있으므로 Web 탭에서 Reload할 수 있습니다. 의존성 설치와 환경설정 변경이 필요한 릴리스에는 Bash 배포 절차를 사용합니다.

## ClevMoney 실제 거래 가져오기

Google Cloud 프로젝트에서 **Google Drive API**를 활성화하고, 백업 폴더를 서비스 계정에 뷰어로 공유합니다. 운영 Google Sheets에는 서비스 계정의 편집자 권한과 `WRITES_ENABLED=true`가 필요합니다. PythonAnywhere에서 `www.googleapis.com`과 `oauth2.googleapis.com`에 연결할 수 있어야 합니다.

서버의 `.env`에 `CLEV_BACKUP_FOLDER_ID=1Oj4v4re7dxID6il27F5nP39sJAMvmCPv`를 추가한 뒤 Reload합니다. **설정 → ClevMoney 백업 → 최신 백업 가져오기**를 누르면 Drive에서 수정 시각이 가장 최근인 ClevMoney 백업을 읽습니다.

첫 가져오기에서 운영 스프레드시트에 `actual_transactions` 탭을 만듭니다. 파일 ID·수정 시각·거래 수·월별 행 범위를 첫 행에, 거래 스키마를 둘째 행에 기록합니다. 이후 최신 백업의 전체 거래로 해당 탭을 교체합니다. 웹앱의 예산 원장과 월마감 계산은 기존 데이터를 사용합니다. 백업에 보관된 정산·저축 등은 원본 수입/지출 구분을 따르며 명시적 계좌 이체 분류는 합계에서 분리합니다. 가져온 내역은 **실제 수입·지출** 메뉴에서 조회합니다.

설정 화면의 **실행 중인 커밋**은 Flask가 시작할 때 기록한 값입니다. 버튼은 Git 반영 후 PythonAnywhere에 Reload를 요청하고, 그 이후 새 프로세스가 대상 커밋으로 응답했을 때 **Reload 완료**를 표시합니다. 요청 연결이 끊겨도 상태 조회를 시도합니다. API 토큰 오류 등으로 요청이 거부되면 화면의 HTTP 상태와 PythonAnywhere Error log를 확인할 수 있습니다.
