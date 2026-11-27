"""Packing Planning 비교 실험 v2 — 사용자 작성 프롬프트(prompts/planner_v2.txt) 사용.

Experiment 1 text_only       : items(item_id, name) + packing_space + constraints
Experiment 2 text_vision     : 위 + 상품 사진
Experiment 3 text_properties : 위(사진 제외) + items[].properties (DB 값을 프롬프트 속성 이름으로 변환)

검증(Evaluator)과 재요청은 하지 않는다. 각 조건 1회 독립 요청 후 응답을 그대로 저장한다.
  .venv/bin/python run_experiment_v2.py --model gpt-4o --image basket.jpg
"""
import argparse, base64, csv, hashlib, json, mimetypes, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB = json.loads((DATA / 'product_properties.json').read_text(encoding='utf-8'))
CONTAINER = json.loads((DATA / 'container_config.json').read_text(encoding='utf-8'))
BASKETS = json.loads((DATA / 'test_baskets.json').read_text(encoding='utf-8'))
PROMPT = (ROOT / 'prompts' / 'planner_v2.txt').read_text(encoding='utf-8')

P = CONTAINER['prototype']
CLEAR = P['wall_clearance_mm']
PACKING_SPACE = {
    'description': '고정 용기에 설치된 비닐봉투. 바닥·측면 지지, 내부를 직육면체로 근사',
    'inner_dimensions_mm': {'width': P['inner_dimensions_mm'][0], 'depth': P['inner_dimensions_mm'][1],
                            'safe_fill_height': P['safe_fill_height_mm']},
    'wall_clearance_mm': CLEAR,
    'allowed_region_mm': {'x': [0, P['inner_dimensions_mm'][0] - 2*CLEAR],
                          'y': [0, P['inner_dimensions_mm'][1] - 2*CLEAR],
                          'z': [0, P['safe_fill_height_mm']]},
}
CONSTRAINTS = {
    'max_layers': 2,
    'min_support_ratio': 0.30,
    'gripper_clearance_mm': 10,   # 임시값: RG2 손가락 두께/여유 실측 전
    'grip_axis': 'x',             # 임시값: 로봇 기준 좌우 방향으로 잡는다고 가정
}

# DB(10개 필드) → 프롬프트 속성 이름 변환 규칙. DB 값은 바꾸지 않고 이름·형식만 맞춘다.
SHAPE_MAP = {'cylinder': 'cylinder', 'truncated_cone': 'cylinder', 'flexible_pouch': 'bag',
             'cuboid': 'box', 'rounded_cuboid': 'box'}
CONVERSION_RULES = {
    'dimensions_mm': 'DB dimensions_mm [w,d,h] → {width, depth, height}',
    'shape': 'cylinder/truncated_cone → cylinder, flexible_pouch → bag, cuboid/rounded_cuboid → box',
    'default_orientation': 'allowed_orientations == ["flat"] 이면 flat, 아니면 upright',
    'allowed_orientations': 'DB 그대로',
    'weight_g': 'DB gross_weight_g',
    'rigid': 'DB rigidity == "high"',
    'fragile': 'DB fragility in ("medium", "high")',
    'deformable': 'DB rigidity == "low" 또는 shape == flexible_pouch',
    'upright_required': 'DB allowed_orientations == ["upright"]',
    'stackable_on_top': 'DB stack_on_top_allowed',
    'max_top_load_g': 'DB max_supported_load_g (null 그대로)',
}


def to_prompt_properties(d):
    return {
        'dimensions_mm': dict(zip(('width', 'depth', 'height'), d['dimensions_mm'])),
        'shape': SHAPE_MAP[d['shape']],
        'default_orientation': 'flat' if d['allowed_orientations'] == ['flat'] else 'upright',
        'allowed_orientations': d['allowed_orientations'],
        'weight_g': d['gross_weight_g'],
        'rigid': d['rigidity'] == 'high',
        'fragile': d['fragility'] in ('medium', 'high'),
        'deformable': d['rigidity'] == 'low' or d['shape'] == 'flexible_pouch',
        'upright_required': d['allowed_orientations'] == ['upright'],
        'stackable_on_top': d['stack_on_top_allowed'],
        'max_top_load_g': d['max_supported_load_g'],
    }


