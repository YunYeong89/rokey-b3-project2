# 로키마트 픽업 주문 웹

손님이 웹에서 주문하면 로봇이 매대에서 재고를 확인하고 상품을 바구니에 담습니다.
직원 화면에서는 재고를 **창고 / 진열 / 카메라** 세 가지로 나눠 봅니다.

| 구분 | 필드 | 뜻 |
|---|---|---|
| 창고 재고 | `warehouse` | 매대에 아직 안 꺼낸 재고. **입고 +5** 버튼으로 늘어남 |
| 진열 재고 | `stock` | 매대에 놓인 재고. 손님 화면의 "남은 n개", 로봇이 집는 대상. **진열 +1**은 창고에서 1개를 꺼내 옮김 |
| 카메라 | (`/vision_count`) | 로봇이 매대를 찍어 센 개수. 진열 재고와 다르면 "불일치", **카메라값 반영**으로 진열 재고를 고침 |

상태: 진열 재고가 `LOW_STOCK`(=1) 이하이면 **보충 필요**, 이때 창고 재고도 0이면 **발주 필요**

### 실물 / 가상 상품
| 구분 | `robot` | 개수 | 처리 |
|---|---|---|---|
| 실물 | `true` | 9종 | 로봇이 매대에서 카메라로 확인하고 집어서 바구니에 담음 |
| 가상 | `false` | 19종 | 로봇은 건너뛰고 **직원이 준비**. 완료 상태 JSON의 `staff_items`로 전달 |

## 폴더 구조
```
pickup_web/
├── web_server.py          웹 서버 실행 진입점 (옵션 처리 → pickup 패키지 조립)
├── robot_order_node.py    로봇 쪽 주문 처리 ROS2 노드 뼈대. TODO 자리에 기존 robot_control 함수 연결
├── pickup/                서버 코드 (패키지)
│   ├── config.py          경로·설정값 (픽업 만료, 실패 제한, LOW_STOCK …) 한곳에서 관리
│   ├── catalog.py         상품 목록 (data/products.json), 로봇/직원 상품 나누기
│   ├── inventory.py       재고(진열·창고·카메라)와 직원 알림 (Store)
│   ├── orders.py          주문·픽업 기록, 픽업 번호·확인번호 (Orders)
│   ├── history.py         고객 구매 이력·추천 반응 (SQLite). CLI: python3 -m pickup.history
│   ├── recommender.py     추천 (Recommender.home / for_basket / substitute). 로봇 노드도 사용
│   ├── robot.py           웹 ↔ 로봇 연결: RobotLink(ROS2) / MockRobot(데모)
│   ├── service.py         위를 하나로 묶은 서버 상태 (PickupService), 로봇 상태 처리
│   └── api/               FastAPI 라우터 (화면별)
│       ├── shop.py        손님 주문 (/api/products, order, status, choice)
│       ├── recommend.py   추천·반응 기록·고객 이력 (/api/recommend*, rec_event, rec_stats, customer)
│       ├── staff.py       직원 화면 (/api/inventory, scan, restock, pickups)
│       ├── pickup.py      QR 픽업 (/api/pickup/*)
│       ├── pages.py       화면 html, /data/* 공개 파일
│       └── schemas.py     요청 본문 모델
├── static/
│   ├── index.html         손님 주문 화면(+ 픽업 QR) + 직원 재고·픽업 대기 화면 (탭으로 전환)
│   ├── kiosk.html         가게 픽업 키오스크 (/kiosk). 카메라로 QR 인식 또는 번호 입력
│   ├── css/, js/          화면별 스타일·스크립트 (index.*, kiosk.*)
│   ├── vendor/jsQR.js     QR 인식 라이브러리 jsQR 1.4.0 (Apache-2.0). 가게 네트워크가 막혀도 되게 파일로 포함
│   └── manifest.json, icon.png   휴대폰 "홈 화면에 추가"용 (PWA)
├── data/
│   ├── products.json      상품 목록(28종)·속성·매대 위치. 서버, 로봇 노드, 브라우저 데모가 모두 읽음
│   ├── recommend/         추천 엔진이 읽는 분석 결과 (아래 "분석 데이터 파일")
│   └── runtime/           서버가 만드는 파일 (git 제외, /data 로 공개 안 함)
│       ├── inventory.json       재고. 지우면 products.json 기본값으로 초기화
│       ├── orders.json          주문·픽업 기록. 서버를 다시 켜도 수령 대기 주문 유지
│       └── customer_history.db  고객 구매 이력·추천 반응
└── analysis/              Colab 노트북(01~04)과 전체 분석 결과 (recommender/). 서버는 사용하지 않음
```
- `/data/*` 로는 `products.json`, `recommend/*` 만 내려받을 수 있음 (`runtime/` 은 404)
- 화면 html 은 css·js 를 상대 경로로 불러서, 서버 없이 `static/index.html` 을 직접 열어도 동작

