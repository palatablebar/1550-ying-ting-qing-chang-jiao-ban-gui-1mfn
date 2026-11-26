from dataclasses import replace
import json
from pathlib import Path

from .show_handoffs import service as intake_service
from .turnover_calendar import service as cohort_service
from .hall_signature import service as feature_service
from .cleanup_trend import service as model_service
from .slot_backcheck import service as calibration_service
from .interval_governance import service as interval_service
from .programming_mix import service as monitor_service
from .usher_windows import service as dispatch_service
from .hall_handover import service as audit_service
from .turnover_calendar import contracts as window_contract

PRESET = Path(__file__).resolve().parents[2] / 'sample_data' / 'show_handoffs_sample.json'
MODULES = (('show_handoffs', '场次交接'),
 ('turnover_calendar', '影厅日程'),
 ('hall_signature', '清理因素'),
 ('cleanup_trend', '耗时滚动'),
 ('slot_backcheck', '窗口复算'),
 ('interval_governance', '区间治理'),
 ('programming_mix', '排片分组'),
 ('usher_windows', '清场计划'),
 ('hall_handover', '开厅交接'))
DISPLAY = {'title': '影厅清场交班轨',
 'object_name': '影厅场次',
 'unit': '分钟',
 'role': '多厅影院保洁值班员',
 'endpoint': 'cinemaready',
 'identity': 'TURNAROUND',
 'location': '运营中心 · 影厅轮转',
 'visual': '场间清洁窗口',
 'queue': '当班场次',
 'resource': '保洁班组位',
 'action': '复排清场窗口',
 'lane': 'L10',
 'field_title': '清场倒排窗口'}


def open_fixture(path=PRESET):
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or not isinstance(document.get('records'), list):
        raise ValueError('样例必须包含 records 数组')
    if len(document['records']) > 2500:
        raise ValueError('单次导入最多处理 2500 条单据')
    return document


def stage_at(index, details, records, issues=()):
    code, title = MODULES[index]
    return {'key': code, 'title': title, 'summary': details, 'records': list(records), 'errors': list(issues)}


def rows_for(plan, scored, vectors, dispatch_views=None):
    by_key = {item.key: item for item in scored}
    view_by_key = {item.raw.key: item for item in (dispatch_views or ())}
    factor_names = ('本场人数', '单桶污渍压力', '交班节奏', '楼梯移动压力', '污渍与人流交互')
    order = []
    for job in plan.jobs:
        point = by_key[job.key]
        view = view_by_key.get(job.key)
        lower = job.lower if view is None else view.lower
        upper = job.upper if view is None else view.upper
        if len(point.contributions) == len(factor_names):
            reasons = sorted(zip(factor_names, point.contributions, strict=True), key=lambda val: (-abs(val[1]), val[0]))
            driver = ' · '.join(f'{key} {amount:+.2f}' for key, amount in reasons[:2])
        else:
            driver = ' · '.join(f'{amount:+.2f}' for amount in point.contributions)
        interval = ('–' if lower is None else f'{lower:.2f}–{upper:.2f}')
        order.append({'key': job.key, 'zone': job.cohort, 'estimate': job.estimate, 'interval': interval,
                      'state': job.state, 'cost': job.cost, 'reason': job.reason, 'evidence': job.summary,
                      'drivers': driver, 'missing': len(point.missing), 'hits': list(job.rule_hits),
                      'measurements': dict(zip(('seats', 'filled', 'spill', 'minutes', 'bins', 'stairs'), point.input_values, strict=True)),
                      'unmeasured': list(point.missing), 'operations': feature_service.business_factors(vectors[job.key])})
    return order


def interval_stage_records(book):
    records = [
        {'核对项': '当班选定方案', '结果': book['选定方案']},
        {'核对项': '切换说明', '结果': '；'.join(book['切换说明'])},
    ]
    records.extend({'守卫': item['守卫'], '通过': '是' if item['通过'] else '否',
                    **{key: value for key, value in item.items()
                       if key not in ('守卫', '通过')}} for item in book['守卫'])
    records.extend({'分组校准': item['影厅组'], '校准实绩': item['校准实绩'],
                    '区间方法': item['区间方法'], '资格': item['资格']}
                   for item in book['分组校准'])
    records.extend({'回放方案': '全馆区间', **{key: value for key, value in row.items()}}
                   for row in book['全馆回放'])
    records.extend({'回放方案': '分组区间', **{key: value for key, value in row.items()}}
                   for row in book['分组回放'])
    records.extend({'区间红线标记当班单': key, '结果': '上界超出清场空档（硬规则优先时实际暂停）'}
                   for key in book['区间红线标记当班单'])
    return records


