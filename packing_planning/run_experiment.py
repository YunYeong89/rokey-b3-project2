"""OpenAI API 기반 Packing Planning 비교 실험.

Experiment 1  text_only        : 상품명 + 수량 + 포장 공간
Experiment 2  text_vision      : 위 + 실제 상품 사진 (사진이 없으면 미실행으로 기록)
Experiment 3  text_properties  : 위(사진 제외) + 주문 상품의 물리적 특성만 추출

세 조건은 모델·주문·공간·프롬프트·스키마·생성 설정이 동일하며 상품 입력 정보만 다르다.
각 요청은 독립적이며(store=False, 이전 대화/결과 미전달) 재계획은 하지 않는다.

사용 예:
  .venv/bin/python run_experiment.py --model gpt-4o --repeats 1
  .venv/bin/python run_experiment.py --model gpt-4o --image data/images/basket.jpg
"""
import argparse, base64, csv, hashlib, json, mimetypes, time
from pathlib import Path

from packing_validator import validate, NOT_IMPLEMENTED

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB = json.loads((DATA / 'product_properties.json').read_text(encoding='utf-8'))
CONTAINER = json.loads((DATA / 'container_config.json').read_text(encoding='utf-8'))
BASKETS = json.loads((DATA / 'test_baskets.json').read_text(encoding='utf-8'))
PROPERTY_FIELDS = ['name', 'variant', 'dimensions_mm', 'gross_weight_g', 'shape', 'rigidity', 'fragility',
                   'allowed_orientations', 'max_supported_load_g', 'stack_on_top_allowed']
DEFAULT_IMAGE_CANDIDATES = [DATA / 'images' / n for n in ('basket.jpg', 'basket.jpeg', 'basket.png', 'basket.webp')] + \
                           [ROOT / n for n in ('basket.jpg', 'basket.jpeg', 'basket.png', 'basket.webp')]

# 공통 포장 공간: container_config.json prototype 값을 사용. 최대 층수는 실험 요구사항(2층)으로 고정
# (container_config.json prototype.max_layers=1 은 수정하지 않고 실험에서만 2로 둔다).
P = CONTAINER['prototype']
SPACE = {
    'size_mm': [P['inner_dimensions_mm'][0], P['inner_dimensions_mm'][1], P['safe_fill_height_mm']],
    'wall_clearance_mm': P['wall_clearance_mm'],
    'max_layers': 2,
}
SPACE_TEXT = {
    'container': '고정 용기에 설치된 비닐봉투 (바닥·측면 지지, 내부를 직육면체로 근사)',
    'coordinate_system': '원점=내부 바닥 왼쪽 앞 모서리, x=오른쪽, y=뒤쪽, z=위쪽, 단위 mm',
    'usable_space_mm': {'x': SPACE['size_mm'][0], 'y': SPACE['size_mm'][1], 'z_safe_fill_height': SPACE['size_mm'][2]},
    'wall_clearance_mm': SPACE['wall_clearance_mm'],
    'allowed_region_mm': {'x': [SPACE['wall_clearance_mm'], SPACE['size_mm'][0]-SPACE['wall_clearance_mm']],
                          'y': [SPACE['wall_clearance_mm'], SPACE['size_mm'][1]-SPACE['wall_clearance_mm']],
                          'z': [0, SPACE['size_mm'][2]]},
    'opening_clear_dimensions_mm': P['opening_clear_dimensions_mm'],
    'max_layers': SPACE['max_layers'],
}

