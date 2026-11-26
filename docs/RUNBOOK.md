# TURNAROUND运行手册

更换班次或复算派单时使用。先确认 `/health` 返回 `ok`，再检查以下入口和凭证中的 `run_id`。

| 文档或路由 | 用途 |
|---|---|
| `sample_data/show_handoffs_sample.json` | 同时提供拟合、校准、回放和当班的样例 |
| `GET /api/cinemaready/overview` | 默认额度和样例处置 |
| `POST /api/cinemaready/run` | 用 `capacity` 和 `strategy` 调整派单，也可同时提供 `records` 与 `as_of` |
| `GET /api/cinemaready/stages/<环节>` | 样例的分环节核对，可传相同额度、策略 |

导入 JSON 对象时 `records` 为 1–2500 条单据，`as_of` 为整数工日；默认第 96 日。额度接受 1–60，策略接受 `balanced` 或 `focus`。入站异常保留于第一环节。整批缺少成熟窗口时响应 422 并给出原因。

```sh
docker compose exec -T app python -m cinemaready.entry evaluate --input sample_data/show_handoffs_sample.json --capacity 9
```

在网页筛选单号、作业区或原因，点击结果中的单号查看完整字段与处置证据。下载 JSON 可检视结果、对照原始资料摘要和模型特征版本。

## 估时区间方案

每次复算都会在「区间治理」环节（路由 `GET /api/cinemaready/stages/interval_governance`，或结果中的 `interval_policy` 字段）给出整班唯一的区间方案：

- `选定方案` 为 `全馆区间` 或 `分组区间`；切换说明和四条守卫（少拦按时、不得多漏放、回放实绩覆盖、缺校准回落）逐条留痕。
- `全馆回放`、`分组回放` 给出逐影厅组的拦下、误拦按时、漏放超窗、实绩超窗计数；未成熟组标注「未到实绩回报时间」。
- 区间上界越过 `minutes − 2`（预留开门验收）的当班单列入 `区间红线标记当班单`，派单状态为「拦截」，不占工位；若同时命中硬规则则仍按「暂停」处置。
- 方案标识进入凭证 `policy_hash`，分派与导出始终共用同一套区间；改变方案会改变 `run_id`。