## 실행
```bash
pip install fastapi uvicorn "qrcode[pil]"   # 컨테이너 안이면 --break-system-packages
# 또는 QR 패키지만 apt 로: sudo apt install python3-qrcode

# 1) 로봇 없이 웹만 시연 (데모 로봇이 흉내 냄)
python3 web_server.py --mock
python3 web_server.py --mock --pickup-expire-min 1   # 픽업 만료를 1분으로 (테스트용)

# 2) 로봇 연결 (ROS2 환경을 source 한 터미널에서)
python3 web_server.py                # 터미널 1
python3 robot_order_node.py          # 터미널 2
```
- 손님 화면: `http://<PC IP>:8000/`
- 직원 화면: `http://<PC IP>:8000/#staff`
- 픽업 키오스크: 가게 PC(로봇 PC)에서 `http://localhost:8000/kiosk` (아래 "QR 픽업" 참고)
- 같은 와이파이의 휴대폰에서 접속 → 공유 메뉴 → **홈 화면에 추가** 하면 앱처럼 열립니다.
- `index.html`만 브라우저로 열어도 서버 없이 **브라우저 데모**로 동작합니다.

## ROS2 토픽 (모두 `std_msgs/String`, 내용은 JSON)
| 토픽 | 방향 | 예시 |
|---|---|---|
| `/order` | 웹 → 로봇 | `{"order_id":3,"items":{"tuna_can":2,"milk_pack":1}}` |
| `/order_choice` | 웹 → 로봇 | `{"order_id":3,"choice":"substitute","substitute":"spam_can"}` |
| `/scan_request` | 웹 → 로봇 | `{"reason":"manual"}` |
| `/pickup` | 웹 → 로봇 | `{"order_id":3,"pickup_code":"A-017"}` 손님이 키오스크에서 수령 완료 (`--mock`이면 로그만) |
| `/order_status` | 로봇 → 웹 | `{"state":"picking","current":"tuna_can","done":1,"total":3,...}` |
| `/vision_count` | 로봇 → 웹 | `{"tuna_can":1,"spam_can":3}` |

`state` 값: `checking`(재고 확인) → `need_choice`(재고 부족, 손님 선택 대기) → `picking` → `done` / `cancelled` / `error`

## 연결 팁
- 상품 `id`(tuna_can, spam_can …)를 **YOLO 클래스 이름과 똑같이** 맞추면 변환 코드가 필요 없습니다.
- `robot_order_node.py`는 주문을 스레드에서 처리합니다. 담는 도중에도 손님 선택(`/order_choice`)을 받기 위해서입니다.
  DSR 모션 함수가 같은 노드의 spin과 충돌하면 `MultiThreadedExecutor`를 쓰거나 모션 전용 노드를 따로 두세요.
- 재고는 `data/runtime/inventory.json`에 저장됩니다. 지우면 `data/products.json` 기본값으로 초기화됩니다.
- 완료 상태(`state: "done"`)에는 `picked`(로봇이 담은 상품) 외에 `staff_items`(직원이 준비할 가상 상품, 예: `{"cola": 1}`)가 함께 옵니다.
- 픽업 번호는 서버가 만들므로 로봇 노드는 `done`만 보내면 됩니다. `/pickup`을 받는 `on_pickup()`은 TODO(바구니를 픽업대로 옮기기)입니다.

