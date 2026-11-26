from collections import Counter, defaultdict

from ..slot_backcheck import service as backcheck
from .contracts import (
    ACCEPTANCE_BUFFER,
    GLOBAL_SCHEME,
    GROUPED_SCHEME,
    MIN_CALIBRATION_SAMPLES,
    GroupCalibration,
    GroupReplayRow,
    IntervalRegime,
    ReplayDecision,
)


def _keys(points):
    return frozenset(point.key for point in points)


def assert_windows_disjoint(calibration, replay, live):
    """校准、回放、当班样本必须互不相通，防止泄漏到区间校准。"""
    calib_keys, replay_keys, live_keys = _keys(calibration), _keys(replay), _keys(live)
    if calib_keys & replay_keys or calib_keys & live_keys or replay_keys & live_keys:
        raise ValueError('校准、回放与当班样本存在重叠，禁止同批样本跨窗使用')


def per_group_calibrations(calibration):
    table = []
    for cohort in sorted({point.cohort for point in calibration}):
        verification = backcheck.prepare_verification(
            [point for point in calibration if point.cohort == cohort])
        eligible = bool(verification.parameters) and verification.size >= MIN_CALIBRATION_SAMPLES
        if verification.parameters == ():
            note = '无成熟校准实绩，沿用全馆区间'
        elif verification.size < MIN_CALIBRATION_SAMPLES:
            note = f'校准实绩不足 {MIN_CALIBRATION_SAMPLES} 条，沿用全馆区间'
        else:
            note = '分组区间可用'
        table.append(GroupCalibration(cohort, verification, eligible, note))
    return tuple(table)


def clear_gap(point):
    # input_values 顺序：seats, filled, spill, minutes, bins, stairs
    return point.input_values[3] - ACCEPTANCE_BUFFER


def assess_ticket(point, verification):
    checked = backcheck.check_point(point, verification)
    gap = clear_gap(point)
    over_window = point.outcome is not None and point.observed and point.outcome > gap
    blocked = checked.upper is not None and checked.upper > gap
    return ReplayDecision(
        key=point.key, cohort=point.cohort, estimate=checked.estimate,
        lower=checked.lower, upper=checked.upper,
        outcome=point.outcome, clear_gap=gap,
        blocked=blocked, over_window=bool(over_window),
        misblocked=blocked and not over_window,
        missed=(not blocked) and over_window,
        uncertain=checked.uncertain,
    )


def replay_decisions(replay, scheme):
    """scheme 为 cohort -> Verification；未登记的组回落到全馆 Verification。"""
    global_verification = scheme[None]
    decisions = []
    for point in replay:
        decisions.append(assess_ticket(point, scheme.get(point.cohort, global_verification)))
    return tuple(decisions)


def replay_metrics_by_group(decisions, live_groups=()):
    grouped = defaultdict(list)
    for decision in decisions:
        grouped[decision.cohort].append(decision)
    rows = []
    names = sorted(set(grouped) | set(live_groups))
    for cohort in names:
        items = grouped.get(cohort, ())
        if not items:
            rows.append(GroupReplayRow(cohort, 0, 0, 0, 0, 0, '未到实绩回报时间'))
            continue
        rows.append(GroupReplayRow(
            cohort, len(items),
            sum(item.blocked for item in items),
            sum(item.misblocked for item in items),
            sum(item.missed for item in items),
            sum(item.over_window for item in items),
            '回放实绩充足',
        ))
    return tuple(rows)


def _totals(rows):
    return {
        'replay_samples': sum(row.replay_samples for row in rows),
        'blocked': sum(row.blocked for row in rows),
        'misblocked': sum(row.misblocked for row in rows),
        'missed': sum(row.missed for row in rows),
        'over_window': sum(row.over_window for row in rows),
    }


