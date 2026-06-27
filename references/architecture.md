# curryflows 架构

一句话定位:curryflows 把人类 review 从构建关键路径上解耦——一个 `/loop` 协调器并发推进多个有界 Workflow kernel,每个产物经跨模型 review + 反捏造门在隔离 worktree 上 speculative 推进,只有合 main、对外不可逆、跨模型真分歧三类 barrier 才升人类决策。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`architecture.md` — 三层模型、跨模型 review、barrier 模型、Workflow↔agent 边界。

---

## 1. 三层控制流

三层各有不同的执行语义,层与层之间是单向的 spawn / 嵌套关系,不可互换。

### 外层:协调器(`/loop` 动态模式)= 调度

- 是一个 **agent 推理循环**,不是 Workflow 脚本。
- 维护在途线程图(thread graph),推进就绪线程;无就绪线程时 park 释放上下文,被事件唤醒:线程完成、人类回复决策项、定时 tick。
- 每个 tick 自己跑廉价信号:`scripts/discover-threads.py` 统一资源发现 + budget 检查。
- 对需要深度审计的自驱线程,spawn 一个**只读 opus subagent** 读 transcript / diff,把巨型 transcript 隔离在 subagent 内,绝不进协调器上下文,只拿回裁决。

### 内层:Workflow kernel = 有界任务

- 三个内置模板:`workflows/feature-impl.js`、`workflows/perf-opt.js`、`workflows/test-gen.js`。
- 确定性扇出、结构化输出(每个 lane 带 JSON schema)、可 resume、带 budget(`maxRounds`,默认 3)。
- **关键约束:Workflow 脚本只编排,不动手。** 脚本本体只有控制流 + `phase()` / `log()` / `agent()` / `parallel()`;它没有 fs / bash 能力。所有动手——编辑代码、跑 bash、驱动 codex、读 git diff——都发生在它 `agent(...)` spawn 出来的 agent 内部,只有那些 agent 才有工具。

### 自驱层:codex `/goal` = 长程不确定线程

- 用于长程、不确定的调查,由强目标契约(budget + blocked-stop)兜住。
- 挂只读审计 + Esc 急停(`scripts/interrupt-target.sh`)。
- codex 全走 tmux,唯一驱动器是 `scripts/inject-steer.sh`(注入)与 `scripts/interrupt-target.sh`(软停),绝不手搓 `send-keys`。

### 为什么外层不能是 Workflow

Workflow kernel 是确定性、有界、可 resume 的有限状态推进:它要求扇出形状、终止条件、budget 在进入时就确定。外层调度恰恰相反——在途线程数量与依赖随事件动态变化、需要在 review 待定时择机推进别的就绪线程、需要 park 后被任意事件唤醒。这是开放式的 agent 推理,无法用确定性 Workflow 表达。因此外层必须是 `/loop` 动态模式的 agent,内层才是 Workflow。

---

## 2. Workflow ↔ agent 边界(动手都在 agent 内)

这是 curryflows 最硬的结构约束,直接决定了能力隔离:

| 层 | 能力 | 不能做 |
|---|---|---|
| Workflow 脚本(`*.js`) | 控制流、`phase`、`log`、`agent`、`parallel`、读 `args` | 无 fs、无 bash、不直接编辑文件、不直接跑命令 |
| 它 spawn 的 agent | 完整工具集(读写文件、bash、git、tmux、调用脚本) | 受 agent 类型与 prompt 边界约束 |

在三个模板里这条边界处处可见:每一段 `await agent('...', {...})` 的 prompt 都明确告诉 agent「只在 worktree `${C.worktree}` 内动手」「`mkdir -p ${EV}`」「跑 `${K.validation_command}`」「读 `git -C ${C.worktree} diff`」——因为脚本自己做不到这些,必须委派给有工具的 agent。

agent 类型按职责分:
- 动手 lane(produce / repair / apply / generate / negctrl)用 `agentType: 'general-purpose'`,有写权限。
- 只读 lane(validate / inspect / cross-review / arbiter / pre-archive-guard)用 `agentType: 'Explore'`,只读。
- 会改同一份代码的 lane(perf 的 strategy lanes、test 的 negctrl)额外加 `isolation: 'worktree'`,各自跑在独立 worktree 里,互不 clobber。

---

## 3. 跨模型 review(codex 腿 + Claude 腿 + arbiter)

每个高风险产物由 **codex 与 Claude 各自独立** review,分歧即信号。三个模板里这一段结构相同:

1. **codex 腿**(`xreview:codex:r<round>`,Explore agent):先把 review prompt 写到 `${EV}/xreview-codex-prompt-r<round>.md`,再跑 `bash ${C.skillDir}/scripts/codex-review.sh --cwd ${C.worktree} --prompt-file ... --out ${EV}/xreview-codex-r<round>.md --effort ${EFFORT} --timeout 900`。`codex-review.sh` 在 tmux 起 codex、注入一个文件交付契约(只读 review,把 findings 写到 `--out`,然后停),轮询该文件出现并稳定后拆 pane。codex 腿读回 findings。**脚本非零退出时返回 `{reviewer:'codex', failed:true, findings:[]}`,严禁捏造 findings。**
2. **Claude 腿**(`xreview:claude:r<round>`,Explore agent):独立读同一份 `git diff` 对照同一份契约,产出 findings。
3. **arbiter**(`arbiter:r<round>`,Explore agent):拿到两份独立 review + validation 结果,**对照 ground truth(契约 + validation 结果,perf 还有 benchmark,test 还有 negative control)裁,不投票**:
   - 两方都报且契约可判 → 真 fix。
   - 仅一方报 → 对照 ground truth 判,真则 fix,不真则 drop。
   - 对照 ground truth 裁不动的真分歧 / 契约缺口 → escalate 给人类。

