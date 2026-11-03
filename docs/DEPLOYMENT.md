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