# 세 실험 공통 프롬프트 (상품 고유 수치는 포함하지 않는다)
PROMPT = '''당신은 편의점 자동 포장 로봇의 Packing Planner다. 주문된 모든 상품을 고정 용기에 설치된 비닐봉투 하나에 담는 계획을 작성하라. 실제 로봇 실행은 하지 않는다.

[공간] 봉투 내부는 직육면체로 근사하며 사용자 메시지의 packing_space를 따른다. 상품의 외접 직육면체는 allowed_region_mm 안에 있어야 한다(x,y 벽에서 여유 확보, z는 0~안전 적재 높이).
[좌표] position_mm은 배치 후 상품 외접 직육면체의 중심(mm)이다. 바닥에 놓인 상품의 z = 높이/2, 다른 상품 위에 놓인 상품의 z = 지지 상품 윗면 높이 + 자신의 높이/2.
[자세] orientation은 upright 또는 flat이다. upright는 상품을 정상적으로 세운 기본 자세 [width(x), depth(y), height(z)]. flat은 상품을 뒤쪽으로 눕혀 원래 depth와 height를 교환한 자세이며, 봉지류처럼 원래 평평하게 놓이는 상품은 flat이 기본 자세 그대로이다. yaw_deg는 0 또는 90이며 90이면 x,y 길이를 교환한다. 상품마다 허용되지 않는 자세가 있을 수 있다.
[적재] 최대 2층(layer 1=바닥, layer 2=1층 상품 위). 상부 상품은 먼저 담긴 하부 상품의 윗면에 접촉해야 한다. 단단하고 하중을 견딜 수 있는 상품 위에만 적재하고, 압축·파손에 취약한 상품 위에는 아무것도 올리지 않는다. 상부 상품 외접 사각형 면적의 30% 이상이 지지되어야 하고, 상부 상품 중심의 수평 투영점이 지지 영역의 볼록껍질 내부에 있어야 한다. 원통형 지지면은 원에 내접하는 정사각형으로 보수적으로 근사한다. 상부 상품이 여러 상품에 걸치면 각 지지 상품에 상부 상품의 전체 무게가 실린다고 보수적으로 가정하고 허용 하중을 넘지 않게 한다. 상품 정보가 주어지면 허용 하중 및 상부 적재 조건을 준수한다. 무리한 적재보다 바닥 배치를 우선하되 필요하면 2층을 활용한다.
[순서] sequence는 1부터 시작하는 담기 순서이며, 지지 상품은 그 위에 놓일 상품보다 먼저 담겨야 한다. 무겁고 단단한 상품을 먼저, 가볍고 파손에 취약한 상품을 나중에 담는 것을 고려한다.
[출력] 주어진 모든 item_id를 정확히 한 번씩 사용한다. 상품 간 외접 직육면체가 서로 겹치면 안 된다. 각 상품에 대해 계획에 사용한 외형 치수(assumed_dimensions_mm, upright 기본 자세 기준 width/depth/height)와 무게 추정(assumed_weight_g)을 기록하고, supported_by에 직접 지지하는 상품의 item_id(바닥이면 빈 배열)를 적는다. 정보가 부족해 추정하거나 가정한 내용은 assumptions에 명시한다. 불가능하다고 판단하면 status를 infeasible로 하고 이유를 reason에 쓴다.'''

XYZ = {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'z': {'type': 'number'}},
       'required': ['x', 'y', 'z'], 'additionalProperties': False}
WDH = {'type': 'object', 'properties': {'width': {'type': 'number'}, 'depth': {'type': 'number'}, 'height': {'type': 'number'}},
       'required': ['width', 'depth', 'height'], 'additionalProperties': False}
STEP = {'type': 'object', 'properties': {
    'sequence': {'type': 'integer'},
    'item_id': {'type': 'string'},
    'product_id': {'type': 'string'},
    'position_mm': XYZ,
    'orientation': {'type': 'string', 'enum': ['upright', 'flat']},
    'yaw_deg': {'type': 'integer', 'enum': [0, 90]},
    'layer': {'type': 'integer', 'enum': [1, 2]},
    'supported_by': {'type': 'array', 'items': {'type': 'string'}},
    'assumed_dimensions_mm': WDH,
    'assumed_weight_g': {'type': 'number'},
    'reason': {'type': 'string'},
}, 'required': ['sequence', 'item_id', 'product_id', 'position_mm', 'orientation', 'yaw_deg', 'layer',
                'supported_by', 'assumed_dimensions_mm', 'assumed_weight_g', 'reason'], 'additionalProperties': False}