这套机制天然把人类决策队列过滤到极少数:绝大多数产物在 arbiter 处就被自动判定。

---

## 4. barrier 模型

curryflows 把「人类必须确认」收敛成极少数 barrier,其余靠「疑问→就地跨模型 review→分歧 settle 不了才升」自动消化。

硬闸(必停)只剩两类:
- **合 main**。
- **对外不可逆**(outward-irreversible)。

其余一律不停:产生疑问 → 就地跑跨模型 review → 一致且契约可判就自动处理 → 只有真分歧 settle 不了才升成决策项(`model-divergence` barrier)。

**seal-contract 放在最开头**:契约由 plan-tree 交叉评审 + 人封定,之后才进 Workflow。每个模板的第一个 phase 就是 `precheck`,对契约做 fail-closed 校验(缺任一必填字段直接 `throw`),保证进入 produce 时契约已封定。

barrier 与决策项格式见 `decision-surface.md`。

---

## 5. speculation + commit

- 每个 thread = **独立分支 + 独立 worktree**(默认 base `~/.cache/curryflows/worktrees/<project>/<thread-id>`,base 可配)。
- 线程在自己的分支/worktree 上 speculative 推进,全程不碰 main。
- 合 main 在 barrier 处**串行**:先 rebase 到最新 main、重跑验证;冲突 settle 不了升决策项(`merge-main` barrier)。
- 每个模板结束返回 `status`:`ready-to-merge`(archive gate 全过)/ `blocked-human`(有 escalations)/ `failed`。`ready-to-merge` 才进合 main barrier。
- 孤儿 worktree 并入资源发现对账:`discover-threads.py` 列出本项目所有 `curryflows/*` 分支的 worktree,与 board 对账标 orphan,配合 `git worktree prune`。

---

## 6. per-project 状态

curryflows skill 本身是通用件,不写死任何项目路径或契约。每个项目的运行态都落在**目标项目内**的 `<project>/.curryflows/`,不进 skill 仓:

- `<project>/.curryflows/board/threads.jsonl` — 线程台账(`discover-threads.py --board` 对账的对象;每条记录可含 `codex_session`、`branch`)。
- `<project>/.curryflows/board/decisions.jsonl` — 人类决策队列(协调器 post escalations,见 `decision-surface.md`)。
- worktree 内的 `${C.worktree}/.curryflows/` — 单个 thread 的证据落盘:`validate-r<n>.log`、`baseline.log`、`gap-report.md`、`xreview-codex-prompt-r<n>.md`、`xreview-codex-r<n>.md` 等。

---

## 7. 数据流

```
人类
 │  seal-contract(plan-tree 交叉评审 + 人封)
 ▼
任务契约(task-contracts/<template>.md)
 │
 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 协调器 /loop(外层,agent 推理,不是 Workflow)                      │
│  · 维护在途线程图;就绪即推进,无就绪则 park,事件唤醒              │
│  · 每 tick:discover-threads.py + budget 检查                       │
│  · 深度审计 → spawn 只读 opus subagent(隔离巨型 transcript)       │
└───────────────┬──────────────────────────────┬────────────────────┘
                │ 起 thread                      │ 挂监督
                ▼                                ▼
   ┌──────────────────────────┐      ┌────────────────────────────┐
   │ Workflow kernel(内层)   │      │ codex /goal(自驱线程)     │
   │ feature / perf / test    │      │ 强契约(budget+blocked-stop)│
   │ 脚本只编排,无 fs/bash    │      │ 只读审计 + Esc 急停         │
   │  │ agent(...) / parallel  │      │ tmux: inject/interrupt      │
   │  ▼                        │      └────────────────────────────┘
   │ ┌──────────────────────┐ │
   │ │ spawn 的 agent(有工具)│ │   分支 + worktree(隔离,speculative)
   │ │  precheck → produce  │ │   证据落 ${worktree}/.curryflows/
   │ │  → validate(真跑+证据)│ │
   │ │  → cross-review:     │ │       ┌─────────────┐  ┌──────────────┐
   │ │     codex 腿 ───────────────────│ codex-review │→ │ findings 文件 │
   │ │     Claude 腿        │ │       │ .sh (tmux)  │  └──────────────┘
   │ │     → arbiter(对照   │ │       └─────────────┘
   │ │       ground truth,  │ │
   │ │       不投票)         │ │
   │ │  → verdict 洗白器     │ │
   │ │  → bounded loop      │ │
   │ │  → 硬停 pre-exec gate │ │
   │ │  → pre-archive guard │ │
   │ │     + minimal-diff   │ │
   │ │  → archive gate      │ │
   │ │    (fail-closed)     │ │
   │ └──────────┬───────────┘ │
   └────────────┼─────────────┘
                │ status + escalations[]
                ▼
   ┌──────────────────────────────────────────────┐
   │ 协调器整合                                     │
   │  · ready-to-merge → 合 main barrier:          │
   │    rebase 最新 main + 重跑验证(串行)         │
   │  · escalations → post 决策项                   │
   └───────┬──────────────────────────┬────────────┘
           │ 自动消化(一致+契约可判)  │ 真分歧/不可逆/合 main 冲突
           ▼                          ▼
        合入 main          <project>/.curryflows/board/decisions.jsonl
                                      │
                                      ▼
                                    人类(只看蒸馏后的决策面)
```

数据流要点:契约从人类经 seal 进入,沿协调器→Workflow→有工具的 agent 单向下行;证据(log / findings / diff)横向落在 worktree 的 `.curryflows/`;只有 `ready-to-merge` 的产物与 escalations 上行回协调器;人类只在队尾看蒸馏后的决策面。
