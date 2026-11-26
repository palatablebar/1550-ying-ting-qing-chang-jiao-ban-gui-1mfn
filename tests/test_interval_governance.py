from dataclasses import replace

import pytest

from cinemaready.cleanup_trend.contracts import Prediction
from cinemaready.interval_governance import service as interval
from cinemaready.interval_governance.contracts import (
    GLOBAL_SCHEME,
    GROUPED_SCHEME,
    MIN_CALIBRATION_SAMPLES,
)
from cinemaready.slot_backcheck.contracts import Assessment
from cinemaready.usher_windows.contracts import Policy
from cinemaready.usher_windows.service import plan_jobs, recommend


def make_prediction(key, cohort, *, day, score=10.0, outcome=None, minutes=30.0, observed=True):
    return Prediction(
        key=key, cohort=cohort, day=day, score=score,
        contributions=(0.1, 0.2, 0.3, 0.4, 0.5), missing=(), excursions=(),
        outcome=outcome, observed=observed,
        input_values=(200.0, 60.0, 10.0, minutes, 6.0, 20.0),
    )


def calibration_batch(prefix='C'):
    rows = []
    for hall in ('甲厅', '乙厅'):
        for i in range(MIN_CALIBRATION_SAMPLES + 2):
            # 甲厅残差小、区间紧；乙厅残差大、区间宽
            score = 10.0 if hall == '甲厅' else 8.0
            outcome = score + (0.2 if hall == '甲厅' else 3.0)
            rows.append(make_prediction(f'{prefix}-{hall}-{i}', hall, day=50,
                                        score=score, outcome=outcome))
    return rows


def replay_ticket(key, cohort, *, score, outcome, minutes):
    return make_prediction(key, cohort, day=70, score=score, outcome=outcome, minutes=minutes)


def live_ticket(key, cohort, *, score=10.0, minutes=30.0):
    return make_prediction(key, cohort, day=92, score=score, minutes=minutes)


def test_windows_must_be_disjoint():
    shared = make_prediction('X1', '甲厅', day=50, outcome=10.0)
    with pytest.raises(ValueError, match='重叠'):
        interval.build_interval_regime((shared,), (shared,), ())


def test_group_without_mature_calibration_falls_back_to_global():
    calibration = calibration_batch()
    replay = [replay_ticket('R1', '甲厅', score=10, outcome=10.2, minutes=30)]
    live = [live_ticket('L1', '甲厅'), live_ticket('L2', '丙厅')]
    regime = interval.build_interval_regime(calibration, replay, live)
    notes = {row.cohort: row for row in regime.group_calibrations}
    assert '丙厅' not in notes  # 校准期没有该组
    # 未成熟组在任何方案下都取全馆区间
    assert regime.verification_for('丙厅') == regime.global_verification
    guard = next(item for item in regime.guards if '回落' in item['守卫'])
    assert '丙厅' in guard['回落分组'] and guard['通过']


def test_sparse_group_calibration_is_marked_ineligible():
    calibration = calibration_batch() + [
        make_prediction('C-丁-0', '丁厅', day=50, score=9.0, outcome=9.5)]
    rows = interval.per_group_calibrations(calibration)
    ding = next(row for row in rows if row.cohort == '丁厅')
    assert not ding.eligible and '沿用全馆区间' in ding.note


def test_misblock_and_missed_are_classified_on_replay():
    calibration = calibration_batch()
    # 清场空档 = minutes - 2；实绩超窗当且仅当 outcome > minutes - 2
    replay = (
        replay_ticket('R-ok', '甲厅', score=10, outcome=10.0, minutes=30),
        replay_ticket('R-misblock', '甲厅', score=10, outcome=9.0, minutes=12),
        replay_ticket('R-miss', '乙厅', score=8, outcome=20.0, minutes=20),
    )
    regime = interval.build_interval_regime(calibration, replay, [live_ticket('L1', '甲厅')])
    decisions = {item.key: item for item in regime.global_replay}
    assert decisions['R-ok'].blocked is False
    assert decisions['R-misblock'].misblocked and not decisions['R-misblock'].over_window
    assert decisions['R-miss'].missed and decisions['R-miss'].over_window


def test_missing_replay_for_live_group_keeps_global_scheme():
    calibration = calibration_batch()
    replay = [replay_ticket('R1', '甲厅', score=10, outcome=10.2, minutes=12)]
    live = [live_ticket('L1', '甲厅'), live_ticket('L2', '乙厅')]
    regime = interval.build_interval_regime(calibration, replay, live)
    assert regime.selected == GLOBAL_SCHEME
    guard = next(item for item in regime.guards if '回放实绩' in item['守卫'])
    assert guard['通过'] is False and '乙厅' in guard['缺回放实绩分组']


def test_more_missed_releases_in_any_group_blocks_grouped_scheme():
    # 甲厅短空档且残差大，主导全馆外分位；乙厅自身残差小、分组上界紧。
    # 回放里乙厅一场实绩超窗：全馆上界拦得住，乙厅分组上界漏放。
    calibration = []
    for i in range(MIN_CALIBRATION_SAMPLES + 4):
        calibration.append(make_prediction(f'C-甲-{i}', '甲厅', day=50, score=10, outcome=13, minutes=14))
        calibration.append(make_prediction(f'C-乙-{i}', '乙厅', day=50, score=10, outcome=10.3, minutes=30))
    replay = [replay_ticket('R1', '乙厅', score=10, outcome=12.5, minutes=14)]
    live = [live_ticket('L1', '甲厅'), live_ticket('L2', '乙厅')]
    regime = interval.build_interval_regime(calibration, replay, live)
    grouped = {row.cohort: row for row in regime.grouped_rows}
    global_rows = {row.cohort: row for row in regime.global_rows}
    assert grouped['乙厅'].missed == 1 and global_rows['乙厅'].missed == 0
    assert regime.selected == GLOBAL_SCHEME
    guard = next(item for item in regime.guards if '漏放' in item['守卫'])
    assert guard['通过'] is False and '乙厅' in guard['多漏放分组']