def comparison_rows(predictions):
    return [{'单号': point.key, '分组': point.cohort, '原始估计': round(point.score, 4),
             '补值字段': len(point.missing), '偏离特征': '、'.join(point.excursions) or '—'}
            for point in predictions[:14]]


def replay_metrics(report):
    result = [{'label': '回放实绩样本', 'value': report.count}, {'label': '回放绝对误差', 'value': report.mae}]
    return result


class ShiftDossier:
    def __init__(self, request, as_of):
        self.raw_forms = request
        self.calendar = replace(window_contract.WindowRule.standard(allow_censor=False),as_of=as_of)
        self.accepted = intake_service.load_samples(request,as_of)
        self.history = cohort_service.build_windows(self.accepted.entries,self.calendar)
        cohort_service.model_inputs(self.history)
        self.feature_stamp = feature_service.fit_features(self.history.train)
        self.prepared = feature_service.transform_windows(self.history,self.feature_stamp)
        self.forecaster = model_service.fit_model(self.prepared.train,self.feature_stamp.fingerprint)

    def grading(self):
        predictions = {}
        for name, points in self.prepared.segments():
            if name != 'train':
                predictions[name] = model_service.predict(self.forecaster,points,self.feature_stamp.fingerprint)
        checked = calibration_service.prepare_verification(predictions['calibration'])
        return checked, predictions

    def verification(self, observed, calibration):
        holdout = calibration_service.validate_predictions(observed['replay'],calibration)
        assignment = calibration_service.validate_predictions(observed['live'],calibration)
        prepared = self.prepared
        baseline = monitor_service.fit_baseline(prepared.train,self.feature_stamp.fingerprint)
        detection = monitor_service.scan_current(baseline,prepared.live,self.feature_stamp.fingerprint)
        return holdout, assignment, detection

    def seal(self, plan, mode, interval_scheme):
        return audit_service.close_batch(self.raw_forms,self.accepted,self.history,plan,self.feature_stamp.fingerprint,self.forecaster.name,mode,interval_scheme)


