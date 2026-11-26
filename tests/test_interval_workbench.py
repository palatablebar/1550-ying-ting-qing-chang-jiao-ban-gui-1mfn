from collections import Counter

from cinemaready.entry import app
from cinemaready.pipeline import preview


ROUTE = '/api/cinemaready'


def test_interval_stage_is_registered_between_backcheck_and_mix():
    output = app.test_client().get(ROUTE + '/overview').get_json()
    keys = [section['key'] for section in output['sections']]
    assert keys == ['show_handoffs', 'turnover_calendar', 'hall_signature', 'cleanup_trend',
                    'slot_backcheck', 'interval_governance', 'programming_mix',
                    'usher_windows', 'hall_handover']
    stage = output['sections'][5]
    assert stage['key'] == 'interval_governance' and stage['records']


def test_workbench_exposes_per_group_misblock_and_miss_metrics():
    output = preview()
    book = output['interval_policy']
    for table in (book['全馆回放'], book['分组回放']):
        assert table and all(
            set(row) >= {'影厅组', '回放实绩', '拦下', '误拦按时', '漏放超窗', '实绩超窗', '状态'}
            for row in table)
    # 样例的临时接管区没有回放实绩，必须显式标注而不是悄悄补零
    sparse = next(row for row in book['全馆回放'] if row['影厅组'] == '临时接管区')
    assert sparse['回放实绩'] == 0 and sparse['状态'] == '未到实绩回报时间'


def test_sample_shift_keeps_global_scheme_with_explicit_guards():
    output = preview()
    book = output['interval_policy']
    # 分组未减少误拦，且临时接管区缺回放实绩 -> 沿用全馆
    assert book['选定方案'] == '全馆区间'
    outcomes = {guard['守卫']: guard['通过'] for guard in book['守卫']}
    assert outcomes['分组区间少拦按时场次'] is False
    assert outcomes['当班各影厅组均有回放实绩'] is False


def test_blocked_tickets_skip_workstation_and_appear_in_queue():
    output = preview()
    states = Counter(row['state'] for row in output['rows'])
    blocked_rows = [row for row in output['rows'] if row['state'] == '拦截']
    flagged = set(output['interval_policy']['区间红线标记当班单'])
    row_states = {row['key']: row['state'] for row in output['rows']}
    # 被区间红线标记的单要么拦截、要么被更优先的硬规则暂停，都不会占工位
    assert blocked_rows
    assert all(row_states[key] in ('拦截', '暂停') for key in flagged)
    assert states['拦截'] <= len(flagged)
    # 拦下的单不占工位：工位守恒只统计已分派
    used = sum(row['cost'] for row in output['rows'] if row['state'] == '已分派')
    assert used + output['metrics'][-1]['value'] == output['capacity']
    assert all(row['reason'] == 'INTERVAL_OVER_CLEAR_GAP' for row in blocked_rows)
    for row in blocked_rows:
        minutes = row['measurements']['minutes']
        upper = float(row['interval'].split('–')[1])
        assert upper > minutes - 2


def test_dispatch_and_receipt_share_one_interval_scheme():
    output = preview()
    receipt_stage = output['sections'][8]
    # 凭证哈希与区间方案绑定：策略指纹在 hall_handover 内消费同一选定方案
    assert output['policy_hash'] and receipt_stage['key'] == 'hall_handover'
    # 对账行按处置状态列出，拦截单在凭证中可查且不进入已分派额度
    ledger_items = {item.get('核对项'): item.get('数量') for item in receipt_stage['records']}
    assert ledger_items.get('拦截', 0) >= 1
    # 同一批次重复计算结果完全一致（整班一次性切换，不允许单单据漂移）
    assert preview()['run_id'] == output['run_id']


def test_interval_stage_route_serves_group_table():
    web = app.test_client()
    run = web.get(ROUTE + '/overview').get_json()
    response = web.get(ROUTE + '/stages/interval_governance')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['key'] == run['sections'][5]['key']
    assert payload['run_id'] == run['run_id']


def test_replay_and_live_samples_never_leak_into_calibration():
    from cinemaready.interval_governance import service as interval
    from cinemaready.pipeline import ShiftDossier, open_fixture

    doc = open_fixture()
    dossier = ShiftDossier(doc['records'], doc['as_of'])
    _, estimates = dossier.grading()
    calib, replay, live = estimates['calibration'], estimates['replay'], estimates['live']
    # 构建成功本身就经过互斥断言；这里再显式核对三窗单号集合不相交
    keys = {name: {point.key for point in points}
            for name, points in (('calib', calib), ('replay', replay), ('live', live))}
    assert not (keys['calib'] & keys['replay'])
    assert not (keys['calib'] & keys['live'])
    assert not (keys['replay'] & keys['live'])
    # 区间校准只用校准窗：回放窗和当班窗没有任何单被喂给 prepare_verification
    regime = interval.build_interval_regime(calib, replay, live)
    assert regime.global_verification.size == len(calib)
    for row in regime.group_calibrations:
        assert row.verification.size <= len(calib)
