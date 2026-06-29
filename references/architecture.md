# curryflows 架构

一句话定位:curryflows 把人类 review 从构建关键路径上解耦——一个 `/loop` 协调器以"审核优先"
推进多个在 tmux 里长跑的 codex /goal worker,每个产物经跨模型 review(worker=codex、
reviewer=Claude)+ 反捏造审核守住,人类异步看蒸馏后的决策面,只有合 main、对外不可逆、跨模型
真分歧三类 barrier 才升人类,**默认不阻断推进**。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`architecture.md` — 三层模型、审核优先
> tick、跨模型 review、barrier、subagent 边界。

---

## 0. 唯一硬约束:不爆主 session 上下文

curryflows 不为省钱做取舍。唯一约束是协调器(主 session)上下文不被撑爆:一切重活(读巨型
transcript / diff、跑脚本、操作 tmux)都在被 spawn 的 subagent 里完成,subagent 的大上下文随它
消亡,协调器只收蒸馏结论。协调器的真相源是 durable 看板文件,不是上下文(见 `board.md`)。

---

## 1. 三层控制流

三层各有不同执行语义,层与层之间是单向 spawn 关系,不可互换。

### 外层:协调器(`/loop` 动态模式)= 调度

- 是一个 **agent 推理循环**,不是确定性脚本。极薄:只做推理、决策、派发、写看板。
- 维护在途线程图;无就绪事项时 park 释放上下文,被事件唤醒:线程完成、人类回复决策项、定时 tick。
- 自己不读大文件、不跑脚本——这些全外派给 subagent。

### 内层:subagent 派发 = 有界动作

每个 tick 派两类 subagent,顺序固定为"先审核、后操作":

- **reviewer subagent(并发多个,opus,只读)**:顺手读资源真值(`discover-threads.py` + 看板)、
  读在途 codex worker 的 transcript/diff,审产物,各回一条清晰裁决(含异议)。多个 reviewer 各取
  不同 lens、各自隔离上下文。契约见 `reviewer-spec.md`。
- **operator subagent(1 个,opus,可改)**:协调器决策后派出,操作 tmux/codex——detach 起新
  /goal、`inject-steer.sh` 注入、`interrupt-target.sh` 软停,以及回收用完的资源。契约见
  `operator-spec.md`。

所有 subagent 一律强力(opus)。**没有单独的 checker**:巡检并入 reviewer(审核本就要读状态)。

### 自驱层:codex `/goal` = 长跑 worker

- 真正干活的长跑、不确定线程,在 detached tmux 里跑,由强目标契约(budget + blocked-stop)兜住。
- 挂只读审计(reviewer 读其 transcript)+ Esc 急停(`interrupt-target.sh`)。
- codex 全走 tmux,唯一驱动器是 `inject-steer.sh`(注入)与 `interrupt-target.sh`(软停),绝不
  手搓 `send-keys`。

### 为什么外层不能是确定性编排

确定性编排要求扇出形状、终止条件、budget 在进入时就定死;外层调度恰恰相反——在途线程数量与依赖随
事件动态变化、需在 review 待定时择机推进别的就绪线程、需 park 后被任意事件唤醒。这是开放式 agent
推理,只能用 `/loop` 动态模式表达。

---

## 2. subagent 边界(动手都在 subagent 内)

这是 curryflows 最硬的结构约束,直接决定能力隔离与上下文隔离:

| 角色 | 能力 | 不能做 |
|---|---|---|
| 协调器(主 session) | 推理、决策、派发 subagent、写看板 jsonl | 不读巨型 transcript/diff、不跑长脚本、不直接操作 tmux |
| reviewer subagent(opus,只读) | 读 transcript/diff、跑 discover-threads、审产物 | 不改代码、不操作 tmux(只读) |
| operator subagent(opus,可改) | tmux/codex 操作、起/驭/回收、git worktree | 受 prompt 边界约束;只执行协调器已定的决策 |

关键:**巨型 transcript 绝不进协调器主 session 上下文**——它被隔离在 reviewer subagent 内,
reviewer 只回裁决。

---

## 3. 跨模型 review(worker=codex + reviewer=Claude + 不投票收敛)

worker 是 codex、reviewer 是 Claude opus,produce 与 review 天然跨模型。每 tick 派**多个** reviewer
(不同 lens,各自独立),分歧即信号:

1. **多 reviewer 一致且依据可判** → 协调器自动处理。
2. **真分歧** → 对照 ground truth(契约 / 权威文档 / GOLD oracle / 复现)裁,**不投票**;裁不动 →
   升人类决策项。
