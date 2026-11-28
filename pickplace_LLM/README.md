# pickplace_LLM: 음성 주문 + 카메라(SAM3 + DINOv3)로 집어 비닐봉투에 넣는 로봇

**"자비스, 콜라 두 개랑 라면 하나 추가해줘"** 라고 말하면, 로봇이 카메라로 매대를 보고(SAM3 로 물건 나누기 + DINOv3 로 상품 맞히기)
**① 가까운 것 → ② 무거운 것** 순서로 집어 비닐봉투 빈자리에 넣습니다. **"콜라 하나 빼줘"** 하면 봉투에서 꺼내 탁자 위 '내려놓는 곳' 에 둡니다.

- 이 폴더 **안에서만** 돕니다. 폴더 밖에서 쓰는 것은 ROS 2 작업 공간 `~/cobot2_ws`(두산 m0609 · RG2 · RealSense)와 파이썬 패키지, 모델 캐시뿐입니다.
- `LLM_sam3` 와 `pickplace_sam3` 는 같이 다녀야 합니다. LLM_sam3 가 `../pickplace_sam3` 를 씁니다.
- ⚠️ 기본 인식기(SAM3 + DINOv3)는 **진짜 모델 가중치로 아직 돌려 보지 않았습니다.** 처음에는 아래 4절 `realsense_check.py` 로 꼭 확인하세요.
  대체 인식기로 Grounding DINO(`--detector gdino`, 원래 pickplace 방식 — 가상 로봇 시험으로 확인됨)가 있습니다.

---

## 0. 폴더 구조

```
pickplace_LLM/
├── README.md
├── LLM_sam3/                     음성 → 명령 → 판단 → pickplace_sam3 에 '넣어 줘 / 꺼내 줘'
│   ├── main.py                   ▶ 실행: 음성(또는 --text 키보드) 주문
│   ├── order_voice_node.py       ▶ 실행: 주문 확인(QR) 음성 안내 ROS2 노드 (7절)
│   ├── config.py                 ⚙ 품목·호출어·녹음·안내 음성·QR 설정
│   ├── .env.example              🔑 복사해서 .env (OPENAI_API_KEY)
│   ├── requirements.txt
│   ├── voice/                    호출어 "자비스" · 녹음 · Whisper · 안내 음성
│   └── logic/
│       ├── command_parser.py     문장 → [품목, 개수, 행동] (gpt-4o, 실패하면 규칙)
│       ├── store_robot.py        판단 (판매 품목? 개수? 봉투에 있나?) → 넣기/꺼내기 요청
│       ├── pickplace_bridge.py   pickplace_sam3 를 따로 띄운 작업 프로세스에서 돌리고 명령을 주고받음
│       └── order_pickup.py       QR 주문 확인 흐름
└── pickplace_sam3/               카메라 인식 + 로봇 동작
    ├── main.py                   ▶ 실행: 키보드 주문 (음성 없이)
    ├── teach.py                  ▶ 실행: 위치 티칭 (2절)
    ├── realsense_check.py        ▶ 실행: 카메라 한 장으로 인식 확인 · 갤러리 사진 모으기 (3·4절)
    ├── config.py                 ⚙ 인식기·위치·봉투·집기 설정 (9절)
    ├── requirements.txt
    ├── vision/
    │   ├── sam3_dinov3.py        SAM3(마스크) + DINOv3(갤러리와 비교) 인식기 — 기본
    │   ├── detector.py           공통 결과 형식(Box) + Grounding DINO 인식기
    │   ├── external.py           팀원이 만든 인식 함수 연결 (8절)
    │   ├── factory.py            인식기 고르기 (--detector)
    │   ├── camera.py             RealSense ROS2 토픽
    │   └── geometry.py           픽셀·깊이 → 로봇 좌표
    ├── logic/
    │   ├── pick_place.py         매대 찍기 → 우선순위 → 집기 → 봉투 넣기 / 봉투에서 꺼내기
    │   ├── front_grasp.py        캔·컵라면 정면(수평) 집기, 눕혀 넣기
    │   ├── neighbors.py          옆 물건 치우기 · 가려진 물건 · 마스크로 폭 재기
    │   └── basket.py             봉투 높이 지도 · 빈자리 · 쌓기 · 정리
    ├── robot/                    두산 m0609 movej/movel + RG2 (ros / modbus)
    └── data/
        ├── T_gripper2camera.npy  핸드아이 캘리브레이션 결과 (mm)
        ├── products.json         상품 목록 (웹 pickup_web 과 같은 것)
        └── gallery/<상품 id>/    (3절에서 만듦) DINOv3 참조 사진
```