SCHEMA = {'type': 'object', 'properties': {
    'status': {'type': 'string', 'enum': ['planned', 'infeasible']},
    'steps': {'type': 'array', 'items': STEP},
    'reason': {'type': 'string'},
    'assumptions': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['status', 'steps', 'reason', 'assumptions'], 'additionalProperties': False}

GEN_SETTINGS = {'temperature': 0, 'max_output_tokens': 6000}
# USD / 1M tokens. 공식 페이지에서 직접 확인하지 못한 공개 단가 → 추정치로만 사용
PRICING = {'gpt-4o': {'input': 2.50, 'output': 10.00, 'source': '공개 단가(gpt-4o 표준 요금) 기준 추정, 청구액 아님'}}

EXPERIMENTS = [
    ('experiment_1_text_only', 'text_only', 'Text-only'),
    ('experiment_2_text_vision', 'text_vision', 'Text + Vision'),
    ('experiment_3_text_properties', 'text_properties', 'Text + Physical Properties'),
]
LEAK_MARKERS = ['gross_weight_g', 'max_supported_load_g', 'stack_on_top_allowed', 'allowed_orientations', 'dimensions_mm"']


def build_order(order_id):
    order = next(o for o in BASKETS['orders'] if o['order_id'] == order_id)
    items = []
    for line in order['items']:
        for k in range(line['quantity']):
            items.append({'item_id': f"{line['product_id']}_{k+1}", 'product_id': line['product_id'],
                          'name': line.get('display_name', DB[line['product_id']]['name'])})
    return order, items


def build_content(mode, order, items, photo):
    payload = {
        'order': [{'name': l.get('display_name', DB[l['product_id']]['name']), 'product_id': l['product_id'],
                   'quantity': l['quantity']} for l in order['items']],
        'items': items,
        'packing_space': SPACE_TEXT,
    }
    content = [{'type': 'input_text', 'text': json.dumps(payload, ensure_ascii=False)}]
    if mode == 'text_vision':
        content.append({'type': 'input_text', 'text': '아래 사진은 매장에 보유 중인 실제 상품을 촬영한 것이다. 주문에 포함되지 않은 상품이나 같은 상품이 주문 수량보다 많이 함께 찍혀 있을 수 있으므로, 주문된 상품만 계획에 사용하라. 사진에서 주문 상품의 크기, 형태, 강성, 파손 위험을 추론하라.'})
        content.append({'type': 'input_image', 'image_url': photo['data_url'], 'detail': 'high'})
    if mode == 'text_properties':
        props = {pid: {f: DB[pid][f] for f in PROPERTY_FIELDS} for pid in sorted({x['product_id'] for x in items})}
        content.append({'type': 'input_text', 'text': '주문 상품의 물리적 특성(개발용 근사값, dimensions_mm은 upright 기준 [width, depth, height]):\n'
                        + json.dumps(props, ensure_ascii=False, indent=1)})
    return content


def redact_request(content):
    out = []
    for c in content:
        if c['type'] == 'input_image':
            out.append({**c, 'image_url': '<base64 생략>'})
        else:
            out.append(c)
    return out


def find_image(arg):
    if arg:
        return arg if arg.exists() else None
    return next((p for p in DEFAULT_IMAGE_CANDIDATES if p.exists()), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='gpt-4o')
    ap.add_argument('--image', type=Path, default=None, help='주문 상품 실제 사진 (없으면 Experiment 2 미실행)')
    ap.add_argument('--order', default='pilot_01')
    ap.add_argument('--repeats', type=int, default=1)
    args = ap.parse_args()

    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / '.env')
    client = OpenAI(max_retries=0)  # 실패 시 자동 재시도하지 않음

    order, items = build_order(args.order)
    img = find_image(args.image)
    photo = None
    if img is not None:
        mime = mimetypes.guess_type(img.name)[0]
        raw = img.read_bytes()
        if mime in ('image/jpeg', 'image/png', 'image/webp'):
            photo = {'path': str(img), 'sha256': hashlib.sha256(raw).hexdigest(),
                     'data_url': f'data:{mime};base64,' + base64.b64encode(raw).decode()}

    out = ROOT / 'results' / time.strftime('%Y%m%d_%H%M%S')
    (out / 'raw_responses').mkdir(parents=True, exist_ok=False)
    (out / 'config.json').write_text(json.dumps({
        'model_requested': args.model, 'generation_settings': GEN_SETTINGS, 'order_id': args.order,
        'order': order, 'items': items, 'packing_space': SPACE, 'prompt': PROMPT, 'schema': SCHEMA,
        'image': None if photo is None else {k: photo[k] for k in ('path', 'sha256')},
        'validator_not_implemented': NOT_IMPLEMENTED, 'repeats': args.repeats,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    rows = []
    for r in range(1, args.repeats + 1):
        for fname, mode, label in EXPERIMENTS:
            suffix = '' if args.repeats == 1 else f'_r{r}'
            record = {'experiment': fname, 'mode': mode, 'label': label, 'repeat': r, 'model_requested': args.model,
                      'generation_settings': GEN_SETTINGS}
            if mode == 'text_vision' and photo is None:
                record.update(run_status='not_run', note='실제 상품 사진 파일이 없어 미실행 (임의 사진으로 대체하지 않음)')
                (out / f'{fname}{suffix}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                rows.append(record); print(f'[{label}] not_run (사진 없음)'); continue
            content = build_content(mode, order, items, photo)
            if mode != 'text_properties':
                text = PROMPT + json.dumps([c for c in content if c['type'] == 'input_text'], ensure_ascii=False)
                leaked = [m for m in LEAK_MARKERS if m in text]
                assert not leaked, f'{mode} 요청에 물리적 특성 필드가 포함됨: {leaked}'
            record['request'] = {'instructions': 'config.json의 prompt와 동일', 'input': redact_request(content)}
            start = time.perf_counter()
            try:
                resp = client.responses.create(
                    model=args.model, instructions=PROMPT, input=[{'role': 'user', 'content': content}],
                    text={'format': {'type': 'json_schema', 'name': 'packing_plan', 'strict': True, 'schema': SCHEMA}},
                    store=False, **GEN_SETTINGS)
                elapsed = time.perf_counter() - start
                (out / 'raw_responses' / f'{fname}{suffix}.json').write_text(resp.model_dump_json(indent=2), encoding='utf-8')
                u = resp.usage
                price = PRICING.get(args.model) or next((v for k, v in PRICING.items() if resp.model.startswith(k)), None)
                cost = None if price is None else round(u.input_tokens/1e6*price['input'] + u.output_tokens/1e6*price['output'], 6)
                record.update(run_status='ok', response_model=resp.model, response_status=resp.status,
                              seconds=round(elapsed, 3), input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                              total_tokens=u.total_tokens, estimated_cost_usd=cost,
                              cost_note=None if price is None else price['source'])
                try:
                    plan = json.loads(resp.output_text)
                    record['plan'] = plan
                    record['evaluation'] = validate(plan, items, DB, SPACE)
                except json.JSONDecodeError as e:
                    record.update(plan=None, parse_error=str(e), evaluation=validate(None, items, DB, SPACE))
            except Exception as e:
                record.update(run_status='api_error', seconds=round(time.perf_counter()-start, 3),
                              error_type=type(e).__name__, error=str(e)[:1000])
            (out / f'{fname}{suffix}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            rows.append(record)
            ev = record.get('evaluation', {})
            print(f"[{label}] {record['run_status']} valid={ev.get('valid')} "
                  f"violations={sum(ev.get('violation_counts', {}).values()) if ev else ''} "
                  f"t={record.get('seconds')}s tokens={record.get('total_tokens')}")

    try:
        from evaluate_run import finalize
        finalize(out)
    except Exception as e:
        print('평가/보고서 생성 실패:', type(e).__name__, e)
    print(out)


if __name__ == '__main__':
    main()
