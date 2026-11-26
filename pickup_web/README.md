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
| 가상 | `false` | 15종 | 로봇은 건너뛰고 **직원이 준비**. 완료 상태 JSON의 `staff_items`로 전달 |

## 파일
| 파일 | 역할 |
|---|---|
| `data/products.json` | **상품 목록(24종)·속성·매대 위치를 한곳에서 관리.** 서버, 로봇 노드, 브라우저 데모가 모두 이 파일을 읽음 |
| `data/our_recs.json` | 상품별 추천 목록·대체 상품·인기도 (Instacart 데이터로 계산, 아래 참고) |
| `data/rec_scores.json` | 코너(aisle) 단위 점수 원본 (지금은 사용하지 않음, 참고용) |
| `data/aisle_index.json`, `data/co_counts_small.npy`, `data/basket_size_comparison.csv` | 추천 계산 중간 결과 (코너 목록, 코너 동시 구매 수, 장바구니 크기별 비교) |
| `recommender.py` | 추천 로직 (`Recommender.home / for_basket / substitute`). 서버와 로봇 노드가 사용 |
| `web_server.py` | FastAPI 서버 + ROS2 브릿지. 재고 DB(`inventory.json`) 관리, `/data/*` 로 데이터 파일 제공 |
| `static/index.html` | 손님 주문 화면 + 직원 재고 화면 (탭으로 전환) |
| `static/manifest.json`, `static/icon.png` | 휴대폰 "홈 화면에 추가"용 (PWA) |
| `robot_order_node.py` | 로봇 쪽 주문 처리 노드 뼈대. TODO 자리에 기존 robot_control 함수 연결 |

## 실행
```bash
pip install fastapi uvicorn          # 컨테이너 안이면 --break-system-packages

# 1) 로봇 없이 웹만 시연 (데모 로봇이 흉내 냄)
python3 web_server.py --mock

# 2) 로봇 연결 (ROS2 환경을 source 한 터미널에서)
python3 web_server.py                # 터미널 1
python3 robot_order_node.py          # 터미널 2
```
- 손님 화면: `http://<PC IP>:8000/`
- 직원 화면: `http://<PC IP>:8000/#staff`
- 같은 와이파이의 휴대폰에서 접속 → 공유 메뉴 → **홈 화면에 추가** 하면 앱처럼 열립니다.
- `index.html`만 브라우저로 열어도 서버 없이 **브라우저 데모**로 동작합니다.

## ROS2 토픽 (모두 `std_msgs/String`, 내용은 JSON)
| 토픽 | 방향 | 예시 |
|---|---|---|
| `/order` | 웹 → 로봇 | `{"order_id":3,"items":{"tuna_can":2,"milk_pack":1}}` |
| `/order_choice` | 웹 → 로봇 | `{"order_id":3,"choice":"substitute","substitute":"spam_can"}` |
| `/scan_request` | 웹 → 로봇 | `{"reason":"manual"}` |
| `/order_status` | 로봇 → 웹 | `{"state":"picking","current":"tuna_can","done":1,"total":3,...}` |
| `/vision_count` | 로봇 → 웹 | `{"tuna_can":1,"spam_can":3}` |

`state` 값: `checking`(재고 확인) → `need_choice`(재고 부족, 손님 선택 대기) → `picking` → `done` / `cancelled` / `error`

## 연결 팁
- 상품 `id`(tuna_can, spam_can …)를 **YOLO 클래스 이름과 똑같이** 맞추면 변환 코드가 필요 없습니다.
- `robot_order_node.py`는 주문을 스레드에서 처리합니다. 담는 도중에도 손님 선택(`/order_choice`)을 받기 위해서입니다.
  DSR 모션 함수가 같은 노드의 spin과 충돌하면 `MultiThreadedExecutor`를 쓰거나 모션 전용 노드를 따로 두세요.
- 재고는 `inventory.json`에 저장됩니다. 지우면 `data/products.json` 기본값으로 초기화됩니다.
- 완료 상태(`state: "done"`)에는 `picked`(로봇이 담은 상품) 외에 `staff_items`(직원이 준비할 가상 상품, 예: `{"cola": 1}`)가 함께 옵니다.

## 추천
| API | 설명 |
|---|---|
| `GET /api/recommend/home` | 첫 화면 "오늘의 추천" 4개. 인기도 순, 재고 0 제외. 20% 확률로 `new_item`을 한 자리에 끼움 (콜드 스타트 실험) |
| `GET /api/recommend?basket=a,b` | 장바구니 기반 "함께 사면 좋아요" 3개 |

