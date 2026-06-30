# 自驱 codex /goal 的强契约模板

本文定义 curryflows 自驱线程(codex `/goal`)的强目标契约模板,基于
`references/goal-cookbook.md`。自驱模式适用于长程不确定调查(profiling、复现 flaky
测试、依赖迁移、bug hunt、benchmark 调优、把研究问题做成证据支撑的审计);一次性编辑或
短 review 不该用 /goal(见 goal-cookbook.md「When not to use Goals」)。

自驱线程必须挂只读审计 + Esc 急停(见 `coordinator.md`、`codex-integration.md`),且
goal 本身必须是强契约。

## 七个必填字段

goal-cookbook.md 给出 six things;curryflows 在其上把 BUDGET 提为独立的第七个必填字段并
设为硬上限。一个 /goal 必须填全这七个字段:

1. **OUTCOME** — 工作完成时什么应为真(可度量的终态)。
2. **VERIFICATION** — 证明它的证据面:测试、benchmark、报告、产物、命令输出或原始材料。
3. **CONSTRAINTS** — codex 工作期间什么不得回退。
4. **BOUNDARIES** — codex 可用的文件、工具、数据、仓库、资源范围。
5. **ITERATION** — 每次尝试后 codex 如何决定下一步试什么。
6. **BLOCKED_STOP** — 在当前限制下没有可辩护路径时,codex 何时停下并报告、什么能解锁进展。
7. **BUDGET** — 硬上限(token / 轮次)。到顶 = 停 + 总结进展和 blocker,**不是完成**。

## 不许启动的弱 goal

缺 **BUDGET** 或 **BLOCKED_STOP** 的弱 goal 不许启动。那正是「一个 codex /goal 跑出约
1.9 亿 token、3.7 天无人察觉」的形态——没有硬上限就没有自然停点,没有 blocked-stop 就会
在没有可辩护路径时继续空转。协调器在 advance 自驱线程前必须核对这两个字段存在且具体。

两条硬规则:

- **BUDGET 是硬上限,到顶就停**:到达 budget 后 codex 应停止实质工作、总结进展与
  blocker、指出下一步,而不是把「到顶」当作「完成」(见 goal-cookbook.md「Budget
  handling」)。
- **完成必须基于证据,不是模型自认为完成**:goal 不能因为模型觉得「大概做完了」就标记
  complete,必须把 OUTCOME 对照 VERIFICATION 列出的具体证据核对后才算完成
  (见 goal-cookbook.md「completion must be evidence-based」)。

## 示例:研究类

```text
/goal
OUTCOME: 对 <论文/课题> 产出最强的证据支撑复现,把 confirmed findings、approximate
  reconstructions、blocked claims、remaining uncertainty 四类分开。
VERIFICATION: 一份 claim-by-claim 审计报告;每条 claim 映射到具体证据(可本地跑的检查、
  重建的图表、数值对照),并标注其 epistemic 支撑等级。
CONSTRAINTS: 不得把 approximate reconstruction 或 close numerical match 描述成 exact
  replay;不得在缺源材料(seeds / checkpoints / 训练状态)时谎称精确复现。
BOUNDARIES: 仅用 <提供的论文材料 + 本地资源 + 指定数据集>;不联网取额外数据。
ITERATION: 每轮记录:本轮试了什么 claim、证据显示了什么、下一条最值得攻的 claim 是哪条。
BLOCKED_STOP: 若某 claim 因缺源材料无法精确复现,标 blocked 并说明缺什么、什么能解锁,
  继续推进其余 claim,而非空转。
BUDGET: 上限 <N> tokens(或 <M> 轮)。到顶即停,输出已确认/近似/受阻/不确定四类的当前
  审计,不视为完成。
```

## 示例:工程类(性能优化)

```text
/goal
OUTCOME: 把 <服务> 的 p95 延迟降到 120ms 以下。
VERIFICATION: <checkout benchmark> 显示 p95 < 120ms,且正确性测试套件全绿。
CONSTRAINTS: 正确性套件不得回退;不改动公共 API 行为。
BOUNDARIES: 仅改 <checkout 服务 + benchmark fixtures + 相关测试>。
ITERATION: 每轮记录:改了什么、benchmark 数字如何、下一个最值得做的实验是什么。
BLOCKED_STOP: 若 benchmark 跑不起来或无有效路径,停下并给出已试路径、已得证据、blocker、
  下一步需要的输入,而不是继续乱试。
BUDGET: 上限 <N> tokens(或 <M> 轮)。到顶即停 + 总结当前最好结果与 blocker,不视为完成。
```

## 启动后立即注册到 board

每个自驱线程一启动,协调器必须立即把它注册到 board(看板 jsonl 的唯一写入者是 `board.py`,绝不手编):

```bash
python3 <skillDir>/scripts/board.py upsert-thread --board ./.curryflows/board \
  --id <thread-id> --codex-session <rollout 的 session uuid> \
  --budget-tokens <BUDGET 的硬上限> --state running --branch curryflows/<thread-id>
```

注册的目的:`discover-threads.py` 在 tick 第 1 步会把 active codex 会话的 `session_id`
与 board 的 `codex_session` 集合对账。已注册的会话才不会被标成 `UNREGISTERED`、不会触发
exit 2、不会被协调器当 runaway 软停(见 `coordinator.md` tick 第 1 步、
`codex-integration.md`「discover-threads.py」)。线程一经注册(`state=running` + `codex_session`),
即纳入协调器每-tick 的 reviewer 只读审计 + Esc 急停(见 `coordinator.md`「监督拆分」);**不再用单独的
`overseer` 标记字段**——审计是每-tick reviewer 的常规职责,而非挂在某条线程上的一次性标记。