---

## 1. 처음 설치 (한 번)

```bash
cd ~/corecode/pickplace_LLM
pip install -r pickplace_sam3/requirements.txt -r LLM_sam3/requirements.txt
#   torchvision 은 깔린 torch 와 짝이 맞는 버전으로 (pytorch.org 의 표). SAM3·DINOv3 이미지 처리기가 씀
cp LLM_sam3/.env.example LLM_sam3/.env       # 열어서 OPENAI_API_KEY=sk-... 넣기 (git 에 안 올라감)
huggingface-cli login                        # 또는 hf auth login
```
- SAM3·DINOv3 는 Hugging Face 에서 **사용 승인**이 필요합니다: `facebook/sam3`, `facebook/dinov3-vits16-pretrain-lvd1689m` 페이지에서 승인 요청 → 로그인.
  처음 실행할 때 내려받아 `~/.cache/huggingface` 에 저장합니다. 다른 체크포인트를 쓰면 `pickplace_sam3/config.py` 의 `SAM3_MODEL` / `DINOV3_MODEL`.
- GPU 가 있으면 자동으로 씁니다 (`DEVICE = "auto"`). CPU 만 있으면 사진 한 장에 수십 초 걸릴 수 있습니다.

로봇 띄우기 (매번, **한 번만** 띄우기):
```bash
cd ~/cobot2_ws && source install/setup.bash
ros2 launch m0609_rg2_bringup bringup.launch.py mode:=real host:=192.168.1.100 camera:=true
```
다른 터미널에서는 항상 먼저 `source ~/cobot2_ws/install/setup.bash`. "Configured and activated dsr_controller2" 가 나온 뒤 실행합니다.

---

## 2. 위치 티칭 (`pickplace_sam3/teach.py`, 처음 한 번)

```bash
cd ~/corecode/pickplace_LLM/pickplace_sam3
python3 teach.py                     # upper → lower → 봉투촬영 → 봉투바닥 → 내려놓는곳
python3 teach.py 봉투바닥 내려놓는곳    # 일부만 다시
```
직접교시로 로봇을 옮기고 Enter (s: 건너뛰기, q: 저장 후 끝) → `positions.json` 에 저장되고 실행할 때 자동으로 읽습니다.

| 이름 | 무엇을 | 저장 값 |
|---|---|---|
| `upper` / `lower` | 카메라가 그 매대를 **정면에서 수평으로** 보는 자세 (캔·컵라면은 정면에서 집음) | joint |
| `봉투촬영` | 카메라가 봉투 바로 위에서 안쪽 전체를 내려다보는 자세 | joint |
| `봉투바닥` | 그리퍼를 아래로, 끝을 봉투 바닥 **중심**에 댄 자세 (z = 바닥 높이) | 좌표 |
| `내려놓는곳` | 봉투에서 꺼낸 상품을 놓을 **첫째 자리** — 그리퍼를 아래로, 끝을 탁자 면에 댄 자세 | 좌표 |

- 봉투 크기·깊이는 `config.py` 의 `BASKET_SIZE`(25x15cm), `CONTAINER_DEPTH`(150mm, 넘칠 때 `CONTAINER_MAX` 200mm), 기울기 `BASKET_YAW`.
- 내려놓는 곳 다음 자리는 `RETURN_STEP`(첫째 자리에서 y −130mm 씩) 간격으로 `RETURN_SLOTS`(3)자리.
  ⚠️ 쥔 채 옮기는 높이에서 팔이 다 펴지는 곳(로봇에서 약 650mm 넘게)은 특이점이라 멈춥니다. 로봇 가까이로 티칭하세요.
- 티칭하지 않은 위치가 있으면 실행할 때 알려 줍니다 — `pickplace_sam3/main.py` 는 계속할지 묻고, `LLM_sam3/main.py` 는 멈춥니다 (예시 좌표로 해 보려면 `--yes`).
- 처음 확인할 것: `GRIPPER_CLOSE_AXIS` (RG2 손가락이 닫히는 방향이 도구 좌표 x / y), `PRODUCT_SPECS` (상품 무게·크기 — 예시값),
  컨트롤러에 그리퍼 끝 TCP 가 설정돼 있는지 (핸드아이 캘리브레이션 때와 같은 TCP).

---

## 3. 갤러리 만들기 (SAM3 + DINOv3 를 쓸 때, 처음 한 번)

