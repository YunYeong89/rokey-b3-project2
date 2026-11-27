# Packing Planning: LLM 포장 계획 비교 실험

편의점 자동 포장 로봇이 주문 상품을 봉투 하나에 담는 계획(Packing Plan)을 LLM으로 만들 수 있는지 확인하는 실험이다.
LLM에 주는 상품 정보만 바꿔서 세 조건을 비교한다. 모델·주문·공간·프롬프트·스키마·생성 설정은 같다.

| 조건 | LLM 입력 |
|---|---|
| Experiment 1 · Text-only | 상품명 + 수량 + 포장 공간 |
| Experiment 2 · Text + Vision | 위 + 실제 상품 사진 (`basket.jpg`) |
| Experiment 3 · Text + Properties | 위(사진 제외) + 주문 상품의 물리 특성(DB) |

## 폴더 구조
```
packing_planning/
├── run_experiment.py        v1: 세 조건 API 호출 (Responses API + Structured Outputs) → results/<실행일시>/
├── run_experiment_v2.py     v2: prompts/planner_v2.txt 사용, 검증·재요청 없이 응답만 저장 → results/v2_<실행일시>/
├── packing_validator.py     LLM과 독립적인 AABB 검증기 (누락·중복·경계·충돌·방향·적재 허용·하중·지지면·무게중심·층수·순서)
├── evaluate_run.py          저장된 v1 결과 재평가 → comparison.csv, experiment_report.md, 그림 (API 재호출 없음)
├── visualize.py             같은 좌표계·색상으로 3D / Top view 그림 생성
├── prompts/planner_v2.txt   v2 시스템 프롬프트
├── data/
│   ├── product_properties.json   상품 DB (8종, 개발용 근사값)
│   ├── container_config.json     봉투·용기 치수
│   ├── test_baskets.json         테스트 주문 (pilot_01 = 첫 비교 주문, 7개)
│   └── README.txt                데이터 주의사항
├── basket.jpg               Experiment 2 입력 사진 (보유 상품 12개 전체를 선반에서 촬영)
├── results/
│   ├── 20261127_124924/     v1 Run 1 (예비 실행, Exp 2 사진 설명 문구 오류 있음)
│   ├── 20261127_125141/     v1 Run 2 (본 실험, analysis_notes.md에 수동 분석)
│   └── v2_20261127_135754/  v2 프롬프트 실행 (검증 없음, 응답 원문만)
└── reports/                 브라우저로 여는 결과 정리 페이지
    ├── progress_report.html      v1 진행 현황·결론 요약
    ├── llm_results_v1.html       v1 세 실험의 LLM 출력 비교
    ├── llm_results_v2.html       v2 세 실험의 LLM 출력 비교
    └── assets/
```
각 `results/<run>/` 에는 `config.json`(입력 조건), `experiment_*.json`(파싱된 계획), `raw_responses/`(API 응답 원문), `comparison.csv` 가 있다.

## 실행
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # OPENAI_API_KEY 입력
.venv/bin/python run_experiment.py --model gpt-4o --image basket.jpg --repeats 1
.venv/bin/python run_experiment_v2.py --model gpt-4o --image basket.jpg
.venv/bin/python evaluate_run.py results/<실행일시>   # v1 결과 재평가만
```
v1에서 사진(`--image`, 또는 `basket.jpg` / `data/images/basket.*`)이 없으면 Experiment 2는 `not_run`으로 기록한다.

## 결과 요약 (v1, gpt-4o, temperature 0)
| 실험 | 총 위반 (Run 1 / Run 2) | 유효 계획 |
|---|---|---|
| 1. Text-only | 18 / 18 | 0 / 2 |
| 2. Text + Vision | 16 / 16 | 0 / 2 |
| 3. Text + Properties | **4 / 7** | 0 / 2 |

- **물리 특성 DB는 도움이 된다.** 치수·무게 오추정과 그에 따른 바닥 접촉 실패(공중 부양·파고듦)가 사라졌고, 2층 적재도 시도했다. Vision은 Text-only 대비 개선이 작고 토큰만 더 썼다.
- **DB만으로는 부족하다.** Validator를 통과한 계획은 6회 중 0건이다. 남은 오류는 벽 여유, 상품 간 간격, 자세에 따른 축 교환, 적층 높이 같은 좌표 산술 실수다.
- **제안:** LLM은 담기 순서·그룹·적층 관계 같은 정성적 결정만 하고, 좌표는 결정론적 배치기(Python)가 DB 치수로 계산한 뒤 Validator로 검증·재계획하는 구조.

자세한 분석은 `results/20261127_125141/analysis_notes.md` 와 `reports/progress_report.html` 참고.

v2(`prompts/planner_v2.txt`)는 속성 출처(given / image / prior) 기록, 보수적 추정 규칙, 그리퍼 여유·잡는 축 제약을 추가한 프롬프트로 세 조건을 한 번씩 다시 돌린 것이다. 검증은 아직 하지 않았고 LLM 출력은 `reports/llm_results_v2.html` 에서 비교할 수 있다.

## 한계 · 주의
- 조건당 v1 2회, v2 1회이고 모델은 gpt-4o 하나뿐이라 통계적 결론은 아니다.
- 모든 치수·무게·하중은 검증되지 않은 개발용 가정값이다. `gripper_clearance_mm`, `grip_axis` 도 임시값이다.
- Validator는 AABB 근사이며 통과가 실제 물리적 안정성을 보장하지 않는다. **실측과 시험 없이 로봇에서 실행하지 말 것.**
