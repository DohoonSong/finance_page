# API 규약

기본 경로 `/api/v1`. 모든 요청은 로그인 세션 쿠키가 필요합니다. 성공 응답은 `{"data": ..., "request_id": "..."}`, 실패 응답은 `{"error":{"code":"...","message":"..."},"request_id":"..."}`입니다. 백업 다운로드만 전체 JSON 문서 형식입니다.

변경 요청은 `Content-Type: application/json`, `X-CSRFToken`(로그인 후 페이지 meta 태그), `X-Request-ID`(8~100자 영문/숫자/밑줄/하이픈)를 사용합니다. UUID를 권장합니다. 동일 작업 재시도에는 동일 ID와 본문을 유지합니다. 새 작업은 새 ID를 사용합니다.

```json
{
  "expected_version": 0,
  "name": "생활비 계좌",
  "type": "bank",
  "currency": "KRW",
  "institution": "은행",
  "active": true
}
```

`POST /accounts` 예시입니다. 생성은 `expected_version: 0`, 수정/삭제는 조회한 행의 정수 `version`이 필요합니다. 서버가 UUID, UTC 시각, 버전을 채웁니다. 알 수 없는 입력 필드는 422입니다.

| 경로 | 메서드 | 설명 |
|---|---|---|
| `/dashboard?month=YYYY-MM` | GET | 월말 자산, 예산, 투자, 마감 추이 |
| `/accounts`, `/accounts/{id}` | GET, POST / PATCH | 계좌 |
| `/asset-items`, `/asset-items/{id}` | GET, POST / PATCH | 일반 자산 기준정보 |
| `/budgets?month=YYYY-MM`, `/budgets/{id}` | GET, POST / PATCH, DELETE | 월별 예산 |
| `/budget-summary?month=YYYY-MM` | GET | 5가지 흐름별 합계 |
| `/budgets/{month}/copy` | POST | `source_month`, `expected_version: 0`. 비어 있는 월만 가능 |
| `/recurring-items`, `/recurring-items/{id}` | GET, POST / PATCH | 반복 템플릿 |
| `/assets?date=YYYY-MM-DD` | GET | 통화별 자산·부채·순자산과 근거 |
| `/asset-snapshots`, `/asset-snapshots/{id}` | GET, POST / PATCH | 자산 스냅샷 |
| `/liabilities`, `/liabilities/{id}` | GET, POST / PATCH | KRW 대출 |
| `/liability-snapshots`, `/liability-snapshots/{id}` | GET, POST / PATCH | 대출 잔액 |
| `/holdings?account_id=...`, `/holdings/{id}` | GET, POST / PATCH | 보유종목 스냅샷 |
| `/portfolio-summary` | GET | 계좌별 최신 보유종목과 누적 투자 손익 |
| `/dividends?year=YYYY`, `/dividends/{id}` | GET, POST / PATCH | 배당 원장 |
| `/dividend-summary?year=YYYY` | GET | 월·통화별 총배당/세금/순배당 |
| `/realized-gains`, `/realized-gains/{id}` | GET, POST / PATCH | 실현 손익 |
| `/closings`, `/closings/{month}` | GET | 전체 리비전 / 특정 월 상세 |
| `/closings/{month}/preview` | POST | `{}` 또는 `expected_version` 필드. 계산만 수행 |
| `/closings/{month}/finalize` | POST | 미리보기의 `revision`을 `expected_version`으로, `data_hash` 전달 |
| `/closings/{month}/reopen` | POST | 현재 `revision`을 `expected_version`으로 전달 |
| `/net-worth/history` | GET | 월별 최신 리비전 중 closed만 반환 |
| `/settings` | GET, PATCH | 수정은 `display_name`, 조회 결과의 문자열 `version`을 `expected_version`으로 전달 |
| `/backup` | GET | 인증된 JSON 다운로드 |

상태: 400 잘못된 형식/CSRF, 401 미인증, 403 읽기 전용, 404 없음, 405 허용되지 않은 메서드, 409 충돌/마감/중복, 422 업무 검증 실패, 429 로그인 속도 제한, 503 Sheets/스키마/복구 오류.

상세 필드와 순서는 인계 문서 및 `finance/models.py`를 참고합니다. 계좌 유형은 `bank/cash/brokerage/retirement/other`, 일반 자산 분류는 `cash/deposit/property/other`, 흐름은 `income/expense/investment/transfer/debt_principal`입니다. 스냅샷 중복 키는 자산·날짜, 대출·날짜, 계좌·종목·날짜입니다.
