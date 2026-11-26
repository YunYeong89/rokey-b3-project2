# 로키마트 픽업 주문 웹

손님이 웹에서 주문하면 로봇이 매대에서 재고를 확인하고 상품을 바구니에 담습니다.
직원 화면에서는 재고를 **창고 / 진열 / 카메라** 세 가지로 나눠 봅니다.

| 구분 | 필드 | 뜻 |
|---|---|---|
| 창고 재고 | `warehouse` | 매대에 아직 안 꺼낸 재고. **입고 +5** 버튼으로 늘어남 |
| 진열 재고 | `stock` | 매대에 놓인 재고. 손님 화면의 "남은 n개", 로봇이 집는 대상. **진열 +1**은 창고에서 1개를 꺼내 옮김 |
| 카메라 | (`/vision_count`) | 로봇이 매대를 찍어 센 개수. 진열 재고와 다르면 "불일치", **카메라값 반영**으로 진열 재고를 고침 |

상태: 진열 재고가 `low_stock` 이하이면 **보충 필요**, 이때 창고 재고도 0이면 **발주 필요**

## 파일
| 파일 | 역할 |
|---|---|
| `products.json` | **상품 목록·속성·매대 위치·대체 상품·추천 규칙·보충 기준을 한곳에서 관리.** 서버, 로봇 노드, 브라우저 데모가 모두 이 파일을 읽음 |
| `web_server.py` | FastAPI 서버 + ROS2 브릿지. 재고 DB(`inventory.json`)와 추천 기능 포함 |
| `static/index.html` | 손님 주문 화면 + 직원 재고 화면 (탭으로 전환) |
| `static/products.json` | `../products.json`을 가리키는 심볼릭 링크 (정적 서버로 열 때 브라우저 데모용) |
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
- 재고는 `inventory.json`에 저장됩니다. 지우면 `products.json` 기본값으로 초기화됩니다.
- 추천은 고정 규칙(`products.json`의 `pairs`) + 주문 기록의 동시 구매 빈도로 계산합니다. 손님 개인 기록은 쓰지 않습니다.

## 상품 바꾸기
`products.json` 하나만 고치면 서버·로봇 노드·브라우저 데모에 함께 반영됩니다.

| 필드 | 뜻 |
|---|---|
| `products[].stock` / `warehouse` | 처음 진열 재고 / 창고 재고 |
| `products[].shelf` | 매대 위치 `"upper"` / `"lower"` (로봇 노드의 `SHELF_VIEW_POSE`가 이 값으로 만들어짐) |
| `products[].fragile` | 깨지기 쉬움 → 약하게·천천히 집기 (직원 화면 태그: 깨짐주의) |
| `products[].deformable` | 모양이 변함 (직원 화면 태그: 말랑) |
| `products[].registered` | `false`면 처음 보는 상품 실험용 (직원 화면 태그: 미등록) |
| `substitute` | 재고 부족 시 제안할 대체 상품 |
| `pairs` | 함께 사면 좋은 상품 (추천) |
| `low_stock` | 이 개수 이하이면 보충 필요 |

> ⚠️ **상품을 바꾸면 `inventory.json`을 지울 것.** 서버는 `inventory.json`이 있으면 그 안의 예전 상품 목록을 그대로 쓰기 때문에 새 상품이 반영되지 않습니다.
>
> `index.html` 안의 `FALLBACK`(서버도 `products.json`도 없을 때 쓰는 기본값)도 같이 맞춰 두면 좋습니다.