DINOv3 는 물건 조각을 **상품별 참조 사진(갤러리)** 과 비교해 어느 상품인지 정합니다. 매대에서 직접 찍은 사진이 가장 잘 맞습니다.

```bash
cd ~/corecode/pickplace_LLM/pickplace_sam3
python3 realsense_check.py --save-crops cola --loop        # 매대에 콜라(·펩시)만 올려 두고 Enter 마다 한 장 → data/gallery/cola/
python3 realsense_check.py --save-crops 컵라면 --loop      # 상품 id 또는 이름
```
- 상품마다 **10~30장**: 돌려 놓고, 자리·조명을 바꿔 가며. 다른 물건이 섞인 조각은 지우기. `q` 로 끝.
- 폴더 이름 = 상품 id: `cola`(코카콜라 + 펩시) `cider` `cup_ramen` `tuna_can` `spam_can` `snack_bag`(쫄병) `pepero`, 그 밖에 `milk_pack` `soap_box` `choco_box` `mini_tissue` `egg_pack`.
- 사진 특징(`data/gallery/embeddings.npz`)은 인식기가 시작할 때 **알아서** 계산합니다 (사진이 바뀌면 다시). 사진은 git 에 올리고 `embeddings.npz` 는 올리지 않습니다.

---

## 4. 인식 확인 (`realsense_check.py`, 로봇은 안 움직임)

```bash
python3 realsense_check.py                     # 모든 상품 기준
python3 realsense_check.py 콜라 2 컵라면 1      # 주문 기준 (집을 후보로 볼 상자)
python3 realsense_check.py --loop --robot      # Enter 마다 한 장 + 로봇 좌표도 표시
python3 realsense_check.py --detector gdino    # 다른 인식기로 비교
```
결과: 표(상자마다 인식된 상품·점수·잰 폭·깊이·가려짐·좌표) + 그림 `data/realsense_check.jpg`
(초록 = 주문 상품 후보 / 노랑 = 다른 상품 / 빨강 = 앞 물건에 가려짐 / 회색 = 모르는 물건). SAM3 마스크 테두리도 그려집니다.

잘 안 되면 (`pickplace_sam3/config.py` 2-1):

| 증상 | 바꿀 값 |
|---|---|
| 매대 판·봉투·손을 물건으로 잡음 | `SAM3_THRESHOLD` 올리기 (0.4), `SAM3_MAX_AREA` 내리기 (0.25), `SAM3_PROMPTS` |
| 물건을 못 나눔 | `SAM3_THRESHOLD` 내리기, `SAM3_PROMPTS` 에 단어 추가 (`"can", "cup", "box", "bottle", "pack"`) |
| 아는 상품인데 `?` | 갤러리 사진 추가 / `DINOV3_MIN_SIM` 내리기 (0.35) |
| 다른 상품으로 부름 | 그 상품들 갤러리 사진 추가. 폭이 다르면 로봇이 크기로 바로잡음 (`SIZE_CHECK`) |

---

## 5. 실행

### 음성 주문 (`LLM_sam3/main.py`)
```bash
cd ~/corecode/pickplace_LLM/LLM_sam3
python3 main.py --gripper ros                 # 음성 + bringup 그리퍼 서비스
python3 main.py                               # 음성 + 실제 RG2 직결 (Modbus)
python3 main.py --gripper ros --text          # 마이크 대신 키보드로 문장 입력
python3 main.py --gripper ros --text --rule   # OpenAI 없이 규칙 기반 해석
python3 main.py --gripper ros --detector gdino   # 인식기 바꾸기
```
1. `🎙 '자비스' 라고 불러주세요` → **"자비스"** → 🔊 "네, 말씀하세요." 가 끝나면 5초 안에 → **"콜라 두 개 추가해줘"**
   (한 번에 "자비스, 콜라 두 개 추가해줘" 도 됨)
2. 처음 명령 전에 로봇·카메라·인식기를 준비합니다 (모델 로드 — 처음엔 오래 걸림).
3. 키보드 모드(`--text`): 문장 입력, `n` = 새 봉투(넣은 기록 지우기), `r` = 내려놓는 곳을 치웠음, `q` = 끝.

| 옵션 | 뜻 |
|---|---|
| `--gripper ros` / `modbus` | bringup 의 `/onrobot/sendCommand` / 실제 RG2 에 Modbus 직결 (기본) |
| `--text` | 마이크 대신 키보드 |
| `--rule` | OpenAI 없이 규칙으로만 해석 (키가 없으면 `--text` 일 때 자동) |
| `--detector sam3_dinov3 / gdino / external` | 물체 인식기 (기본 `sam3_dinov3`) |
| `--yes` | 티칭 안 한 위치가 있어도 예시 좌표로 진행 |

