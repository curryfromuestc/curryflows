---
name: curryflows
description: >-
  通用工作流协调器 skill,把人类 review 从构建关键路径上解耦。与 ultracode 官方 Workflow 分工、
  按任务动态切:确定性/有界任务(实现一批、评审 diff、研究、内层扇出+对抗验证)走 ultracode 官方
  Workflow、不用本 skill;curryflows 专管 tmux 里长跑、跨会话、防 runaway 的自驱 codex /goal 群 +
  durable 异步人类决策面。一个 /loop 协调器以"审核优先"
  推进多个在 tmux 里长跑的 codex /goal worker:每个 tick 调官方 Workflow(workflows/review-panel.js)
  跑 review 面板审产物 + 对账资源,协调器据裁决决策,再派一个 operator subagent 去操作 tmux
  (起/驭/回收 codex)。worker 是 codex、reviewer 是 Claude,天然跨模型;裁决只回一条清晰摘要
  给主 session,完整证据落 durable 看板,人类异步看、异步决策,默认不阻断推进——只在合 main、
  对外不可逆、跨模型真分歧三种 barrier 才升人类。自驱 codex /goal 挂强目标契约(budget +
  blocked-stop)+ 只读审计 + Esc 急停,防跑飞。统一资源发现把所有在途 codex 会话 + worktree
  对账,用完即回收,杜绝 runaway。触发于:"起 curryflows 协调器"、"用 curryflows 跑并发开发"、
  "做带跨模型评审的并发开发"、"监督 codex /goal 别跑飞"、"排查 runaway codex 会话"、
  "把人类 review 从关键路径上解耦"。
user-invocable: true
argument-hint: '[start | status | board | oversee <codex-session-id> <pane>]'
type: skill
tags: [工作流, 编排, 跨模型评审, 协调器, codex, tmux, worktree, 反捏造]
requires:
  - codex CLI (>=0.128, 支持 /goal)
  - tmux
  - git (>=2.5, 支持 worktree)
  - python3
---

# curryflows

把人类 review 从构建关键路径上解耦的通用工作流协调器。一个 `/loop` 协调器并发推进多个长跑
worker;解耦期的正确性由跨模型 review(worker=codex,reviewer=Claude)+ 反捏造审核守住。
**每个 tick 都吐出一条清晰摘要回主 session,人类有空才看、才决策,而决策默认不阻断推进——只有
合 main、对外不可逆、跨模型真分歧三种 barrier 才停。**

> 本 skill 是通用件,不写死任何具体项目的路径或契约。每个项目的运行态(看板 / 决策队列 /
> worktree / 日志)落在目标项目里,不进 skill 仓。

## 何时用 / 何时不用(与 ultracode 官方 Workflow 的分工)

建议开局同时挂上 **ultracode + curryflows**,按任务**动态切**:

- **确定性 / 有界任务** —— 实现一批改动、评审一个 diff、做一次研究、内层并行扇出 + 对抗验证、
  judge panel、对单个产物拉 codex 第二意见 —— **走 ultracode 的官方 Workflow 工具,不要用
  curryflows**。这类有界多-agent 工作 Workflow 工具原生更省事(本仓自身的批量改动就是纯 Workflow
  工具做的)。
- **非确定 / 长跑 / 跨会话 / 要把人类 review 从关键路径解耦** —— **走 curryflows**。

**curryflows 不重造循环引擎**:外层 `/loop` 反应式循环(park/唤醒、跨天调度、非阻断推进)本身是
Claude Code 内置原语,任何会话都能直接用。curryflows 的**不可替代价值**收窄为 `/loop` 之上、官方
Workflow 工具做不到的这一层:

1. **tmux 里跨会话存活的自驱 codex /goal 群**:SSH 断连不丢、可重连;Esc 急停可驭 live worker;
   budget + blocked-stop 强契约挂在真·长跑 worker 上。
2. **防 runaway**:`discover-threads.py` 跨会话对账,杜绝"一个 codex /goal 跑 1.9 亿 token、3.7 天
   无人察觉"。