## QR 픽업
```
손님 주문 → 로봇 담기(done) → 서버가 픽업 번호(A-017)·QR 발급 → 손님 화면에 QR 표시
→ 손님이 가게 키오스크 카메라에 QR → 키오스크가 상품·바구니 안내 → "수령 완료" (같은 QR 다시 못 씀)
```

### 주문 상태 (`data/runtime/orders.json`의 `status`)
| status | 뜻 |
|---|---|
| `in_progress` | 로봇이 확인·담는 중 (로봇 상태 `checking`/`need_choice`/`picking`) |
| `ready_for_pickup` | 로봇이 `done`을 보냄 → 픽업 번호·token 발급, 수령 대기 |
| `picked_up` | 키오스크에서 수령 완료 |
| `expired` | 준비 후 `PICKUP_EXPIRE_MIN`(=30)분 안에 안 가져감. 직원 화면에 "만료 · 바구니 정리"로 표시 |
| `cancelled` / `error` | 로봇이 취소·오류를 보냄 |

- 저장 필드: `order_id, pickup_code, pickup_token, items, picked, staff_items, status, created_at, ready_at, picked_up_at, expires_at` (+ 손님 확인용 `client_secret`, 시간은 유닉스 초)
- `pickup_code`: `A-001` ~ `A-999`를 돌아가며 사용 (수령 대기 중인 번호는 건너뜀). 화면 표시·바구니 라벨용
- `pickup_token`: `secrets.token_urlsafe(16)`. **QR 내용은 `ROKEY-PICKUP:{pickup_token}`** 뿐이고 `order_id`는 넣지 않음
- 확인번호 4자리: `pickup_token`의 SHA-256에서 만든 숫자. QR이 안 될 때 픽업 번호와 함께 입력
- 만료 시간은 `pickup/config.py`의 `PICKUP_EXPIRE_MIN` 또는 `--pickup-expire-min`으로 변경
- 서버를 다시 켜면 `ready_for_pickup` 주문은 그대로 유지되고, 담는 중(`in_progress`)이던 주문은 이어서 할 수 없으므로 `error`로 바뀜
- `data/runtime/orders.json`을 지우면 기록과 주문 번호가 처음부터 다시 시작 (손님 화면의 저장된 주문도 사라짐)

### 픽업 API
| API | 설명 |
|---|---|
| `POST /api/order` | 응답에 `client_secret` 추가: `{"order_id": 3, "client_secret": "..."}` |
| `GET /api/pickup/order/{order_id}?secret=` | 손님 화면용 내 주문 상태 (픽업 번호, 확인번호 `check`, 만료 시각, 로봇 진행 상황). secret 이 틀리면 403 |
| `GET /api/pickup/qr/{order_id}.png?secret=` | QR PNG. `ready_for_pickup`일 때만, `client_secret`이 맞을 때만 (없거나 틀리면 403, 준비 전·수령 후 409) |
| `POST /api/pickup/verify` | `{"token": "..."}` (QR 내용 전체를 보내도 됨) → 성공 `{"ok": true, "order": {...}}` |
| `POST /api/pickup/manual` | `{"pickup_code": "A-017", "check": "1234"}` → verify 와 같음 |
| `GET /api/pickups` | 직원 화면용: 수령 대기 + 최근 3시간 안에 만료된 주문 |

verify / manual 실패는 **400** `{"ok": false, "reason": ..., "message": ...}`

| reason | 뜻 |
|---|---|
| `already_picked` | 이미 수령한 주문 |
| `expired` | 만료된 주문 |
| `not_ready` | 아직 로봇이 담는 중 |
| `unknown` | 없는 token / 번호·확인번호 불일치 / 취소된 주문 |