응답: `{"items": [{"id", "name", "emoji", "reason", "level"}]}`

`for_basket` 규칙
1. 장바구니 각 상품의 `recommend`를 합침 (같은 상품은 lift가 가장 높은 것만). 3개가 안 차면 인기 상품(`popular`)으로 채움
2. 정렬: `strong` → `weak` → `popular`, 같은 단계 안에서는 lift(없으면 인기도) 높은 순
3. 이미 담은 상품, 재고 0인 상품 제외. 같은 코너(aisle) 상품(참치캔·스팸)은 하나만 — 재고 많은 쪽
4. 문구: `strong` "같이 사는 비율 4.5배" / `weak` "이런 상품은 어때요?" / `popular` "인기 상품"

| level | 기준 |
|---|---|
| `strong` | lift ≥ 1.2 |
| `weak` | 1.1 ≤ lift < 1.2 |
| `popular` | 연관 상품이 부족할 때 인기 상품으로 채움 |

`our_recs.json`에 `level`이 없으면 lift 값으로 위 기준에 따라 판정합니다.

재고 부족 시 대체 상품은 `our_recs.json`의 `substitute` 중 재고 있는 첫 상품입니다.
점수 계산은 `Recommender._score()` 하나에 모아 두었으므로, 나중에 밴딧(클릭·구매 반응)을 붙일 때 이 함수만 바꾸면 됩니다.

### 추천 데이터 출처
- **Instacart** 공개 주문 데이터(prior orders)를 사용했습니다. 개별 상품 대신 **코너(aisle)** 단위로 묶어, 우리 편의점 상품을 비슷한 코너에 대응시켰습니다 (`products.json`의 `aisle`).
- 장보기 대량 주문이 결과를 왜곡하지 않도록 **서로 다른 코너가 2~5개인 주문만** 사용했습니다 (약 117만 건, `basket_size_comparison.csv` 참고).
- 두 코너 A, B의 연관도는 **lift = P(A와 B를 같이 삼) / (P(A) × P(B))**. 1보다 크면 우연보다 자주 같이 산다는 뜻입니다. 동시 구매 수가 적은(200건 미만) 쌍은 제외했습니다.
- `popularity` = 그 코너가 포함된 주문 비율.
- 손님 개인 기록은 쓰지 않습니다.

## 상품 바꾸기
`data/products.json` 하나만 고치면 서버·로봇 노드·브라우저 데모에 함께 반영됩니다.

| 필드 | 뜻 |
|---|---|
| `products[].id` / `name` / `emoji` / `price` | 상품 id(=YOLO 클래스 이름) / 이름 / 아이콘 / 가격(원) |
| `products[].stock` / `warehouse` | 처음 진열 재고 / 창고 재고 |
| `products[].category` | 손님 화면 카테고리 탭: 식품 / 음료 / 간식 / 생활용품 (기타는 "전체"에만) |
| `products[].aisle` | Instacart 코너 이름. `our_recs.json`과 같은 코너 중복 제거에 사용 |
| `products[].robot` | `true` 실물(로봇이 집음) / `false` 가상(직원 준비, 손님 화면 태그: 직원 준비) |
| `products[].shelf` | 매대 위치 `"upper"` / `"lower"` (실물만. 로봇 노드의 `SHELF_VIEW_POSE`가 이 값으로 만들어짐) |
| `products[].fragile` | 깨지기 쉬움 → 약하게·천천히 집기 (직원 화면 태그: 깨짐주의) |
| `products[].deformable` | 모양이 변함 (직원 화면 태그: 말랑) |
| `products[].registered` | `false`면 처음 보는 상품 실험용 (직원 화면 태그: 미등록) |

대체 상품·함께 사면 좋은 상품은 `data/our_recs.json`에서 관리합니다.

> ⚠️ **상품을 바꾸면 `inventory.json`을 지울 것.** `inventory.json`에는 예전 상품의 재고(진열·창고)가 남아 있어 새 상품 목록과 맞지 않습니다. 서버는 상품 정보는 `products.json`에서, 재고 숫자만 `inventory.json`에서 가져오지만, 재고를 처음 값으로 되돌리려면 지워야 합니다.
>
> 브라우저 데모는 `/data/products.json`, `/data/our_recs.json`을 불러오고, 실패하면(파일로 직접 열기 등) `index.html` 안의 `FALLBACK`, `FALLBACK_RECS`를 씁니다. 이 기본값도 같이 맞춰 두면 좋습니다.
