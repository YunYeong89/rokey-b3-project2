# Packing Planning 비교 실험 보고서 — 20261127_124924

> 자동 생성(evaluate_run.py). 수치는 저장된 원본 응답과 독립 Validator 결과에서 계산. 상품 치수·무게·허용 하중은 **개발용 근사값**이며 검증 결과가 실제 물리적 안정성을 보장하지 않는다.

## 1. 실험 조건

- 요청 모델: `gpt-4o` / 실제 응답 모델: `gpt-4o-2024-08-06`
- 생성 설정: `{"temperature": 0, "max_output_tokens": 6000}` (Structured Outputs `json_schema`, strict=true, store=false, 자동 재시도 0회)
- 주문 `pilot_01`: 코카콜라×2, 참치캔×1, 스팸×1, 신라면 컵×1, 아몬드 빼빼로×1, 쫄병스낵×1 (총 7개)
- item_id: `coca_cola_1`, `coca_cola_2`, `tuna_can_1`, `spam_1`, `shin_ramyun_1`, `almond_pepero_1`, `jjolbyeong_1`
- 포장 공간: [320, 260, 200] mm (x,y,안전 적재 높이), 벽 여유 10 mm, 최대 2층
- 사진: `basket.jpg` sha256 754212088361…
- 반복: 1회

| 실험 | 입력 정보 | 실행 상태 |
|---|---|---|
| Text-only (r1) | 상품명·수량·공간 | ok |
| Text + Vision (r1) | 상품명·수량·공간 + 실제 사진 | ok |
| Text + Physical Properties (r1) | 상품명·수량·공간 + 주문 상품 물리 특성(6종) | ok |

## 2. 평가 지표 비교

| 지표 | Text-only r1 | Text + Vision r1 | Text + Physical Properties r1 |
|---|---|---|---|
| 계획 생성 성공(status=planned) | True | True | True |
| 계획 유효(위반 0건) | False | False | False |
| 총 위반 건수 | 18 | 16 | 4 |
| 상품 누락 | 0 | 0 | 0 |
| 중복 배치 | 0 | 0 | 0 |
| 상품 간 충돌(AABB) | 5 | 3 | 1 |
| 공간 경계 위반 | 6 | 6 | 2 |
| 방향 위반 | 0 | 0 | 0 |
| 적재 금지 상품 위 적재 | 0 | 0 | 0 |
| 적재 하중 초과 | 0 | 0 | 0 |
| 지지면 부족(공중부양/파고듦 포함) | 7 | 7 | 1 |
| 무게중심 안정성 위반 | 0 | 0 | 0 |
| 최대 층수 초과 | 0 | 0 | 0 |
| 담기 순서 위반 | 0 | 0 | 0 |
| 적재 안정성(근사) 통과 | False | False | False |
| 최대 적재 높이(mm) | 121.0 | 123.5 | 122.0 |
| 실제 사용 층수(지지 확인된 상품 기준) | 0 | 0 | 1 |
| 지지 없는 상품 수 | 7 | 7 | 1 |
| 사용 공간 외접 박스(mm) | 297.5×280.0×136.0 | 292.5×300.0×143.5 | 325.0×290.5×122.0 |
| 사용 공간 외접 부피(cm³) | 11328.8 | 12592.1 | 11518.3 |
| LLM 선언 layer 불일치(경고) | 7 | 7 | 1 |
| LLM 선언 supported_by 불일치(경고) | 0 | 0 | 0 |
| [보조] 자체 가정 치수 기준 유효 | False | False | False |
| [보조] 자체 가정 치수 기준 위반 건수 | 6 | 8 | 4 |
| API 응답 시간(s) | 6.595 | 6.678 | 3.636 |
| Input tokens | 1502 | 2302 | 2242 |
| Output tokens | 690 | 820 | 696 |
| Total tokens | 2192 | 3122 | 2938 |
| 추정 비용(USD) | 0.010655 | 0.013955 | 0.012565 |

비용은 공개 단가(gpt-4o 표준 요금) 기준 추정, 청구액 아님. 세 실험 모두 동일 상품·동일 용기이므로 (상품 총부피/용기 부피) 비율은 같아 비교 지표로 쓰지 않았다.

## 3. 실험별 담기 순서 및 배치 계획

### Text-only (r1)

- status: `planned` — All items fit within the allowed region without exceeding the safe fill height.
- assumptions:
  - Assumed dimensions and weights based on typical product sizes.
  - Coca Cola bottles and cans are stable when placed upright.
  - Pepero and snack are stable when placed flat.