같은 IP에서 `unknown` 실패가 **1분에 10번**(`FAIL_MAX`, `FAIL_WINDOW_SEC`)을 넘으면 잠시 **429** `too_many`.
(무차별 대입만 막으려는 것이라 `already_picked` 등은 세지 않음 → 키오스크가 수령한 QR을 다시 읽어도 키오스크가 막히지 않음)

### 손님 화면
- 주문하면 `order_id`, `client_secret`을 브라우저 `localStorage`에 저장 → 새로고침해도 진행 상황·QR이 다시 열림
- 담기가 끝나면 큰 픽업 번호, QR, 확인번호, 만료까지 남은 시간, 로봇이 담은 상품 / 직원이 준비할 상품 표시
- 키오스크에서 수령하면 손님 화면도 "수령 완료"로 바뀜 (2초마다 확인)
- 상단 **🎫 내 픽업** 버튼: 저장된 주문의 QR 화면 다시 열기
- 브라우저 데모(서버 없이 `index.html`만 열기)에서는 픽업 번호만 흉내 내고 QR·키오스크는 동작하지 않음

### 키오스크 (`/kiosk`)
- 가로 화면 기준. 카메라 영상에서 QR을 자동 인식 (브라우저 기본 `BarcodeDetector`, 없으면 `static/vendor/jsQR.js`)
- 성공: 초록 화면에 픽업 번호, 상품 목록, "바구니 A-017을 가져가세요", 직원 준비 상품이 있으면 "직원에게 ○○를 받아 가세요"
- 실패: 이유별 빨간 화면. 3초 뒤 다시 스캔 대기
- 같은 QR이 계속 보이면 6초 동안 다시 확인하지 않음 (verify 한 번만 호출)
- QR이 안 되면 오른쪽에 **픽업 번호 + 확인번호 4자리** 입력

> ⚠️ **카메라는 보안 연결(https)이나 `localhost`에서만 열립니다.** 키오스크는 가게 PC(로봇 PC)에 웹캠을 연결하고
> 그 PC의 브라우저에서 `http://localhost:8000/kiosk`로 여세요. 다른 기기(태블릿 등)에서 `http://<PC IP>:8000/kiosk`로 열면
> 카메라가 막힐 수 있습니다 — 그때는 화면의 **수동 입력**을 쓰세요.

## 추천 (Switching Hybrid)
추천 목적에 따라 화면별로 알고리즘을 나눕니다. 가중합(Weighted Hybrid)은 쓰지 않습니다.

| 화면 | 대상 | 알고리즘 | 화면 문구 |
|---|---|---|---|
| 첫 화면 | 구매 이력 있는 고객 | **R** 재구매 Top 3 (모자라면 인기 상품으로 보충) | 🔁 평소 자주 구매하시는 상품 |
| 첫 화면 | 신규 고객 / 비회원 | **Popularity** Top 3 | 🔥 인기 상품 추천 |
| 장바구니 | 장바구니에 상품이 있을 때 | **S** 같은 코너 → **C** 다른 코너 → 인기 상품, Top 3 | 💡 함께 구매하면 좋은 상품 |
| 재고 부족 | 로봇이 재고 부족 발견 | `our_recs.json`의 `substitute` (기존과 같음) | 부족한 만큼 ○○로 바꿀게요 |

### 왜 이렇게 나눴나 (Colab 오프라인 실험, `pattern_analysis_v3`)
Instacart 주문 76,781건 (Validation 38,390 / Final Test 38,391), 11개 알고리즘, 가중치 66개 조합 Grid Search.

| 알고리즘 | Hit@3 | 재구매 상품 | 신규 구매 상품 |
|---|---:|---:|---:|
| R | **62.94%** | **75.91%** | 4.01% |
| Popularity | 35.92% | 39.42% | 20.02% |
| S | 32.70% | 35.45% | **20.22%** |
| C | 23.04% | 24.19% | 17.81% |
| 기존 Lift 비교군 | 18.83% | | |
| Random | 12.52% | | |