말할 수 있는 것: 품목 = 콜라(펩시 포함), 사이다, 쫄병, 빼빼로, 스팸, 참치캔, 라면 / 행동 = 추가(넣어·담아·줘 …), 빼기(빼줘·취소 …) / 개수 = 하나·두 개·3개 …, "다·전부"(빼기만).

| 상황 | 로봇 반응 |
|---|---|
| 목록에 없는 품목 ("새우깡") / 행동이 없음 ("스팸 하나") | 판매하지 않는 품목 / 추가할지 뺄지 다시 물음 |
| 한 번에 5개 초과 | 거절 (`LLM_sam3/config.py` `MAX_COUNT_PER_COMMAND`) |
| 이어서 말한 추가 ("콜라 둘이랑 라면 하나") | 매대를 한 번 찍고 섞어서 가까운 순서로 |
| 매대에 없음 / 봉투 자리 없음 | 넣은 만큼만 하고 이유를 말함 |
| 봉투에 없는 품목 빼기 / 위에 다른 물건이 쌓임 / 내려놓는 곳이 다 참 | 그 이유를 말하고 꺼낸 만큼만 |
| 로봇 동작 오류 | 그 명령은 멈추고 안내, 대기 자세로 돌아가려고 시도 |

### 키보드 주문만 (`pickplace_sam3/main.py`, 음성·OpenAI 없이)
```bash
cd ~/corecode/pickplace_LLM/pickplace_sam3
python3 main.py --gripper ros [--detector gdino]
```
```
> 콜라 2, 컵라면 1      ← 여러 개 (쉼표로) — 매대를 찍어 ① 가까운 → ② 무거운 순서로
> 2                    ← 목록 2번 상품 1개
> c 2                  ← 매대의 2번 상품 개수 세기 (집지 않음)
> b                    ← 봉투 확인 (가장 높은 곳, 넣을 자리 있는지 — 지도 data/last_basket.png)
> n                    ← 새 봉투 (넣은 기록 지우기 — 봉투를 바꾸면 꼭)
> q                    ← 끝
```

---

## 6. 로봇이 하는 일 (요약)

1. **매대 찍기** (`upper` 촬영 자세) → 인식기가 물건마다 상품·점수·마스크를 줌 → 깊이로 로봇 좌표·폭을 잼.
   폭이 그 상품과 안 맞으면 폭이 맞는 다른 주문 상품으로 바로잡음. 앞 물건에 가려진 상품은 앞 물건을 옆으로 치우고 다시 찍어 집음.
2. **순서**: 로봇에서 가까운 것부터, 거리 차이 50mm(`NEAR_TOL`) 안이면 무거운 것부터. 매대는 한 번 찍은 순서표대로 집고, 방금 집은 자리 근처면 다시 찍음.
3. **봉투 자리 먼저**: 봉투를 찍어 높이 지도 → 바닥 빈자리 → 다른 물건 위(쌓기) → 그래도 없으면 안의 물건을 옮겨 자리 만들기 → 안 되면 그 상품은 건너뜀.
4. **집기**: 캔·컵라면(`FRONT_GRASP`)은 **정면에서 정중앙을 수평으로**, 손가락은 '상품 폭 + 양쪽 8mm' 만 벌림. 옆 물건에 손가락이 닿으면 그 물건을 잠깐 옆으로 옮겼다가 나중에 제자리로.
   쥔 뒤 상품 뒷면이 선반 끝에서 100mm(`SHELF_CLEARANCE`) 떨어질 때까지 수평으로 뺌.
5. **넣기**: 세운 채 수평으로 입구에 안 들어가면 **눕혀서** (`LAY_DOWN`), 받침면 위 5mm 에서 놓음. 평소 150mm 까지, 넘칠 때만 200mm 까지 채움.
6. **꺼내기** (음성 "빼기"): 넣을 때 남긴 기록으로 그 상품 자리를 알고, 봉투를 찍어 확인 → 위에서 집어 봉투 입구보다 높이 들고 → 내려놓는 곳에.
   위에 다른 물건이 쌓인 것은 꺼내지 않음. **이번 실행 중에 넣은 것만** 앎 (다시 켜면 기록 없음). 매대 재진열은 직원.