| 순서 | item_id | 위치 x,y,z (mm) | 자세 | yaw | 선언 층 | 계산 층 | 선언 지지 | 계산 지지 | LLM 가정 치수 W×D×H | DB 치수 | LLM 가정 무게 / DB | 이유 |
|---:|---|---|---|---:|---:|---:|---|---|---|---|---|---|
| 1 | `coca_cola_1` | 45, 45, 60 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 60×60×120 | 66×66×122 | 500 / 370 | Cylindrical bottle placed upright for stability. |
| 2 | `coca_cola_2` | 135, 45, 60 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 60×60×120 | 66×66×122 | 500 / 370 | Cylindrical bottle placed upright for stability. |
| 3 | `tuna_can_1` | 225, 45, 45 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 90×90×90 | 85×85×40 | 200 / 165 | Cylindrical can placed upright for stability. |
| 4 | `spam_1` | 45, 135, 40 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 80×60×80 | 100×60×55 | 340 / 230 | Rectangular can placed upright for stability. |
| 5 | `shin_ramyun_1` | 135, 135, 40 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 75×75×80 | 100×100×110 | 100 / 80 | Cup noodle placed upright for stability. |
| 6 | `almond_pepero_1` | 225, 135, 20 | flat | 0 | 1 | – | 바닥 | 바닥/없음 | 40×20×200 | 85×25×155 | 50 / 48 | Box placed flat due to its shape. |
| 7 | `jjolbyeong_1` | 45, 225, 20 | flat | 0 | 1 | – | 바닥 | 바닥/없음 | 50×20×150 | 150×115×55 | 30 / 87 | Snack placed flat due to its shape. |

**Validator 위반 내역**

- [3. 포장 공간 경계 위반] coca_cola_1: x[12.0,78.0] y[12.0,78.0] z[-1.0,121.0]
- [3. 포장 공간 경계 위반] coca_cola_2: x[102.0,168.0] y[12.0,78.0] z[-1.0,121.0]
- [3. 포장 공간 경계 위반] tuna_can_1: x[182.5,267.5] y[2.5,87.5] z[25.0,65.0]
- [3. 포장 공간 경계 위반] spam_1: x[-5.0,95.0] y[105.0,165.0] z[12.5,67.5]
- [3. 포장 공간 경계 위반] shin_ramyun_1: x[85.0,185.0] y[85.0,185.0] z[-15.0,95.0]
- [3. 포장 공간 경계 위반] jjolbyeong_1: x[-30.0,120.0] y[167.5,282.5] z[-7.5,47.5]
- [4. 상품 간 충돌] tuna_can_1 / shin_ramyun_1 (겹침 2.5×2.5×40.0mm)
- [4. 상품 간 충돌] tuna_can_1 / almond_pepero_1 (겹침 85.0×30.0×7.5mm)
- [4. 상품 간 충돌] spam_1 / shin_ramyun_1 (겹침 10.0×60.0×55.0mm)
- [4. 상품 간 충돌] shin_ramyun_1 / almond_pepero_1 (겹침 2.5×100.0×25.0mm)
- [4. 상품 간 충돌] shin_ramyun_1 / jjolbyeong_1 (겹침 35.0×17.5×55.0mm)
- [8. 지지면 부족(공중 부양 포함)] shin_ramyun_1: z_bottom=-15.0mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] jjolbyeong_1: z_bottom=-7.5mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] coca_cola_1: z_bottom=-1.0mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] coca_cola_2: z_bottom=-1.0mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] almond_pepero_1: z_bottom=7.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] spam_1: z_bottom=12.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] tuna_can_1: z_bottom=25.0mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) coca_cola_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) coca_cola_2: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) tuna_can_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) spam_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) shin_ramyun_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) almond_pepero_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) jjolbyeong_1: 선언 1 / 계산 None

### Text + Vision (r1)

- status: `planned` — 모든 상품을 바닥에 배치하여 적재 가능.
- assumptions:
  - 코카콜라 캔의 크기는 일반적인 355ml 캔 크기로 가정.
  - 참치캔은 일반적인 원형 캔 크기로 가정.
  - 스팸은 일반적인 직사각형 캔 크기로 가정.
  - 신라면 컵은 일반적인 컵라면 크기로 가정.
  - 아몬드 빼빼로와 쫄병스낵은 가벼운 스낵으로 가정.

