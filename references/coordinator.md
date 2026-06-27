# 协调器 tick runbook(`/loop` 动态模式)

协调器是 curryflows 三层控制流的外层调度(见 `SKILL.md`)。它以 `/loop` 动态模式运行:
每个 tick 推进就绪线程,无就绪时 park 释放上下文,被事件(线程完成、人类回复、定时唤醒)
重新拉起。这一层是 agent 推理,不是 Workflow 脚本。

## 状态落盘:board 是真相源

协调器自身的上下文窗口不是状态存储。所有在途状态落盘在目标项目里:

```
<project>/.curryflows/board/threads.jsonl     # 每行一个线程记录
<project>/.curryflows/board/decisions.jsonl    # 每行一个决策项
```

board 是真相源(source of truth),不是上下文。每个 tick 开始时,协调器从 board 读回状态,
而不是依赖上一个 tick 残留在上下文里的记忆;tick 结束时,把状态变更写回 board。这样做的原因:
协调器会 park、会被压缩(compact)、会跨多个唤醒周期存活,上下文不可靠;board 文件可靠。

`threads.jsonl` 每行至少携带这些字段(其中 `codex_session`、`branch` 被
`discover-threads.py` 用于对账,见 `codex-integration.md`):

```json
{"thread_id": "...", "template": "feature|perf|test", "state": "ready|running|build-done|blocked-human|merged|rolled-back",
 "branch": "curryflows/<thread-id>", "worktree": "<path>",
 "codex_session": "<uuid|null>", "budget_tokens": <int|null>, "overseer": "attached|null"}
```

`decisions.jsonl` 每行一个决策项(格式细节见 `decision-surface.md`),状态在
`open` / `resolved` / `rejected` 之间流转。

## tick 六步

每个 tick 严格按以下顺序执行。

### 1) discover & reconcile(先发现,再对账)

tick 第一件事是跑统一资源发现,把所有在途 codex 会话 + worktree 与 board 对账:

```bash
python3 scripts/discover-threads.py --project . \
  --board ./.curryflows/board/threads.jsonl
```

判读发现结果(退出码与标记的精确语义见 `discover-threads.py` 头注与
`codex-integration.md`):

- **exit 2** = 有在途资源未登记到 board(`UNREGISTERED` 的 active codex 会话,或孤儿
  worktree)。这是 curryflows 存在的理由——一个未被发现的 codex /goal 曾跑了约 1.9 亿
  token、3.7 天而无人察觉。exit 2 必须当作 runaway 处理。
- **`RUNAWAY-SUSPECT`** = active 且 rollout 体积 ≥ `--runaway-mb`(默认 50MB)的会话。
- **孤儿 worktree** = `curryflows/*` 分支的 worktree 未被 board 追踪(`[ORPHAN]`)。

对每个 runaway(`UNREGISTERED` / `RUNAWAY-SUSPECT` / 孤儿 worktree)的处置:

1. 用 `interrupt-target.sh <pane>` 对该 codex 会话发单个 Escape 软停(进程存活、goal
   上下文完整,只停在途 turn);
2. 向 `decisions.jsonl` post 一个决策项,描述这是什么资源、为何判为 runaway、证据
   (session_id / rollout 路径 / 体积 / worktree 路径);
3. **本 tick 暂不起新的 codex**——在 runaway 未被人类裁决前,不再扩张 codex 占用,避免
   雪上加霜。

孤儿 worktree 在人类裁决后才动手清理(`git worktree prune` / 删分支),不在发现阶段
擅自删除。

### 2) advance 就绪线程

对 board 上 `state=ready` 的线程,把它推进到 running:

- 建独立分支 `curryflows/<thread-id>` + worktree(默认
  `~/.cache/curryflows/worktrees/<project>/<thread-id>`,base 可配,见 `SKILL.md`
  「并发隔离」);
- 以该线程的模板(feature / perf / test)起一个 Workflow kernel 实例;
- 把 `state` 置为 `running` 写回 board。

并发上限可配(默认保守,大仓调低)。超过上限的就绪线程留在 `ready`,下个 tick 再说。

### 3) 收完成 build 线程的 escalations

对完成构建的线程(Workflow kernel 返回,`state=build-done`),收集它返回的 escalations
(模板在跨模型 review 中裁不动的真分歧,或撞到 barrier 的事项)。这一步只收集,不裁决。

### 4) distill:escalations → 决策项

把模板返回的 escalations 蒸馏成决策项 post 到 `decisions.jsonl`,并把对应线程置为
`blocked-human`。蒸馏的目的是让人类看的是收敛后的决策面,而不是千行原文——这是
curryflows 把人类 review 从关键路径解耦的核心(见 `SKILL.md`)。

### 5) resolve 已决决策项

