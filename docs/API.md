# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 夏令时与本地时间

- 标准批（10列）event_time 必须带UTC偏移，无偏移返回 INVALID_TIME 隔离。
- 本地补传批（11列，末列 clock_offset）允许 event_time 为无偏移当地时间。
  - 普通钟点 UNIQUE；回拨重复钟点 AMBIGUOUS（两个UTC候选 fold=0/1）；春季前跳 SKIPPED 隔离。
  - clock_offset 显式偏移（如 -05:00）可对歧义消歧；补传顺序不得用于消歧。
  - 未消歧妥投：M4 path_decision=TIME_PENDING（delivered_at 为空，delivery_candidates 含两候选及 not_adopted_reason），M5 sla=时效挂起、sla_binding=临时、expected=0，仅给 provisional_* 临时答复。

## 新增端点

- `GET /api/runs/<id>/raw`：只读返回该批原始扫描（accepted_scans、intake_quarantine、input_sha256），任何重算/替换都不改写或删除。
- `POST /api/runs/<id>/claims`，body `{"parcel":"1002"}`：幂等赔付登记。首次 `claimed=true`；同(批次,包裹)重试 `claimed=false, already_claimed=true`，不重复计赔。时效挂起件（settleable=false）返回 409 NOT_SETTLEABLE。
- 归档 `/api/runs/<id>/archive` 增加 kind（accepted/time_pending/quarantine）、binding（正式/临时/隔离）、sla 列，evidence JSON 与页面同源，含候选时刻与未采用原因。

## 失败恢复

新批次采用“先完整计算、成功后再落库”，计算失败不会触碰既有批次，旧版结论保留；同源输入重算命中相同 run_id（INSERT OR IGNORE），不覆盖。