| 순서 | item_id | 위치 x,y,z (mm) | 자세 | yaw | 선언 층 | 계산 층 | 선언 지지 | 계산 지지 | LLM 가정 치수 W×D×H | DB 치수 | LLM 가정 무게 / DB | 이유 |
|---:|---|---|---|---:|---:|---:|---|---|---|---|---|---|
| 1 | `coca_cola_1` | 40, 40, 62.5 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 66×66×125 | 66×66×122 | 350 / 370 | 탄산음료 캔은 단단하고 무거워 바닥에 배치. |
| 2 | `coca_cola_2` | 140, 40, 62.5 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 66×66×125 | 66×66×122 | 350 / 370 | 탄산음료 캔은 단단하고 무거워 바닥에 배치. |
| 3 | `tuna_can_1` | 240, 40, 45 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 90×90×90 | 85×85×40 | 200 / 165 | 참치캔은 단단하고 무거워 바닥에 배치. |
| 4 | `spam_1` | 40, 140, 40 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 80×60×80 | 100×60×55 | 200 / 230 | 스팸은 단단하고 무거워 바닥에 배치. |
| 5 | `shin_ramyun_1` | 140, 140, 35 | upright | 0 | 1 | – | 바닥 | 바닥/없음 | 75×75×70 | 100×100×110 | 100 / 80 | 라면 컵은 가벼우나 바닥에 배치. |
| 6 | `almond_pepero_1` | 240, 140, 15 | flat | 0 | 1 | – | 바닥 | 바닥/없음 | 40×20×150 | 85×25×155 | 50 / 48 | 빼빼로는 가볍고 평평하게 배치. |
| 7 | `jjolbyeong_1` | 140, 240, 15 | flat | 0 | 1 | – | 바닥 | 바닥/없음 | 50×20×150 | 150×115×55 | 50 / 87 | 쫄병스낵은 가볍고 평평하게 배치. |

**Validator 위반 내역**

- [3. 포장 공간 경계 위반] coca_cola_1: x[7.0,73.0] y[7.0,73.0] z[1.5,123.5]
- [3. 포장 공간 경계 위반] coca_cola_2: x[107.0,173.0] y[7.0,73.0] z[1.5,123.5]
- [3. 포장 공간 경계 위반] tuna_can_1: x[197.5,282.5] y[-2.5,82.5] z[25.0,65.0]
- [3. 포장 공간 경계 위반] spam_1: x[-10.0,90.0] y[110.0,170.0] z[12.5,67.5]
- [3. 포장 공간 경계 위반] shin_ramyun_1: x[90.0,190.0] y[90.0,190.0] z[-20.0,90.0]
- [3. 포장 공간 경계 위반] jjolbyeong_1: x[65.0,215.0] y[182.5,297.5] z[-12.5,42.5]
- [4. 상품 간 충돌] tuna_can_1 / almond_pepero_1 (겹침 85.0×20.0×2.5mm)
- [4. 상품 간 충돌] shin_ramyun_1 / jjolbyeong_1 (겹침 100.0×7.5×55.0mm)
- [4. 상품 간 충돌] almond_pepero_1 / jjolbyeong_1 (겹침 17.5×35.0×25.0mm)
- [8. 지지면 부족(공중 부양 포함)] shin_ramyun_1: z_bottom=-20.0mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] jjolbyeong_1: z_bottom=-12.5mm, 바닥/지지 상품과 접촉 없음(바닥 아래로 파고듦)
- [8. 지지면 부족(공중 부양 포함)] coca_cola_1: z_bottom=1.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] coca_cola_2: z_bottom=1.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] almond_pepero_1: z_bottom=2.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] spam_1: z_bottom=12.5mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- [8. 지지면 부족(공중 부양 포함)] tuna_can_1: z_bottom=25.0mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) coca_cola_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) coca_cola_2: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) tuna_can_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) spam_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) shin_ramyun_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) almond_pepero_1: 선언 1 / 계산 None
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) jjolbyeong_1: 선언 1 / 계산 None

### Text + Physical Properties (r1)

- status: `planned` — All items fit within the allowed region without stacking on fragile items.
- assumptions:
  - Assumed Shin Ramyun cup dimensions as 100x100x110 mm based on typical size.
  - All items are placed in a single layer due to fragility constraints.