결과 그림: `data/last_detection.jpg` (매대 인식), `data/last_basket.png` (봉투 지도).

---

## 7. 주문 확인 (QR) 음성 안내 (`LLM_sam3/order_voice_node.py`)

```bash
cd ~/corecode/pickplace_LLM/LLM_sam3 && python3 order_voice_node.py
```
카메라·QR 인식 노드가 토픽을 보내면 안내 음성이 나옵니다.

| 사건 | 토픽 | 멘트 |
|---|---|---|
| 손님 감지 | `/order/customer_detected` (Empty) | 어서 오세요. 화면 속 QR 코드를 보여주세요. |
| QR 인식 | `/order/qr` (String, 예 `ORDER-0001`) | 맞음: N번 장바구니 물품을 가져가 주세요 / 틀림: 확인되지 않는 QR… |
| 물품 가져감 | `/order/item_taken` (Int32, 1 또는 2) | QR 전: ⚠️ QR 먼저 / 다른 장바구니: ⚠️ 잘못 가져가셨습니다 / 맞음: 감사합니다 |
| 초기화 | `/order/reset` (Empty) | — |

발행: `/order/voice` (방금 말한 문구), `/order/state` (대기 / QR 대기 / 수령 대기). QR → 장바구니 번호는 `LLM_sam3/config.py` 의 `ORDERS`.

---

## 8. 팀원 인식 코드를 그대로 연결 (`--detector external`)

팀원 함수가 BGR 사진(numpy, cv2.imread 모양) 하나를 받아 아래 목록을 돌려주면 로봇 코드는 고칠 것이 없습니다.
```python
# 예: pickplace_sam3/team_vision/infer.py
def detect(bgr):
    return [
        {"pid": "cola", "score": 0.91, "box": [x1, y1, x2, y2],   # 픽셀 (왼쪽 위, 오른쪽 아래)
         "mask": mask_hw_bool,                                    # (선택) 있으면 가운데·폭·가려짐을 마스크로
         "scores": {"cola": 0.91, "cider": 0.05}},                # (선택) 상품별 점수
        {"label": "사이다", "score": 0.8, "box": [...]},          # pid 대신 이름·별명도 됨
        {"pid": None, "score": 0.5, "box": [...]},                # 모르는 물건
    ]
```
```bash
PICKPLACE_EXTERNAL=team_vision.infer:detect python3 realsense_check.py --detector external     # 먼저 확인
PICKPLACE_EXTERNAL=team_vision.infer:detect python3 main.py --gripper ros --detector external  # LLM_sam3 도 같은 방법
```
`모듈:클래스` 를 주면 한 번 만들어(모델 한 번 로드) `detect()` 를 부릅니다. 파일 경로(`/경로/infer.py:detect`)도 됩니다.
SAM3 + DINOv3 인식기의 분류 방식만 바꾸고 싶으면 `vision/sam3_dinov3.py` 의 `classify()` 하나만 고치면 됩니다.

---

## 9. 설정 바꾸기

**`pickplace_sam3/config.py`**

| 바꾸고 싶은 것 | 설정 |
|---|---|
| 기본 인식기 | `DETECTOR` (`sam3_dinov3` / `gdino` / `external`) 또는 `PICKPLACE_DETECTOR=gdino` |
| SAM3 · DINOv3 모델 / 장치 | `SAM3_MODEL`, `DINOV3_MODEL` / `DEVICE` |
| 인식 기준 | `SAM3_PROMPTS`, `SAM3_THRESHOLD`, `SAM3_MAX_AREA`, `DINOV3_MIN_SIM`, `DINOV3_TEMPERATURE` |
| Grounding DINO 가 찾을 영어 이름 | `DINO_PROMPTS` (상품 id 목록도 여기 — 새 상품은 `NEW_PRODUCTS` + `PRODUCT_SPECS` 에도) |
| 상품 무게·크기 (순서·자리 계산) | `PRODUCT_SPECS` (⚠️ 예시값) |
| 집는 순서 | `PICK_PRIORITY` (`near_heavy` / `weight`), `NEAR_TOL` |
| 정면 집기 / 눕혀 넣기 상품 | `FRONT_GRASP` / `LAY_DOWN` |
| 선반에서 띄우는 거리 | `SHELF_CLEARANCE` (100mm) |
| 옆 물건 치우기 / 가려진 상품 꺼내기 | `CLEAR_NEIGHBORS` / `UNCOVER_HIDDEN` |
| 봉투 크기 · 채우는 높이 | `BASKET_SIZE`, `CONTAINER_DEPTH`, `CONTAINER_MAX` |
| 내려놓는 곳 | `RETURN_SPOT` (teach.py), `RETURN_STEP`, `RETURN_SLOTS` |
| 손가락 닫히는 방향 / 속도 | `GRIPPER_CLOSE_AXIS` / `VELOCITY, ACC` |