3. **需要 codex 第二意见**时,reviewer 可调 `scripts/codex-review.sh` 拉一份 codex 侧独立审核(可选,
   非每 tick 必跑);codex-review.sh 文件交付,脚本非零退出即返回失败、严禁捏造 findings。

这套机制把人类决策队列过滤到极少数。reviewer 的反捏造 + 独立复验职责见 `reviewer-spec.md`。

---

## 4. barrier 模型(异步、非阻断)

curryflows 把"人类必须确认"收敛成极少数 barrier,其余靠"疑问→就地跨模型 review→分歧 settle
不了才升"自动消化。人类在 `dashboard.html` / `decisions.jsonl` 上异步处理,**前进不等人**。

硬闸:**合 main**、**对外不可逆**、**跨模型真分歧**;另有 **seal-contract** 在开头封定 worker 的
目标契约(plan-tree 交叉评审 + 人封)。barrier 与决策项格式见 `decision-surface.md`。

---

## 5. speculation + commit + 资源回收

- 每个长跑 worker = **独立分支 + 独立 worktree**(默认 base `~/.cache/curryflows/worktrees/<project>/<thread-id>`,可配)。
- worker 在自己的分支/worktree 上 speculative 推进,全程不碰 main。
- 合 main 在 barrier 处**串行**:先 rebase 到最新 main、重跑验证;冲突 settle 不了升决策项。
- **用完即回收**:operator 每 tick 把跑完 / 孤儿的 tmux 会话、codex 线程、worktree 直接回收
  (`reap.sh`:`tmux kill-session` + `git worktree remove/prune` + 删 curryflows 分支),这是硬职责,不指望收尾钩子。
  `discover-threads.py` 双向对账给出可回收集。

---

## 6. per-project 状态(综合看板)

curryflows skill 本身通用,不写死任何项目路径。每个项目的运行态落在 `<project>/.curryflows/`,
不进 skill 仓(格式见 `board.md`):

- `board/threads.jsonl` — 线程台账(`discover-threads.py --board` 对账对象;含 `codex_session`、`branch`)。
- `board/decisions.jsonl` — 人类决策队列。
- `board/ticks.jsonl` — 每 tick 完整裁决(durable 历史,摘要的后备)。
- `board/dashboard.html` — `render-board.py` 渲染的综合看板(人类异步视图)。
- worktree 内 `${worktree}/.curryflows/` — 单个 worker 的证据落盘(validate 日志、findings、diff 等)。

---

## 7. 数据流

```
人类
 │  seal-contract(plan-tree 交叉评审 + 人封 worker 的目标契约)
 ▼
任务契约(task-contracts/task.md)
 │
 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 协调器 /loop(外层,agent 推理,薄,不读大文件 / 不跑脚本)         │
│  tick: 审核 → 决策 → 操作 → 写看板 + 回摘要                         │
└───┬──────────────────────────┬──────────────────────────┬──────────┘
    │ ① 先派(并发)             │ ③ 后派                    │ 写
    ▼                          ▼                          ▼
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ reviewer subagent ×N │  │ operator subagent ×1 │  │ board/*.jsonl        │
│ (opus,只读)          │  │ (opus,可改)          │  │ + dashboard.html       │
│ · discover-threads   │  │ · detach 起 /goal    │  │ (durable 真相源)     │
│ · 读 transcript/diff │  │ · inject/interrupt   │  └──────────┬───────────┘
│ · 审产物,回裁决     │  │ · reap 回收资源      │             │
│   (含异议,隔离巨型  │  └──────────┬───────────┘             │
│    transcript)       │             │ 操作                    │
└──────────┬───────────┘             ▼                          │
           │ 裁决              ┌──────────────────────────┐      │
           │ (蒸馏)            │ codex /goal worker(tmux) │      │
           ▼                   │ 强契约 budget+blocked-stop│      │
┌──────────────────────┐      │ 分支 + worktree(隔离)    │      │
│ 协调器决策(② 薄)    │      └──────────────────────────┘      │
│ · 一致+可判 → 自动    │                                         │
│ · 真分歧 → 升人类     │                                         │
│ · 落地已回复决策项    │─────────────────────────────────────────┘
└───────┬──────────────┘
        │ 回主 session:一条清晰摘要(不糊弄,只给指针)
        ▼
   人类(异步看 dashboard.html / decisions.jsonl,前进不等人)
```

数据流要点:契约从人类经 seal 进入;每 tick 先审核(巨型 transcript 隔离在 reviewer 内)→ 协调器
决策 → operator 操作 → 写 durable 看板 + 回一条清晰摘要;人类只在队尾异步看蒸馏后的决策面,
决策不阻断推进。