- 최적 가중치가 R=1.0, S=0, C=0 (`hybrid_weights.json`) → 섞으면 오히려 손해. 정답의 약 82%가 재구매 상품이라 첫 화면은 R.
- R은 새 상품을 거의 못 맞힘(4.01%) → 새 상품 탐색은 장바구니에서 S, C가 따로 맡음.
- 신규 고객 모의 실험에서는 Popularity(35.92%)가 가장 좋음 → 이력 없으면 인기 상품.
- ⚠️ 화면을 나누는 방식 자체는 **아직 검증되지 않은 설계**라서 노출·클릭·구매를 기록합니다 (아래 "추천 반응 기록").

### 점수 계산
- **R** = 이 고객이 그 상품을 산 주문 수 / 이 고객의 구매 완료 주문 수. 한 주문에서 여러 개 사도 1번. 같은 R이면 최근에 산 것, 그다음 인기도 순. 문구: "주문 3번 중 2번 구매"
- **S, C** 연관 점수 = `0.5 × confidence + 0.5 × max(0, 1 − 1/lift)` (`hybrid_scores_optimized.json`의 `score`, `lift_weight`=0.5). 동시 구매 200건(`min_count`) 이상인 관계만 들어 있음
  - **S** (상품 → 상품, `s_pairs` 8개): 초콜릿↔다크초콜릿, 과자↔나초·토르티야 칩, 콜라↔오렌지 탄산음료, 요구르트↔딸기 요구르트. 문구: "콜라와 같은 코너 · 함께 구매 8.3배"
  - **C** (코너 → 코너, `c_pairs`): lift > 1인 관계만 사용 (434개 중 120개. 나머지는 오히려 덜 같이 사는 관계). 대상 코너에서는 재고 있는 가장 인기 상품 1개. 문구: "콜라와 함께 구매 1.7배"
  - 장바구니 **전체**를 봄: 여러 상품과 관련 있으면 점수를 더함. 순서는 S → C → 인기 상품
- **Popularity** = 그 상품군이 들어 있는 주문 비율 (`hybrid_scores_optimized.json`의 `popularity`, 없으면 `our_recs.json`)

### 공통 규칙
- 품절(진열 재고 0), 이미 장바구니에 담은 상품, 판매 목록(`products.json`)에 없는 상품 제외
- 같은 상품 중복 없음, 한 목록에 같은 코너는 1개, 최대 3개
- 장바구니가 비면 장바구니 추천 없음 (영역이 숨겨짐)
- 분석 파일이 없거나 깨지면 인기 상품(그것도 없으면 재고 순)으로, DB 오류·추천 오류가 나도 빈 목록으로 응답하고 주문은 계속 됨
- 순서는 `Recommender._rank()` 하나에서 정함 → 나중에 밴딧(클릭·구매 반응)을 붙일 때 여기만 바꾸면 됨
- `new_item`(분석 데이터 없는 실험 상품)은 인기 상품이 모자랄 때만 나옴. 예전의 "20% 확률로 끼워 넣기"는 `NEW_ITEM_RATE = 0.0`으로 꺼 둠

### API
| API | 설명 |
|---|---|
| `GET /api/recommend/home?customer_id=&basket=` | 첫 화면. `{"items", "mode": "personal"/"popular", "title", "rec_id", "n_orders"}` |
| `GET /api/recommend?basket=a,b&customer_id=` | 장바구니. `{"items", "title", "rec_id"}` |
| `POST /api/order` | `customer_id`(선택), `rec_attrib`(추천을 눌러 담은 상품 `{pid: {slot, algo, rec_id, rank}}`) 추가 |
| `GET /api/customer?customer_id=` | 고객 구매 이력·상품별 R 점수 |
| `POST /api/rec_event` | 추천 클릭 기록 `{"event":"click","slot":"home"/"cart","product_id","algo","rec_id","rank","customer_id"}` |
| `GET /api/rec_stats` | 화면(slot)·알고리즘별 노출 / 클릭 / 구매 수, CTR, 구매율 |

추천 항목: `{"id", "name", "emoji", "reason", "algo": "R"/"S"/"C"/"popular", "level", "score", "basis": {"from", "confidence", "lift"}}`
(`level`은 예전 화면 색 이름과 호환: S=`strong`, C=`weak`, 인기=`popular`, R=`personal`)