**`LLM_sam3/config.py`**

| 바꾸고 싶은 것 | 설정 |
|---|---|
| 품목 / STT 가 틀리게 적는 품목명 | `ITEMS`, `ITEM_ALIASES` |
| 음성 품목 → pickplace_sam3 상품 id | `ITEM_TO_PID` |
| 한 번에 최대 개수 | `MAX_COUNT_PER_COMMAND` (5) |
| 호출어가 잘 안 잡힘 | 터미널 `[wakeword] 들림: "…"` 글자를 `WAKEWORD_EXTRA_WORDS` 에 |
| 말했는데 무음 처리 | `SPEECH_RMS_MIN` 낮추기 (예: 500) |
| 안내 문구 / 목소리 | `VOICE_MESSAGES` / `TTS_VOICE`, `TTS_RATE` |
| 문장 해석 모델 | `OPENAI_MODEL` (gpt-4o) |
| 다른 마이크 | `VOICE_MIC_DEVICE=번호 python3 main.py ...` |

---

## 10. 문제 해결

| 증상 | 해결 |
|---|---|
| `SAM3/DINOv3 모델을 내려받지 못했습니다` | Hugging Face 승인 + `huggingface-cli login` (급하면 `--detector gdino`) |
| `SAM3/DINOv3 를 불러올 수 없습니다` / `requires the Torchvision library` | `pip install -U 'transformers>=5' torchvision` (torch 와 짝 맞게) |
| `갤러리가 비어 있습니다` / 전부 `?` | 3절 갤러리 사진 모으기 |
| `아직 티칭하지 않은 위치: ...` | 2절 `teach.py` (예시 좌표로 해 보려면 `--yes`) |
| `pickplace_sam3 폴더를 찾지 못했습니다` | `pickplace_LLM` 안에 두 폴더가 함께 있는지 |
| `카메라 사진을 받지 못했습니다` | bringup 을 `camera:=true` 로 띄웠는지, `ros2 topic list` 에 `/camera/color/image_raw` |
| `카메라에 봉투가 보이지 않습니다` / 빈 봉투인데 자리 없음 | 봉투촬영 자세 · 봉투바닥 다시 티칭, `data/last_basket.png` 확인 |
| 엉뚱한 곳을 집음 | `data/last_detection.jpg` 확인 → 4절 설정 / 핸드아이 캘리브레이션 |
| `movej 대기 자세 실패` | bringup 이 두 개 떠 있음 → 모두 Ctrl+C, `pkill -f ros2_control_node` 후 한 번만 |
| `목표 [...] 에 도달하지 못함` | 작업 범위 밖 · 특이점 → 촬영 자세 / 내려놓는 곳을 로봇 가까이로 |
| `그리퍼 연결 실패` | bringup 으로 띄웠으면 `--gripper ros` (`modbus` 는 RG2 직결용) |
| `Error code: 403 / 429` | OpenAI 키 권한·크레딧 |
| 메모리 부족으로 꺼짐 | 모델이 수 GB 사용 — 다른 프로그램 닫기, GPU PC 에서 |

### 알아두면 좋은 것
- LLM_sam3 는 pickplace_sam3 를 **따로 띄운 작업 프로세스**에서 돌립니다 (두 폴더가 `config`·`logic`·`robot` 같은 이름을 써서 한 프로그램에서 섞이면 안 됨). 모델은 처음 한 번만 로드합니다.
- 인터넷 필요: 호출어 확인·받아 적기·문장 해석(OpenAI), 모델 처음 내려받기(Hugging Face), 안내 음성 처음 만들기.
- git 에 안 올라가는 것 (`.gitignore`): `LLM_sam3/.env`(키), `sounds/cache/`, `data/last_*`, `data/realsense_check.jpg`, `data/gallery/embeddings.npz`, `__pycache__`.
  `positions.json`(티칭 결과)과 `data/gallery/` 사진은 같은 로봇·매대를 쓰면 올려서 같이 쓰면 됩니다.
- 비닐봉투는 입구를 벌려 세워 두세요 (오므라들면 카메라에 바닥이 안 보임).