| 순서 | item_id | 위치 x,y,z (mm) | 자세 | yaw | 선언 층 | 계산 층 | 선언 지지 | 계산 지지 | LLM 가정 치수 W×D×H | DB 치수 | LLM 가정 무게 / DB | 이유 |
|---:|---|---|---|---:|---:|---:|---|---|---|---|---|---|
| 1 | `coca_cola_1` | 43, 43, 61 | upright | 0 | 1 | 1 | 바닥 | 바닥/없음 | 66×66×122 | 66×66×122 | 370 / 370 | First item placed at the bottom left corner. |
| 2 | `coca_cola_2` | 143, 43, 61 | upright | 0 | 1 | 1 | 바닥 | 바닥/없음 | 66×66×122 | 66×66×122 | 370 / 370 | Placed next to the first Coca-Cola can. |
| 3 | `spam_1` | 243, 40, 27.5 | upright | 0 | 1 | 1 | 바닥 | 바닥/없음 | 100×60×55 | 100×60×55 | 230 / 230 | Placed next to the second Coca-Cola can. |
| 4 | `tuna_can_1` | 43, 143, 20 | upright | 0 | 1 | 1 | 바닥 | 바닥/없음 | 85×85×40 | 85×85×40 | 165 / 165 | Placed behind the first Coca-Cola can. |
| 5 | `shin_ramyun_1` | 143, 143, 55 | upright | 0 | 1 | 1 | 바닥 | 바닥/없음 | 100×100×110 | 100×100×110 | 80 / 80 | Placed behind the second Coca-Cola can. |
| 6 | `almond_pepero_1` | 243, 143, 77.5 | flat | 0 | 1 | – | 바닥 | 바닥/없음 | 85×25×155 | 85×25×155 | 48 / 48 | Placed behind the Spam can, flat orientation. |
| 7 | `jjolbyeong_1` | 43, 243, 27.5 | flat | 0 | 1 | 1 | 바닥 | 바닥/없음 | 150×115×55 | 150×115×55 | 87 / 87 | Placed behind the Tuna can, flat orientation. |

**Validator 위반 내역**

- [3. 포장 공간 경계 위반] tuna_can_1: x[0.5,85.5] y[100.5,185.5] z[0.0,40.0]
- [3. 포장 공간 경계 위반] jjolbyeong_1: x[-32.0,118.0] y[185.5,300.5] z[0.0,55.0]
- [4. 상품 간 충돌] shin_ramyun_1 / jjolbyeong_1 (겹침 25.0×7.5×55.0mm)
- [8. 지지면 부족(공중 부양 포함)] almond_pepero_1: z_bottom=65.0mm, 바닥/지지 상품과 접촉 없음(공중 부양)
- (경고: LLM이 선언한 layer와 기하학적으로 계산된 층이 다름) almond_pepero_1: 선언 1 / 계산 None

## 4. Validator 구현 범위

구현됨(AABB 근사): 1. 주문 상품/수량 누락, 1. 주문에 없는 item_id, 2. 상품 중복 배치, 3. 포장 공간 경계 위반, 4. 상품 간 충돌, 5. 허용 배치 방향 위반, 6. 상부 적재 금지 상품 위 적재, 7. 허용 적재 하중 초과, 8. 지지면 부족(공중 부양 포함), 9. 무게중심 투영점이 지지 볼록껍질 밖, 10. 최대 적재 층수 초과, 11. 담기 순서 위반(지지 상품보다 먼저 배치 / 순번 오류)

미구현/근사 한계:
- 실제 무게중심(내용물 분포) — 외접 직육면체의 기하 중심으로 대체
- 원통/원뿔대 형상의 정확한 충돌 — AABB로 보수적으로 근사(실제로는 닿지 않는 캔끼리도 충돌로 판정될 수 있음)
- 신라면 컵 원뿔대 형상의 윗면/아랫면 크기 차이
- 마찰, 상품 변형, 봉투 변형/들림, 동역학(운반 중 흔들림)
- 그리퍼(RG2) 접근 경로 및 손가락 간섭(swept volume), 봉투 입구(300×240mm) 통과 경로
- 로봇 도달 가능성/작업 시간

## 5. 결과 파일

- `experiment_*.json`: 요청 입력(이미지 base64 생략), 파싱된 plan, Validator 결과, 토큰/시간
- `raw_responses/`: OpenAI Responses API 응답 원문
- `comparison.csv`: 지표 비교표
- `packing_plans_3d.png`: 동일 공간·좌표계·색상의 3D/Top view 시각화(DB 치수 기준 AABB)
- `config.json`: 프롬프트, 스키마, 생성 설정, 주문, 공간

## 6. 비고 (수동 작성)

- 이 디렉터리는 **예비 실행(Run 1)** 이다. 본 비교와 분석은 `results/20261127_125141/experiment_report.md`에 있다.
- 이 실행의 Experiment 2 요청에는 "아래 사진은 주문된 실제 상품을 촬영한 것이다"라는 문구가 들어갔다. 실제 `basket.jpg`에는 주문 외 상품(펩시 2개, 나랑드 사이다, 두 번째 신라면 컵, 두 번째 참치캔)도 찍혀 있으므로 이 설명은 사실과 다르다. Run 2에서 이 문구만 바로잡았다.
- 원래 Validator에는 지지 없는 상품을 '2층'으로 계산하는 버그가 있었다. 이를 수정한 뒤 `evaluate_run.py`로 이 실행을 다시 평가했다(API 재호출 없음, plan과 원문 응답은 그대로 유지).
