# KIS Market Scanner V1

한국투자 Open API의 실제 국내주식 순위 데이터를 이용해 거래량·거래대금·가격 모멘텀이 강한 종목을 찾는 조회 전용 MVP입니다. 주문 기능은 없습니다.

## V1에서 검증하는 것

1. API 키를 코드와 분리해 안전하게 관리합니다.
2. OAuth 접근 토큰을 발급하고 만료 전까지 로컬 캐시를 재사용합니다.
3. 코스닥 거래량 순위 데이터를 조회합니다.
4. 최소 거래대금과 등락률로 위험한 저유동성 후보를 1차 제거합니다.
5. 가격 모멘텀·거래량 증가율·거래대금·순위를 점수화해 상위 후보를 출력합니다.
6. 조회된 후보를 날짜·시간과 함께 SQLite DB에 저장합니다.
7. 저장된 신호의 이후 현재가를 다시 조회해 신호 발생가 대비 수익률을 기록합니다.
8. API 실패와 비정상 응답은 조용히 무시하지 않고 명시적인 오류로 남깁니다.

현재 점수는 **수익이 검증된 투자전략이 아니라 데이터 수집을 시작하기 위한 가설**입니다. 이후 신호 발생 시점과 10분·30분 후 수익률, MFE, MAE를 저장해 통계적으로 수정합니다.

## Windows 실행 방법

PowerShell에서 프로젝트 폴더로 이동한 뒤 실행합니다.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env`에 `KIS_APP_KEY`, `KIS_APP_SECRET`을 입력합니다. 키는 채팅·화면 캡처·GitHub에 올리지 마세요.

선택으로 DB 경로를 바꿀 수 있습니다.

```powershell
KIS_DATABASE_PATH=.cache/kis-scanner.sqlite3
```

연결 확인:

```powershell
kis-scan check
```

코스닥 후보 조회:

```powershell
kis-scan scan --top 10
```

조회 결과를 DB에 저장:

```powershell
kis-scan scan --top 10 --save
```

저장된 최근 신호의 현재가를 다시 조회해 수익률 기록:

```powershell
kis-scan track --hours 24
```

최근 저장 신호 확인:

```powershell
kis-scan history --limit 20
```

필터를 바꾸는 예시:

```powershell
kis-scan scan --top 10 --min-change 1.5 --max-change 15 --min-value-krw 3000000000 --save
```

테스트:

```powershell
pytest
```

## 출력 예시

```text
rank code   name             price  change%  volume%  trade_value       score
1    123456 예시기업         8420      4.80    365.20   12,800,000,000    78.36
```

가격 추적 예시:

```text
signal code   name             signal_px     now_px  return%    mins
1      123456 예시기업            8,420      8,650     2.73    30.0
```

## 저장 구조

SQLite DB 기본 경로는 `.cache/kis-scanner.sqlite3`입니다.

- `signals`: 신호 발생 시각, 종목 코드, 종목명, 발생 당시 가격, 등락률, 거래량 증가율, 거래대금, 점수
- `price_observations`: 관측 시각, 관측 현재가, 신호 발생가 대비 수익률, 신호 후 경과 시간

## 구조

- `config.py`: 환경변수와 실행 설정
- `client.py`: 토큰 발급·캐시·KIS REST 요청
- `models.py`: API 응답을 내부 데이터 구조로 변환
- `scoring.py`: 후보 필터와 가설 점수 계산
- `scanner.py`: 수집 → 변환 → 필터 → 정렬
- `storage.py`: SQLite 저장과 조회
- `tracker.py`: 저장 신호의 현재가 추적
- `cli.py`: 사용자가 실행하는 명령

## 다음 개발 순서

1. 추적 명령을 스케줄러로 10분·30분 뒤 자동 실행
2. 당일 분봉 API로 MACD·RSI·RVOL 계산
3. 매일의 신호 성과 리포트 생성
4. 거래량 순위로 좁힌 후보만 WebSocket 실시간 추적
5. 충분한 표본이 쌓인 뒤 점수식 재설계