3. **durable 跨会话真相源 + 异步人类决策面**:`board.py` 看板 / `decisions.jsonl` 队列 / HTML 看板,
   人类跨天异步裁决、默认不阻断推进。
4. **worker 生命周期状态机 + 分阶段 reap + 已封契约 fail-closed 门**。

判据:**凡能在一次有界 episode 内跑完的 → ultracode;只有"要在 tmux 里长跑、跨会话、防跑飞、人类
异步裁决"的 → curryflows。** curryflows 自己 tick 内的有界扇出(reviewer 面板)即由
`workflows/review-panel.js` 这个官方 Workflow 承载执行,协调器每 tick 调 Workflow 工具跑它。

## 唯一的硬约束:不爆主 session 上下文

curryflows 不为省钱做取舍。唯一的设计约束是:**协调器(主 session)的上下文绝不能被巨型
transcript / diff / 裁决全文撑爆**。一切重活——读几百 MB 的 codex transcript、读 git diff、
跑脚本、操作 tmux——都发生在被 spawn 的 subagent 里,它们的大上下文随 subagent 消亡;协调器
只收回蒸馏后的结论。协调器会 park、会被压缩、会跨多个唤醒周期存活,所以它的真相源是 durable
看板文件,不是上下文(见 `references/board.md`)。

## 三层控制流

1. **协调器(`/loop` 动态模式)= 外层调度**:极薄,只做推理、决策、派发、写看板,自己不读大
   文件、不跑脚本。维护在途线程图,无就绪事项时 park 释放上下文,被事件(线程完成、人类回复、
   定时)唤醒。这一层是 agent 推理,**不是**确定性编排脚本。
2. **内层有界动作**:每个 tick 先调官方 Workflow 工具跑 **`workflows/review-panel.js`**(review 面板,
   只读)审产物 + 对账资源;协调器据裁决决策后,再派一个 **operator subagent**(opus,可改)去操作
   tmux/codex(起/驭/回收)。review 面板由官方 Workflow 承载,operator 仍是强力(opus)subagent。
3. **codex `/goal` = 自驱 worker**:真正干活的长跑线程,在 detached tmux 里跑,由强目标契约
   (budget + blocked-stop)+ 只读审计 + Esc 急停兜住(见 `references/goal-contract.md`)。

为什么外层不能是确定性编排:在途线程数量与依赖随事件动态变化、需要在 review 待定时择机推进别的
就绪线程、需要 park 后被任意事件唤醒——这是开放式 agent 推理,只能用 `/loop` 动态模式表达。

## tick:审核优先 → 决策 → 操作

每个 tick 严格按此顺序(完整 runbook 见 `references/coordinator.md`):

1. **审核(协调器调官方 Workflow 工具跑 `workflows/review-panel.js`,只读)**:Workflow 内部逐线程
   pipeline——stage1 并发多 lens(correctness/bounds/invariant/repro,各自隔离上下文)+ 每 lens 跑
   `scripts/discover-threads.py` 资源对账 + 跨模型硬规则(worker 非 codex 时追加 `codex-review.sh`
   腿),stage2 arbiter 对照契约收敛(**不投票**、裁不动则 escalate);返回 `{reviews, escalations}`。
   巨型 transcript 隔离在各 lens agent 内,绝不进协调器。
2. **决策(协调器,薄)**:收齐裁决 → 收敛;多裁决一致且依据可判就自动处理,真分歧裁不动 →
   升人类决策项(不投票)。同时落地人类已回复的决策项。
3. **操作(后派 1 个 operator subagent)**:按决策操作 tmux/codex——detach 起新 /goal、
   `inject-steer.sh` 注入指令、`interrupt-target.sh` 软停,以及**回收用完的资源**(`tmux
   kill-session` + `git worktree remove/prune` + 删 curryflows 分支,见 `scripts/reap.sh`)。
4. **写看板 + 回摘要**:更新 durable 看板,向主 session 回一条清晰摘要(schema 见下)。
5. **park 或 continue**:有就绪事项就继续;否则 arm Monitor + ScheduleWakeup(1200–1800s)后停下省上下文。