### 고객 구매 이력 (`pickup/history.py`, `data/runtime/customer_history.db`)
- **고객 식별(시연용)**: 손님 화면 오른쪽 위 **👤 로그인** → 고객번호(한글·영문·숫자 20자 이내) 입력. 비밀번호 없이 번호만, 브라우저에 기억. 입력 안 하면 비회원 = 인기 상품.
  고객번호만 알면 누구나 그 이력을 볼 수 있으므로 **전화번호 같은 개인정보는 쓰지 말 것** (실서비스라면 로그인 필요)
- `purchases` 테이블: `customer_id, order_id, order_uid, product_id, quantity, purchased_at, order_status`
  - **로봇이 담기를 끝낸(done) 주문만** 저장. 취소(`cancelled`)·실패(`error`)는 저장하지 않음
  - 저장하는 상품은 실제로 준비된 상품(`picked` + `staff_items`, 대체 상품 포함)
  - `order_status`: `completed` → 수령하면 `picked_up`, 안 가져가서 만료되면 `expired` (**R 계산에서 뺌**)
  - 같은 주문은 `order_uid`로 한 번만 저장 (로봇이 `done`을 두 번 보내거나, `orders.json`을 지워 주문 번호가 다시 1부터 시작해도 안전)
- Instacart의 고객 ID·개인 구매 이력은 쓰지 않음. Instacart는 인기도와 S·C 연관성 계산에만, 개인화(R)는 이 웹의 실제 고객 이력만 사용
- DB를 지우면 모든 고객이 신규 고객(인기 상품)으로 돌아감

```bash
python3 -m pickup.history seed        # 시연용 샘플: 1234(우유·시리얼), 5678(콜라·과자)
python3 -m pickup.history show 5678   # 이력과 R 점수
python3 -m pickup.history events      # 추천 노출/클릭/구매 집계
```

### 추천 반응 기록 (`rec_events` 테이블)
| event | 언제 |
|---|---|
| `impression` | 서버가 추천 목록을 줄 때 (항목마다, 순위 포함) |
| `click` | 손님이 추천 카드·칩을 눌러 담을 때 |
| `purchase` | 추천에서 담은 상품이 들어 있는 주문을 로봇이 완료했을 때 (주문의 `rec_attrib`로 연결) |

`slot`(home / cart)과 `algo`(R / S / C / popular)별로 모아서 화면 분리 방식이 효과가 있는지 비교할 수 있습니다 (`GET /api/rec_stats`).

### Mock 모드로 추천 시험
```bash
python3 -m pickup.history seed
python3 web_server.py --mock
```
1. 처음 접속 → "🔥 인기 상품 추천"
2. 👤 로그인 → `5678` → "🔁 평소 자주 구매하시는 상품" (콜라, 과자, 견과류)
3. 콜라를 담으면 아래에 "💡 함께 구매하면 좋은 상품" (오렌지 탄산음료 S, 생수·초콜릿 C)
4. 주문하면(데모 로봇이 완료) 이력이 쌓여 첫 화면 R이 바뀜. 새 번호로 로그인해 주문하면 신규 → 기존 고객 전환을 볼 수 있음

브라우저 데모(서버 없이 `index.html`만 열기)는 구매 이력이 없어 첫 화면은 인기 상품, 장바구니는 예전 `our_recs.json` 방식의 간이 추천입니다.