def select_scheme(global_replay, grouped_replay, global_rows, grouped_rows,
                  live_groups, group_calibrations):
    totals_global = _totals(global_rows)
    totals_grouped = _totals(grouped_rows)
    by_group_global = {row.cohort: row for row in global_rows}
    by_group_grouped = {row.cohort: row for row in grouped_rows}
    guards = []

    fewer_misblocks = totals_grouped['misblocked'] < totals_global['misblocked']
    guards.append({
        '守卫': '分组区间少拦按时场次',
        '全馆误拦': totals_global['misblocked'], '分组误拦': totals_grouped['misblocked'],
        '通过': fewer_misblocks,
    })

    worse_groups = sorted(
        cohort for cohort in by_group_grouped
        if by_group_grouped[cohort].missed > by_group_global.get(
            cohort, GroupReplayRow(cohort, 0, 0, 0, 0, 0, '')).missed)
    no_extra_misses = not worse_groups
    guards.append({
        '守卫': '任何影厅组不得多漏放超窗场次',
        '多漏放分组': '、'.join(worse_groups) or '—', '通过': no_extra_misses,
    })

    replay_by_group = Counter(decision.cohort for decision in global_replay)
    uncovered = sorted(cohort for cohort in live_groups if replay_by_group.get(cohort, 0) == 0)
    replay_complete = not uncovered
    guards.append({
        '守卫': '当班各影厅组均有回放实绩',
        '缺回放实绩分组': '、'.join(uncovered) or '—', '通过': replay_complete,
    })

    sparse_live = sorted(
        cohort for cohort in live_groups
        if not any(row.cohort == cohort and row.eligible for row in group_calibrations))
    guards.append({
        '守卫': '缺成熟校准实绩的组回落全馆区间',
        '回落分组': '、'.join(sparse_live) or '—',
        '通过': True,
    })

    adopted = fewer_misblocks and no_extra_misses and replay_complete
    reasons = []
    if not fewer_misblocks:
        reasons.append('分组区间未减少误拦按时场次，沿用全馆区间')
    if not no_extra_misses:
        reasons.append('分组区间存在多漏放超窗场次的影厅组，沿用全馆区间')
    if not replay_complete:
        reasons.append('当班分组缺回放实绩，沿用全馆区间')
    if adopted:
        reasons.append('分组区间通过全部守卫，整班切换至分组区间')
    return (GROUPED_SCHEME if adopted else GLOBAL_SCHEME), tuple(guards), tuple(reasons)


def build_interval_regime(calibration, replay, live, *, coverage=.85):
    assert_windows_disjoint(calibration, replay, live)
    global_verification = backcheck.prepare_verification(calibration, coverage=coverage)
    group_calibrations = per_group_calibrations(calibration)

    global_scheme = {None: global_verification}
    grouped_scheme = {None: global_verification}
    for row in group_calibrations:
        if row.eligible:
            grouped_scheme[row.cohort] = row.verification

    global_replay = replay_decisions(replay, global_scheme)
    grouped_replay = replay_decisions(replay, grouped_scheme)
    live_groups = sorted({point.cohort for point in live})
    global_rows = replay_metrics_by_group(global_replay, live_groups)
    grouped_rows = replay_metrics_by_group(grouped_replay, live_groups)

    selected, guards, reasons = select_scheme(
        global_replay, grouped_replay, global_rows, grouped_rows,
        live_groups, group_calibrations)

    chosen_scheme = grouped_scheme if selected == GROUPED_SCHEME else global_scheme
    blocked_live = tuple(sorted(
        point.key for point in live
        if assess_ticket(point, chosen_scheme.get(point.cohort, global_verification)).blocked))

    return IntervalRegime(
        selected=selected,
        global_verification=global_verification,
        group_calibrations=group_calibrations,
        global_replay=global_replay,
        grouped_replay=grouped_replay,
        global_rows=global_rows,
        grouped_rows=grouped_rows,
        blocked_live=blocked_live,
        guards=guards,
        reasons=reasons,
    )


def blocking_violation(assessment):
    """区间上界越过清场空档（已预留开门验收时间）即应拦下。"""
    if assessment.upper is None or assessment.uncertain:
        return None
    gap = clear_gap(assessment.raw)
    if assessment.upper > gap:
        return {
            'rule_id': 'INTERVAL_OVER_CLEAR_GAP',
            'meaning': '区间上界超出清场空档，预留开门验收后无法按时交厅',
            'upper': round(assessment.upper, 4),
            'clear_gap': gap,
        }
    return None


def blocked_keys(regime, live_assessments):
    return frozenset(
        assessment.raw.key for assessment in live_assessments
        if blocking_violation(assessment) is not None)


def _row_dicts(rows):
    return [
        {'影厅组': row.cohort, '回放实绩': row.replay_samples, '拦下': row.blocked,
         '误拦按时': row.misblocked, '漏放超窗': row.missed, '实绩超窗': row.over_window,
         '状态': row.status}
        for row in rows
    ]


def interval_evidence(regime):
    calibration_rows = [
        {'影厅组': row.cohort, '校准实绩': row.verification.size,
         '区间方法': row.verification.method, '资格': row.note}
        for row in regime.group_calibrations
    ]
    return {
        '选定方案': regime.selected,
        '切换说明': list(regime.reasons),
        '守卫': list(regime.guards),
        '分组校准': calibration_rows,
        '全馆回放': _row_dicts(regime.global_rows),
        '分组回放': _row_dicts(regime.grouped_rows),
        '区间红线标记当班单': list(regime.blocked_live),
    }