## 每 tick 的摘要:清晰、不糊弄

回主 session 的摘要必须紧凑,但**禁止绿洗**。缺以下任一项不算合格摘要:

- 每条在跑线程:状态 / 本 tick 实质进展 / 预算余额;
- 审核裁决:**含异议**(哪个 reviewer 报了什么、是否有跨模型分歧),不许只报"通过";
- **未验证项 / 风险 / 越界**:强制如实暴露;
- 待人类决策项(若有)+ 本 tick 回收了哪些资源。

完整裁决 / transcript 落 durable 看板,摘要只给指针(路径)。详见 `references/board.md`。

## 跨模型 review(本 skill 的招牌)

worker 是 codex、reviewer 是 Claude opus,produce 与 review 天然跨模型;每 tick 由
`workflows/review-panel.js` 这个官方 Workflow 扇出**多个**reviewer(不同 lens,各自独立),分歧即
信号:一致且依据可判 → 自动处理;真分歧 → 对照 ground truth(契约 / 权威文档 / GOLD oracle /
复现)裁,**不投票**;裁不动 → 升人类。需要 codex 第二意见时,该 Workflow 调 `scripts/codex-review.sh`
拉一份 codex 侧审核(默认 worker=codex 时为可选,非每 tick 必跑)。
**硬规则:跨模型 review 仅当 `worker.model != reviewer.model` 才成立。** 默认 worker=codex /goal、
reviewer=Claude opus → 天生跨模型;但若某线程的 worker 是 Claude subagent(非 codex),则至少一个
reviewer 必须是 codex 腿(`scripts/codex-review.sh`)——此时 codex-review.sh 是**必需**、不是可选,
否则审核退化为单模型、跨模型保证作废。协调器必须保证 reviewer 模型集合里存在与 worker 不同的模型。
reviewer 的反捏造 / 独立复验职责见 `references/reviewer-spec.md`。

## 综合看板(HTML,启动即 serve)

每个项目的运行态落在 `<project>/.curryflows/`:`board/threads.jsonl`(线程台账)、
`board/decisions.jsonl`(人类决策队列)、`board/ticks.jsonl`(每 tick 完整裁决,durable 历史),
以及 `contracts/<thread-id>.md`(已封的每线程契约,`task-contracts/task.md` 填好的副本;
threads.jsonl 的 `contract` 字段指向它)。**看板 jsonl 的唯一写入者是 `scripts/board.py`**(原子
重写 + 枚举/必填 fail-closed 校验);绝不手编 `threads.jsonl` / `decisions.jsonl`——手编易写坏行,
而 `render-board.py` 对坏行静默跳过,会无声丢状态。`scripts/render-board.py` 只读这些 jsonl,
确定性渲染出一张自包含 **HTML 看板**(`dashboard.html`,浅色学术配色,浏览器直接开)。

`start` 协调器时**顺带后台拉起 `scripts/serve-board.py`**:它在本地端口(默认
`127.0.0.1:8787`)serve 看板,**每次请求实时重渲染** + 页面自带自动刷新,人类浏览器里就能看实时
状态(SSH 机器上端口转发即可)。格式见 `references/board.md`。

worker 生命周期(状态机 `ready → running → idle → reviewed → committed → verified →
session-reaped → merged | rolled-back`,任意状态可入 `blocked-human`)、分阶段 reap 与
relaunch/扩展见 `references/coordinator.md` `## worker 生命周期`。

## 资源管理:用完即回收

operator 每 tick 负责把跑完 / 孤儿的 tmux 会话、codex 线程、worktree **直接回收**——这是硬职责,
不指望收尾钩子。`discover-threads.py` 双向对账(真实 tmux 会话 / worktree vs 看板)给出可回收集,
operator 执行 `scripts/reap.sh`。详见 `references/operator-spec.md`。

## 并发隔离

