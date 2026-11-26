from dataclasses import dataclass, field

from ..slot_backcheck.contracts import Verification

# 以片尾为起点预留 2 分钟开门验收时间，区间上界不得越过该时点。
ACCEPTANCE_BUFFER = 2
# 分组校准至少需要的成熟校准实绩；不足则该组不具备分组区间资格。
MIN_CALIBRATION_SAMPLES = 6

GLOBAL_SCHEME = '全馆区间'
GROUPED_SCHEME = '分组区间'


@dataclass(frozen=True, slots=True)
class GroupCalibration:
    cohort: str
    verification: Verification
    eligible: bool
    note: str


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    key: str
    cohort: str
    estimate: float
    lower: float | None
    upper: float | None
    outcome: float
    clear_gap: float
    blocked: bool
    over_window: bool
    misblocked: bool
    missed: bool
    uncertain: bool


@dataclass(frozen=True, slots=True)
class GroupReplayRow:
    cohort: str
    replay_samples: int
    blocked: int
    misblocked: int
    missed: int
    over_window: int
    status: str


@dataclass(frozen=True, slots=True)
class IntervalRegime:
    """整班一次性切换的估时区间方案。"""
    selected: str
    global_verification: Verification
    group_calibrations: tuple[GroupCalibration, ...]
    global_replay: tuple[ReplayDecision, ...]
    grouped_replay: tuple[ReplayDecision, ...]
    global_rows: tuple[GroupReplayRow, ...]
    grouped_rows: tuple[GroupReplayRow, ...]
    blocked_live: tuple[str, ...]
    guards: tuple[dict, ...] = field(default=())
    reasons: tuple[str, ...] = field(default=())

    @property
    def grouped_adopted(self):
        return self.selected == GROUPED_SCHEME

    def verification_for(self, cohort):
        if not self.grouped_adopted:
            return self.global_verification
        for row in self.group_calibrations:
            if row.cohort == cohort and row.eligible:
                return row.verification
        return self.global_verification

    def has_bounds(self, cohort):
        return self.verification_for(cohort).parameters != ()