def test_grouped_scheme_adopted_only_when_misblocks_drop_without_new_misses():
    # 甲厅校准残差极小 -> 分组上界紧，可少拦按时场次；乙厅保持中性
    calibration = []
    for i in range(MIN_CALIBRATION_SAMPLES + 4):
        calibration.append(make_prediction(f'C-甲-{i}', '甲厅', day=50, score=10,
                                           outcome=10.0 + 0.05 * (i % 3)))
        calibration.append(make_prediction(f'C-乙-{i}', '乙厅', day=50, score=10,
                                           outcome=10.0 + (5.0 if i < 7 else 0.0)))
    # minutes=13、空档 11：全馆上界 12.5 拦下，甲厅分组上界 10.05 放行且实绩 10.2 未超窗
    replay = [
        replay_ticket('R1', '甲厅', score=10, outcome=10.2, minutes=13),
        replay_ticket('R2', '乙厅', score=10, outcome=10.4, minutes=30),
    ]
    live = [live_ticket('L1', '甲厅'), live_ticket('L2', '乙厅')]
    regime = interval.build_interval_regime(calibration, replay, live)
    totals_global = interval._totals(regime.global_rows)
    totals_grouped = interval._totals(regime.grouped_rows)
    assert totals_grouped['misblocked'] < totals_global['misblocked']
    assert totals_grouped['missed'] == totals_global['missed'] == 0
    assert regime.selected == GROUPED_SCHEME
    assert all(guard['通过'] for guard in regime.guards)
    # 整班切换：甲厅拿到自己的校准，未知组回落全馆
    assert regime.verification_for('甲厅') != regime.global_verification
    assert regime.verification_for('未知厅') == regime.global_verification


def test_blocking_violation_reserves_acceptance_buffer(prediction):
    # minutes=20 -> 清场空档 18；上界 19 触发拦截，上界 18 不触发
    raw = replace(prediction, input_values=(200.0, 60.0, 10.0, 20.0, 6.0, 20.0))
    over = Assessment(raw, 12.0, 9.0, 19.0, False)
    fit = Assessment(raw, 12.0, 9.0, 18.0, False)
    uncertain = Assessment(raw, 12.0, None, None, True)
    assert interval.blocking_violation(over)['rule_id'] == 'INTERVAL_OVER_CLEAR_GAP'
    assert interval.blocking_violation(fit) is None
    assert interval.blocking_violation(uncertain) is None


def test_blocked_ticket_never_occupies_workstation(prediction):
    raw = replace(prediction, key='BLK', input_values=(200.0, 60.0, 10.0, 20.0, 6.0, 20.0))
    blocked_view = Assessment(raw, 12.0, 9.0, 19.5, False)
    plan = plan_jobs([blocked_view], (), 5, 'focus', blocked=frozenset({'BLK'}))
    job = plan.jobs[0]
    assert job.state == '拦截' and job.reason == 'INTERVAL_OVER_CLEAR_GAP'
    assert plan.balance == plan.capacity == 5
    # 拦下的单不进入候选，硬规则仍优先于拦截
    hard_raw = replace(prediction, key='HARD', input_values=(248.0, 89.0, 18.0, 15.0, 5.0, 30.0))
    hard = Assessment(hard_raw, 30.0, 25.0, 40.0, False)
    decision = recommend(hard, frozenset(), Policy.select(5, 'focus'), blocked=frozenset({'HARD'}))
    assert decision.state == '暂停'


def test_regime_is_one_shot_for_entire_shift():
    calibration = calibration_batch()
    replay = [replay_ticket('R1', '甲厅', score=10, outcome=10.1, minutes=30)]
    live = [live_ticket('L1', '甲厅'), live_ticket('L2', '乙厅')]
    regime = interval.build_interval_regime(calibration, replay, live)
    # 无论分组还是全馆，同一班内所有组通过同一入口取方案，方案标记唯一
    selections = {regime.selected}
    assert selections <= {GLOBAL_SCHEME, GROUPED_SCHEME}
    for cohort in ('甲厅', '乙厅', '未知厅'):
        verification = regime.verification_for(cohort)
        assert verification is not None


def test_empty_replay_window_is_flagged_not_treated_as_zero_error():
    calibration = calibration_batch()
    live = [live_ticket('L1', '甲厅')]
    regime = interval.build_interval_regime(calibration, [], live)
    assert regime.selected == GLOBAL_SCHEME
    row = regime.global_rows[0]
    assert row.replay_samples == 0 and row.status == '未到实绩回报时间'
    guard = next(item for item in regime.guards if '回放实绩' in item['守卫'])
    assert guard['通过'] is False and '甲厅' in guard['缺回放实绩分组']


def test_coverage_is_forwarded_to_verification():
    calibration = calibration_batch()
    replay = [replay_ticket('R1', '甲厅', score=10, outcome=10.1, minutes=30)]
    regime = interval.build_interval_regime(calibration, replay, [live_ticket('L1', '甲厅')],
                                            coverage=.5)
    assert regime.global_verification.size == len(calibration)