每个长跑 worker = 独立分支 + worktree(默认 `~/.cache/curryflows/worktrees/<project>/<thread-id>`,
base 可配)。worker 在自己的分支/worktree 上 speculative 推进,全程不碰 main。合 main 在 barrier
处串行:先 rebase 到最新 main、重跑验证,冲突 settle 不了升决策项。孤儿 worktree 并入资源发现对账
+ 回收。

## 人类决策(barrier,异步、非阻断)

默认不阻塞:疑问 → 就地跨模型 review → 一致且依据可判就自动处理。只有三类硬闸入队:**合 main**、
**对外不可逆**、**跨模型真分歧**(另有 **seal-contract** 在开头封定 worker 的目标契约)。人类在
`dashboard.html` / `decisions.jsonl` 上异步处理,**前进不等人**。详见 `references/decision-surface.md`。

## 自驱 codex 的监督

协调器取代了独立的 overseer 会话:廉价信号(`discover-threads.py` + budget)在审核阶段由 reviewer
顺手出;深度审计由只读 opus reviewer 读 transcript/diff(隔离巨型 transcript,绝不进协调器);坏裁决
→ 协调器决策 → operator 跑 `interrupt-target.sh` 软停 + post 决策项。codex 全走 tmux,唯一驱动器是
`inject-steer.sh` / `interrupt-target.sh`:**对 live codex TUI 绝不手搓 raw send-keys**(在普通 shell
pane 上用 send-keys 启动 codex 二进制是允许的,见 `references/codex-integration.md`)。对目标 codex 的
写只有两类:Escape(软停)和人类裁决后注入的指令,其余全只读。

## 操作

- `start` — 在当前项目起协调器 `/loop`(见 `references/coordinator.md`),并后台拉起
  `scripts/serve-board.py` serve HTML 看板(默认 `127.0.0.1:8787`)。
- `status` — 跑 `scripts/discover-threads.py --project . --board ./.curryflows/board/threads.jsonl`,
  列所有在途资源 + 未对账的 runaway。
- `board` — 看板 jsonl 的所有写入走 `scripts/board.py`(唯一写入者,原子 + fail-closed,见上);
  `scripts/render-board.py --board ./.curryflows/board` 只读这些 jsonl 渲染出 `dashboard.html`
  (或直接开已 serve 的端口看实时版)。
- `oversee <codex-session-id> <pane>` — 把一个已在跑的 codex /goal(在 curryflows 之外启动的)
  注册到看板,纳入协调器每-tick 的 reviewer 审核 + Esc 急停;不是独立 overseer 会话。

## 文档索引(references)

- `architecture.md` — 三层模型、审核优先 tick、跨模型 review、barrier、subagent 边界。
- `coordinator.md` — coordinator tick runbook + `/loop` prompt。
- `reviewer-spec.md` — reviewer / arbiter 契约(由官方 Workflow `workflows/review-panel.js` 执行):读什么、裁决 schema、反捏造 + 独立复验、清晰摘要要求。
- `operator-spec.md` — operator subagent 契约:起/驭/回收 tmux/codex、资源生命周期、detach、回传。
- `board.md` — 综合看板格式(threads/decisions/ticks/dashboard)+ 每 tick 摘要 schema。
- `codex-integration.md` — codex 全走 tmux + inject/interrupt + 文件交付 + discover-threads。
- `goal-contract.md` — /goal 强契约(budget + blocked-stop)。
- `decision-surface.md` — 决策项格式 + barrier / 疑问驱动。
- `goal-cookbook.md` — codex /goal 参考。

## Workflow 脚本(非 references)

- `workflows/review-panel.js` — 随仓附带的官方 Workflow 参考脚本:协调器每 tick 调官方 Workflow 工具
  跑它(逐线程 pipeline:stage1 并发多 lens + 资源对账 + 跨模型硬规则,stage2 arbiter 收敛/escalate),
  返回 `{reviews, escalations}`。前提:协调器会话须已开 ultracode / 已 opt-in 官方 Workflow。

## 依赖与边界

硬依赖见 frontmatter `requires`。本 skill 不依赖任何具体项目运行时。外部资料、外部 skills 或
示例实现只作参考,不覆盖本地约定。
