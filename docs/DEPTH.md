# 影厅清场交班轨验证目录

AI/ML · 传统ML · 中型

修改业务链路时先检查受影响模块的测试，再运行整套 `pytest`。回归记录须能够定位到具体单据或计算值。

## 功能模块

| 编号 | 功能 | 核对内容 | 核心文件 | 独立测试 |
|---|---|---|---|---|
| M1 | 场次交接 | 单据版次与隔离 | `src/cinemaready/show_handoffs/service.py` | `tests/test_show_handoffs.py` |
| M2 | 影厅日程 | 截止日和成熟标签 | `src/cinemaready/turnover_calendar/service.py` | `tests/test_turnover_calendar.py` |
| M3 | 清理因素 | 原始数据到特征 | `src/cinemaready/hall_signature/service.py` | `tests/test_hall_signature.py` |
| M4 | 耗时滚动 | 训练及可重复推断 | `src/cinemaready/cleanup_trend/service.py` | `tests/test_cleanup_trend.py` |
| M5 | 窗口复算 | 回放误差及可信区间 | `src/cinemaready/slot_backcheck/service.py` | `tests/test_slot_backcheck.py` |
| M6 | 区间治理 | 分组/全馆区间守卫与拦截 | `src/cinemaready/interval_governance/service.py` | `tests/test_interval_governance.py`、`tests/test_interval_workbench.py` |
| M7 | 排片分组 | 差异定位和外推审查 | `src/cinemaready/programming_mix/service.py` | `tests/test_programming_mix.py` |
| M8 | 清场计划 | 硬规则与额度分配 | `src/cinemaready/usher_windows/service.py` | `tests/test_usher_windows.py` |
| M9 | 开厅交接 | 单据及额度证据 | `src/cinemaready/hall_handover/service.py` | `tests/test_hall_handover.py` |

## 深度登记

| 编号 | 边界 | 判断 | 处理位置 | 测试定位 |
|---|---|---|---|---|
| TRAP-14 | 同版不同读数 | 两条都隔离，换序结论一致 | `src/cinemaready/show_handoffs/service.py` | `tests/test_show_handoffs.py::test_conflicting_same_version_is_order_independent` |
| TRAP-19 | 多个规则或外推标记重叠 | 硬约束优先，保留全部命中码 | `src/cinemaready/usher_windows/service.py` | `tests/test_usher_windows.py::test_hard_rule_precedes_outside_reference` |
| TRAP-23 | 标签落在窗口的终点 | 终点进入隔离段，窗口为半开区间 | `src/cinemaready/turnover_calendar/service.py` | `tests/test_turnover_calendar.py::test_half_open_days` |
| TRAP-32 | 大偏移细方差与常数列 | 中心化后累加，常数列尺度为一 | `src/cinemaready/hall_signature/service.py` | `tests/test_hall_signature.py::test_cancellation_safe_scaling` |
| TRAP-42 | 拟合窗口后才回传结果 | 依标签成熟时间屏蔽，未来数据不参与拟合 | `src/cinemaready/turnover_calendar/service.py` | `tests/test_turnover_calendar.py::test_mature_label_cannot_cross_partition` |
| TRAP-43 | 回放没有成熟记录 | 记录未定义状态，避免以零误差替代 | `src/cinemaready/slot_backcheck/service.py` | `tests/test_slot_backcheck.py::test_no_outcome_gets_undefined_metrics` |
| TRAP-44 | 校准样本不足 | 分派前转人工判读并保留原因 | `src/cinemaready/slot_backcheck/service.py` | `tests/test_slot_backcheck.py::test_empty_calibration_requires_review` |
| TRAP-53 | 单条推断与混批 | 复用冻结参数，各条估计完全一致 | `src/cinemaready/cleanup_trend/service.py` | `tests/test_cleanup_trend.py::test_input_batch_invariance` |
| TRAP-54 | 特征版本与模型不配对 | 拒绝继续推断并要求重拟合 | `src/cinemaready/cleanup_trend/service.py` | `tests/test_cleanup_trend.py::test_mismatched_model_input_version_fails` |
| TRAP-61 | 回放或当班样本泄漏进区间校准 | 三窗单号重叠即拒绝，区间只吃校准窗 | `src/cinemaready/interval_governance/service.py` | `tests/test_interval_governance.py::test_windows_must_be_disjoint` |
| TRAP-62 | 分组区间未减少误拦却切换 | 误拦持平也回落全馆，守卫逐条留痕 | `src/cinemaready/interval_governance/service.py` | `tests/test_interval_governance.py::test_grouped_scheme_adopted_only_when_misblocks_drop_without_new_misses` |
| TRAP-63 | 某组多漏放超窗场次 | 任一组漏放高于全馆即否决分组方案 | `src/cinemaready/interval_governance/service.py` | `tests/test_interval_governance.py::test_more_missed_releases_in_any_group_blocks_grouped_scheme` |
| TRAP-64 | 当班分组缺回放实绩 | 守卫否决并沿用全馆，不用零误差替代 | `src/cinemaready/interval_governance/service.py` | `tests/test_interval_workbench.py::test_sample_shift_keeps_global_scheme_with_explicit_guards` |
| TRAP-65 | 拦下的单占用工位 | 拦截不进候选、不耗额度，硬规则仍优先 | `src/cinemaready/usher_windows/service.py` | `tests/test_interval_governance.py::test_blocked_ticket_never_occupies_workstation` |
