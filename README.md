# 跨境包裹扫描补传与妥投时效核验

供跨境集运客服质控员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18247 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 扫描采信：`/scans`，`/api/scans`。
- 2. 节点映射：`/nodes`，`/api/nodes`。
- 3. 时区归整：`/timezones`，`/api/timezones`。
- 4. 路径裁决：`/paths`，`/api/paths`。
- 5. 时效计算：`/timeliness`，`/api/timeliness`。
- 6. 赔付对账：`/compensation`，`/api/compensation`。
- 7. 批次归档：`/archives`，`/api/archives`。

## 核心规则

以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。

### 夏令时回拨歧义裁决（TRAP-30～36）

承运商晚到补传只写当地时间，回拨日同一钟点出现两次。裁决口径：

1. **不按补传顺序采纳**：`received_time` 只算上传延迟，绝不能用于在两个真实时刻间二选一，否则可能把更晚的签收误判为达标。
2. **唯一时刻才进正式时效**：普通钟点直接定UTC；回拨重复钟点生成两个候选（fold=0/1），仅当承运商在 `clock_offset` 显式给出UTC偏移时才消歧为唯一正式时刻。
3. **拿不到唯一时刻则时效挂起**：M4 出 `TIME_PENDING` 临时路径并在页面列出每个候选时刻及未采用原因；M5 不计赔付，仅按**最晚候选**给客服当天临时答复（最保守），结论标注“临时”。
4. **证据下载读取同一结论**：归档CSV直接序列化该批次结果，挂起件标注 `time_pending/临时` 并附两个候选。
5. **失败恢复旧版、原始扫描不误删、重试不重复计赔**：重算先算后存，失败不触库；`/raw` 只读原始扫描；赔付经幂等登记，挂起件不可结算（409）。

11列本地补传批（含 `clock_offset`）见 `sample_data/dst.csv`；10列标准批仍拒绝无偏移时间。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据。
3. 重跑相同样例，批次号与行数保持一致。
4. 复核后导出一份证据CSV。版本已变化的复核提交返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出。`POST /api/runs/<id>/claims`按(批次,包裹)幂等登记赔付，重试不重复计赔，时效挂起件返回409；`GET /api/runs/<id>/raw`只读返回原始扫描，重算/替换均不删除。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