def execute(documents, capacity=12, mode='balanced', *, as_of=96):
    if type(as_of) is not int or as_of not in range(91,3651):
        raise ValueError('截至工日超出当前排班表')
    if not isinstance(documents,(tuple,list)) or not 1<=len(documents)<=2500:
        raise ValueError('需要 1 至 2500 份可识别单据')
    dossier = ShiftDossier(documents,as_of)
    fitted_calibration, estimates = dossier.grading()
    evaluated, checked_live, drift = dossier.verification(estimates,fitted_calibration)
    # 区间方案只用校准期实绩拟合，用同一批成熟回放场次对比；当班样本不参与校准。
    regime = interval_service.build_interval_regime(
        estimates['calibration'], estimates['replay'], estimates['live'])
    # 整班一次性切换：当班每一张单都按选定方案取区间，缺成熟校准的组回落全馆。
    live_for_dispatch = tuple(
        calibration_service.check_point(point, regime.verification_for(point.cohort))
        for point in estimates['live'])
    blocked = interval_service.blocked_keys(regime, live_for_dispatch)
    allocation = dispatch_service.plan_jobs(live_for_dispatch,drift.domain,capacity,mode,blocked)
    closed = dossier.seal(allocation,mode,regime.selected)
    intake,windows,state,features,kernel = dossier.accepted,dossier.history,dossier.feature_stamp,dossier.prepared,dossier.forecaster
    scored = estimates['live']
    performance = calibration_service.performance(evaluated)
    coverage = monitor_service.coverage_by_group(features.calibration,features.live)
    states = dict(closed.totals.states)
    overview = [
        {'label': '当班影厅场次', 'value': len(allocation.jobs)},
        {'label': '已分派', 'value': states.get('已分派', 0)},
        {'label': '候补', 'value': states.get('候补', 0)},
        {'label': '拦截', 'value': states.get('拦截', 0)},
        {'label': '暂停及人工判读', 'value': states.get('暂停', 0) + states.get('人工判读', 0)},
        {'label': '额度结余', 'value': allocation.balance},
    ]
    vectors = {item.key: item for item in features.live}
    row_list = rows_for(allocation, scored, vectors, live_for_dispatch)
    operating_rows = dispatch_service.operating_ledger(row_list)
    overtime = calibration_service.boundary_diagnostics(evaluated, 15)
    sector = calibration_service.sector_replay(evaluated, 15)
    operating_shift = monitor_service.shop_floor_shift(features.train, features.live)
    field_evidence = audit_service.sign_field_evidence(row_list, allocation)
    features_glance = feature_service.feature_evidence(state) + feature_service.measure_review(features)
    weight_rows = [{'量测': key, '取值': value} for key, value in kernel.evidence]
    ignored = [issue.evidence() for issue in intake.issues]
    interval_book = interval_service.interval_evidence(regime)
    details = [
        stage_at(0, f'接收 {intake.source_rows} 条，采用 {len(intake.entries)} 条', intake_service.preliminary_evidence(intake), ignored),
        stage_at(1, f'拟合 {len(windows.train)} 条，校准 {len(windows.calibration)} 条，回放 {len(windows.replay)} 条', cohort_service.window_evidence(windows) + cohort_service.readiness_evidence(windows, as_of)),
        stage_at(2, '特征转换版本 ' + state.fingerprint, features_glance),
        stage_at(3, kernel.name, weight_rows + model_service.inspection_rows(kernel, features) + comparison_rows(scored)),
        stage_at(4, f'{fitted_calibration.method} · {performance.status} · {performance.count} 条回放实绩', calibration_service.calibration_evidence(performance) + [overtime] + sector),
        stage_at(5, f'{regime.selected} · 整班同一套区间', interval_stage_records(interval_book)),
        stage_at(6, f'{drift.status} · {len(drift.domain)} 张单据需外推判读', monitor_service.shift_evidence(drift, coverage) + operating_shift),
        stage_at(7, f'本班占用 {allocation.capacity-allocation.balance} / {allocation.capacity} 个工位单位', dispatch_service.action_evidence(allocation) + dispatch_service.operating_group(live_for_dispatch, allocation)),
        stage_at(8, f'处置凭证 {closed.stamp} · 额度守恒', audit_service.reconciliation_evidence(closed) + field_evidence['执行业务单']),
    ]
    details[7]['operating_rows'] = operating_rows
    details[7]['operating_title'] = '清场倒排窗口'
    points = [{'label': row['key'], 'value': row['estimate'], 'state': row['state'], 'zone': row['zone'],
               'upper': row['interval'], 'cost': row['cost']} for row in sorted(row_list, key=lambda row: row['key'])]
    def estimate(vector):
        return calibration_service.check_point(model_service.predict_one(kernel, vector), fitted_calibration).estimate

    # Compare interventions against frozen features, model and calibration;
    # scenario estimates never overwrite the base ticket or its decision.
    for row in row_list:
        current = vectors[row['key']]
        row['alternatives'] = feature_service.render_interventions(current, state, estimate)
        row['joint_experiments'] = feature_service.render_joint_experiments(current,state,estimate)
    details[8]['records'].extend(audit_service.site_checklist(row_list))
    details[8]['records'].extend(audit_service.intervention_summary(row_list))
    overview_report = {'capacity' : capacity, 'mode': mode, 'as_of': as_of, 'sample_hash': closed.batch_hash,
              'policy_hash': closed.policy_hash, 'feature_stamp': state.fingerprint,
              'model': kernel.name, 'run_id': closed.stamp, 'object_name': DISPLAY['object_name'],
              'unit': DISPLAY['unit'], 'sections': details, 'flow_counts': list(states.items()),
              'interval_policy': interval_book,
              'measures': {'seats': ('总座数', '席'),
 'filled': ('本场上座', '%'),
 'spill': ('报告污渍点', '处'),
 'minutes': ('片尾结束至下一场', 'min'),
 'bins': ('厅外垃圾桶', '个'),
 'stairs': ('清理阶梯', '级')}, 'metrics': overview, 'performance': replay_metrics(performance), 'rows': row_list, 'series': points}
    return overview_report


def preview(capacity=12, mode='balanced'):
    fixture = open_fixture()
    return execute(fixture['records'], capacity, mode, as_of=fixture['as_of'])