ATTRS = ['dimensions_mm', 'shape', 'default_orientation', 'allowed_orientations', 'weight_g', 'rigid', 'fragile',
         'deformable', 'upright_required', 'stackable_on_top', 'max_top_load_g']
SRC = {'type': 'string', 'enum': ['given', 'image', 'prior']}
DIMS = {'type': 'object', 'properties': {k: {'type': 'number'} for k in ('width', 'depth', 'height')},
        'required': ['width', 'depth', 'height'], 'additionalProperties': False}
ITEM_PROPS = {'type': 'object', 'properties': {
    'item_id': {'type': 'string'},
    'dimensions_mm': DIMS,
    'shape': {'type': 'string', 'enum': ['box', 'cylinder', 'bag']},
    'default_orientation': {'type': 'string', 'enum': ['upright', 'flat']},
    'allowed_orientations': {'type': 'array', 'items': {'type': 'string', 'enum': ['upright', 'flat']}},
    'weight_g': {'type': 'number'},
    'rigid': {'type': 'boolean'}, 'fragile': {'type': 'boolean'}, 'deformable': {'type': 'boolean'},
    'upright_required': {'type': 'boolean'}, 'stackable_on_top': {'type': 'boolean'},
    'max_top_load_g': {'type': ['number', 'null']},
    'attribute_sources': {'type': 'object', 'properties': {a: SRC for a in ATTRS}, 'required': ATTRS,
                          'additionalProperties': False},
}, 'required': ['item_id'] + ATTRS + ['attribute_sources'], 'additionalProperties': False}
XYZ = {'type': 'object', 'properties': {k: {'type': 'integer'} for k in 'xyz'}, 'required': list('xyz'),
       'additionalProperties': False}
STEP = {'type': 'object', 'properties': {
    'sequence': {'type': 'integer'},
    'item_id': {'type': 'string'},
    'position_mm': XYZ,
    'orientation': {'type': 'string', 'enum': ['upright', 'flat']},
    'yaw_deg': {'type': 'integer', 'enum': [0, 90]},
    'layer': {'type': 'integer', 'enum': [1, 2]},
    'supported_by': {'type': 'array', 'items': {'type': 'string'}},
    'reason': {'type': 'string'},
}, 'required': ['sequence', 'item_id', 'position_mm', 'orientation', 'yaw_deg', 'layer', 'supported_by', 'reason'],
    'additionalProperties': False}
SCHEMA = {'type': 'object', 'properties': {
    'status': {'type': 'string', 'enum': ['planned', 'infeasible']},
    'reason': {'type': 'string'},
    'item_properties': {'type': 'array', 'items': ITEM_PROPS},
    'steps': {'type': 'array', 'items': STEP},
}, 'required': ['status', 'reason', 'item_properties', 'steps'], 'additionalProperties': False}

GEN_SETTINGS = {'temperature': 0, 'max_output_tokens': 8000}
PRICE = {'input': 2.50, 'output': 10.00}  # USD / 1M tokens, gpt-4o 공개 단가 기준 추정
EXPERIMENTS = [('experiment_1_text_only', 'text_only', 'Text-only'),
               ('experiment_2_text_vision', 'text_vision', 'Text + Vision'),
               ('experiment_3_text_properties', 'text_properties', 'Text + Physical Properties')]
PHOTO_NOTE = '첨부한 사진에는 주문된 상품이 모두 찍혀 있으며, 주문하지 않은 상품도 함께 찍혀 있을 수 있다.'