### 분석 데이터 파일 (Colab `pattern_analysis_v3`)
`data/recommend/` 에 있는 4개만 서버가 읽고, 나머지는 `analysis/recommender/` 에 있습니다 (참고용).
| 파일 | 내용 | 사용 |
|---|---|---|
| `data/recommend/hybrid_scores_optimized.json` | 27개 상품군의 코너 id, 인기도, S·C 점수(`s_pairs`, `c_pairs`), 가중치 | 추천 엔진이 읽음 |
| `data/recommend/same_aisle_scores.json`, `cross_aisle_scores.json` | 관계별 count, confidence, lift (scope: all / aisles_1_5 / aisles_2_5) | 추천 이유(배수), C의 lift > 1 거르기 |
| `analysis/recommender/evaluation_v3/hybrid_weights.json` | Grid Search 최적 가중치 R=1, S=0, C=0 | 참고 (Switching 근거) |
| `analysis/recommender/pattern_analysis_v3/product_mapping.csv` | Instacart 상품 12,972개 → 우리 상품 id 매핑 | 참고 (일관성 확인) |
| `analysis/recommender/pattern_analysis_v3/repurchase_summary.csv` | 상품군별 재구매 고객 비율 | 참고 |
| `data/recommend/our_recs.json` | 예전 추천 결과 | 대체 상품, 인기도 예비값 |
| `analysis/recommender/rec_scores.json` 등 | 예전 코너 단위 계산 결과 | 사용 안 함 (참고용) |

- 27개 분석 상품 id는 모두 `products.json`에 있음 (23개 기존 + 가상 4종 `tortilla_chips`, `dark_chocolate`, `strawberry_yogurt`, `orange_soda`)
- 새 4종은 기존 상품과 같은 Instacart 코너로 세분화됨 (과자 107, 초콜릿 45, 콜라 77, 요구르트 120) → `products.json`의 `aisle` 이름도 같게 맞춤
- `product_mapping.csv`의 상품별 코너 id와 `hybrid_scores_optimized.json`의 `item_aisle`이 모두 일치함을 확인
- `new_item`은 분석 데이터에 없음 (인기도 0)

## 상품 바꾸기
`data/products.json` 하나만 고치면 서버·로봇 노드·브라우저 데모에 함께 반영됩니다.

| 필드 | 뜻 |
|---|---|
| `products[].id` / `name` / `emoji` / `price` | 상품 id(=YOLO 클래스 이름) / 이름 / 아이콘 / 가격(원) |
| `products[].stock` / `warehouse` | 처음 진열 재고 / 창고 재고 |
| `products[].category` | 손님 화면 카테고리 탭: 식품 / 음료 / 간식 / 생활용품 (기타는 "전체"에만) |
| `products[].aisle` | Instacart 코너 이름 (사람이 읽는 용도. 추천은 `hybrid_scores_optimized.json`의 `item_aisle` 코너 id 사용) |
| `products[].robot` | `true` 실물(로봇이 집음) / `false` 가상(직원 준비, 손님 화면 태그: 직원 준비) |
| `products[].shelf` | 매대 위치 `"upper"` / `"lower"` (실물만. 로봇 노드의 `SHELF_VIEW_POSE`가 이 값으로 만들어짐) |
| `products[].fragile` | 깨지기 쉬움 → 약하게·천천히 집기 (직원 화면 태그: 깨짐주의) |
| `products[].deformable` | 모양이 변함 (직원 화면 태그: 말랑) |
| `products[].registered` | `false`면 처음 보는 상품 실험용 (직원 화면 태그: 미등록) |

대체 상품은 `data/recommend/our_recs.json`, 함께 구매 추천·인기도는 `data/recommend/hybrid_scores_optimized.json`에서 관리합니다. 새 상품을 추천에 넣으려면 Colab 분석에도 같은 id로 추가하세요 (없으면 추천에는 인기도 0으로만 나옴).

> ⚠️ **상품을 바꾸면 `data/runtime/inventory.json`을 지울 것.** `inventory.json`에는 예전 상품의 재고(진열·창고)가 남아 있어 새 상품 목록과 맞지 않습니다. 서버는 상품 정보는 `products.json`에서, 재고 숫자만 `inventory.json`에서 가져오지만, 재고를 처음 값으로 되돌리려면 지워야 합니다.
>
> 브라우저 데모는 `/data/products.json`, `/data/recommend/our_recs.json`을 불러오고, 실패하면(파일로 직접 열기 등) `static/js/index.js` 안의 `FALLBACK`, `FALLBACK_RECS`를 씁니다. 이 기본값도 같이 맞춰 두면 좋습니다.