对 `decisions.jsonl` 里人类已裁决的决策项执行落地。需要人类裁决的硬闸只有四类:

- **合 main**:在 barrier 处串行,先 rebase 到最新 main、重跑验证,冲突 settle 不了
  再回到决策项;
- **对外不可逆**:有外部副作用、无法回滚的动作;
- **seal-contract**:封任务契约(放在开头,plan-tree 交叉评审 + 人封);
- **model-divergence**:跨模型 review 裁不动的真分歧。

裁决为「接受」→ 执行落地(合 main 走串行 barrier);裁决为「拒绝」→ 回滚该线程的分支 +
worktree,把线程置为 `rolled-back`。决策项置 `resolved` / `rejected` 写回 board。

### 6) park 或 continue

- 若仍有就绪线程或待处理项 → 不 park,继续下一个 tick;
- 若无就绪事项 → arm 一个 Monitor(等线程完成 / 人类回复事件),并
  ScheduleWakeup 在 1200–1800s 后再唤醒一次,然后停下释放上下文,省 token。
  被事件或定时唤醒后,从第 1 步重新开始。

## overseer 拆分:谁读 transcript、谁动手、谁出廉价信号

协调器取代了原来独立的 codex-goal-overseer 会话(见 `SKILL.md`)。监督职责按上下文成本
拆成三档,关键是:**巨型 transcript 绝不进协调器主 session 的上下文**。

- **深度审计 = 只读 opus subagent**。当某个自驱 codex 线程需要审计(疑似 drift / 捏造 /
  假实现 / 破坏项目不变量),协调器 spawn 一个只读的 opus subagent 去读该线程的
  transcript 和 diff。transcript 可达几百 MB,这个 subagent 把它隔离在自己的上下文里,
  只回一个裁决结论(继续 / 软停 / 升人类),裁决回到协调器,transcript 本身绝不进协调器
  上下文。
- **决策 + 动手 = 协调器主 session**。拿到 subagent 的裁决后,协调器主 session 才决定
  并执行写动作:对坏裁决跑 `interrupt-target.sh` 软停 + 向 `decisions.jsonl` post 决策
  项。对目标 codex 的写只有两类:Escape(软停)和人类裁决后注入的指令——其余全只读。
- **廉价信号 = 主 session 顺手做**。`discover-threads.py`(只读首行 + 文件 stat,不读
  正文)和 budget 核对成本极低,协调器主 session 在 tick 第 1 步顺手跑,不必 spawn
  subagent。

判读规则:深度审计从目标自己的 transcript 推导该项目的 drift 判据(吸收自
codex-goal-overseer);两方信号一致且契约可判才自动处理,裁不动的真分歧才升人类
(见 `SKILL.md`「跨模型 review」)。

## `/loop` prompt 文本范例

在目标项目根目录,以动态模式起协调器:

```text
/loop 你是 curryflows 协调器,运行在动态(self-paced)模式。board 在
./.curryflows/board/{threads.jsonl,decisions.jsonl},board 是真相源,不是你的上下文:
每个 tick 先从 board 读回状态,tick 末把变更写回 board。

每个 tick 按序执行:
1) discover & reconcile:跑 `python3 scripts/discover-threads.py --project .
   --board ./.curryflows/board/threads.jsonl`。exit 2 或任何 UNREGISTERED active 会话 /
   RUNAWAY-SUSPECT / 孤儿 worktree 都当 runaway:用 interrupt-target.sh 对其 pane 软停 +
   向 decisions.jsonl post 决策项,且本 tick 暂不起新的 codex。
2) advance:对 state=ready 的线程建分支 curryflows/<thread-id> + worktree,以其模板
   (feature/perf/test)起 Workflow kernel,置 running 写回 board(尊重并发上限)。
3) 对 state=build-done 的线程收 escalations。
4) distill:把 escalations 蒸馏成决策项 post 到 decisions.jsonl,线程置 blocked-human。
5) resolve:对人类已裁决的决策项落地。硬闸四类:合 main(串行 barrier:先 rebase 最新
   main + 重跑验证)、对外不可逆、seal-contract、model-divergence。接受则落地,拒绝则回滚
   分支 + worktree 置 rolled-back。
6) park 或 continue:有就绪事项就继续;否则 arm Monitor 等线程完成 / 人类回复,
   ScheduleWakeup 1200-1800s 后再唤醒,然后停下省上下文。

监督拆分:需要深度审计某个自驱 codex 线程时,spawn 一个只读 opus subagent 读其
transcript/diff(把巨型 transcript 隔离在 subagent 上下文里,绝不进你的主上下文),只拿回
裁决;由你(主 session)决定并执行 Esc 软停 / post 决策项;discover + budget 这类廉价信号
你自己顺手跑。对目标 codex 的写只有 Escape 和人类裁决后的指令注入,其余全只读。
```