def build_items(order_id, with_props):
    order = next(o for o in BASKETS['orders'] if o['order_id'] == order_id)
    items = []
    for line in order['items']:
        for k in range(line['quantity']):
            it = {'item_id': f"{line['product_id']}#{k+1}", 'name': line['display_name']}
            if with_props:
                it['properties'] = to_prompt_properties(DB[line['product_id']])
            items.append(it)
    return order, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='gpt-4o')
    ap.add_argument('--image', type=Path, default=ROOT / 'basket.jpg')
    ap.add_argument('--order', default='pilot_01')
    args = ap.parse_args()

    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / '.env')
    client = OpenAI(max_retries=0)

    photo = None
    if args.image and args.image.exists():
        raw = args.image.read_bytes(); mime = mimetypes.guess_type(args.image.name)[0]
        photo = {'path': str(args.image), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'data_url': f'data:{mime};base64,' + base64.b64encode(raw).decode()}

    out = ROOT / 'results' / ('v2_' + time.strftime('%Y%m%d_%H%M%S'))
    (out / 'raw_responses').mkdir(parents=True, exist_ok=False)
    order, _ = build_items(args.order, False)
    (out / 'config.json').write_text(json.dumps({
        'prompt_file': 'prompts/planner_v2.txt', 'prompt': PROMPT, 'model_requested': args.model,
        'generation_settings': GEN_SETTINGS, 'order': order, 'packing_space': PACKING_SPACE,
        'constraints': CONSTRAINTS, 'temporary_values': ['constraints.gripper_clearance_mm', 'constraints.grip_axis'],
        'property_conversion_rules': CONVERSION_RULES, 'schema': SCHEMA,
        'image': None if photo is None else {k: photo[k] for k in ('path', 'sha256')},
        'evaluator': '실행하지 않음 (재요청 없음)',
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    rows = []
    for fname, mode, label in EXPERIMENTS:
        _, items = build_items(args.order, mode == 'text_properties')
        payload = {'packing_space': PACKING_SPACE, 'constraints': CONSTRAINTS, 'items': items}
        content = [{'type': 'input_text', 'text': json.dumps(payload, ensure_ascii=False, indent=1)}]
        record = {'experiment': fname, 'mode': mode, 'label': label, 'model_requested': args.model}
        if mode == 'text_vision':
            if photo is None:
                record.update(run_status='not_run', note='사진 없음')
                (out / f'{fname}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                rows.append(record); print(label, 'not_run'); continue
            content.append({'type': 'input_text', 'text': PHOTO_NOTE})
            content.append({'type': 'input_image', 'image_url': photo['data_url'], 'detail': 'high'})
        if mode != 'text_properties':
            assert '"properties"' not in content[0]['text']
        record['request_input'] = [c if c['type'] != 'input_image' else {**c, 'image_url': '<base64 생략>'} for c in content]
        t0 = time.perf_counter()
        try:
            resp = client.responses.create(
                model=args.model, instructions=PROMPT, input=[{'role': 'user', 'content': content}],
                text={'format': {'type': 'json_schema', 'name': 'packing_plan', 'strict': True, 'schema': SCHEMA}},
                store=False, **GEN_SETTINGS)
            dt = time.perf_counter() - t0
            (out / 'raw_responses' / f'{fname}.json').write_text(resp.model_dump_json(indent=2), encoding='utf-8')
            u = resp.usage
            record.update(run_status='ok', response_model=resp.model, seconds=round(dt, 3),
                          input_tokens=u.input_tokens, output_tokens=u.output_tokens, total_tokens=u.total_tokens,
                          estimated_cost_usd=round(u.input_tokens/1e6*PRICE['input'] + u.output_tokens/1e6*PRICE['output'], 6))
            try:
                record['plan'] = json.loads(resp.output_text)
            except json.JSONDecodeError as e:
                record.update(plan=None, parse_error=str(e))
        except Exception as e:
            record.update(run_status='api_error', seconds=round(time.perf_counter()-t0, 3),
                          error_type=type(e).__name__, error=str(e)[:1000])
        (out / f'{fname}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        rows.append(record)
        p = record.get('plan') or {}
        print(f"[{label}] {record['run_status']} status={p.get('status')} t={record.get('seconds')}s tokens={record.get('total_tokens')}")

    with (out / 'comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
        cols = ['experiment', 'label', 'run_status', 'response_model', 'plan_status', 'n_steps', 'n_layer2',
                'seconds', 'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost_usd']
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            p = r.get('plan') or {}
            w.writerow({**{k: r.get(k, '') for k in cols}, 'plan_status': p.get('status', ''),
                        'n_steps': len(p.get('steps', [])), 'n_layer2': sum(1 for s in p.get('steps', []) if s['layer'] == 2)})
    print(out)


if __name__ == '__main__':
    main()
