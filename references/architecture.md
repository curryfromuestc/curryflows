# curryflows 架构

一句话定位:curryflows 把人类 review 从构建关键路径上解耦——一个 `/loop` 协调器以"审核优先"
推进多个在 tmux 里长跑的 codex /goal worker,每个产物经跨模型 review(worker=codex、
reviewer=Claude)+ 反捏造审核守住,人类异步看蒸馏后的决策面,只有对外不可逆、跨模型真分歧才升人类
(合 main 验证过即自动合,见 CANON [L]),**默认不阻断推进**——"不阻断"指不弹窗、其余无依赖线程照跑;
触发决策的那条线程仍真停等人(Esc 软停、沉默不是同意,CANON [N])。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`architecture.md` — 三层模型、审核优先
> tick、跨模型 review、barrier、subagent 边界。

---

## 0. 上下文纪律:持久上下文 + 受控压缩 + tick 内不灌大读(CANON [Q])

协调器上下文是**可消耗资源,不是记忆**(CANON [Q],权威定义见 `coordinator.md`):session 级
cron 按节拍注入 tick prompt,上下文跨 tick 持续存在,由 auto-compact 有界(启动时
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000` 钳制);上下文历史仅供参考,每 tick 重新读回看板对账,
跨 tick 状态只信 durable 看板(见 `board.md`)。tick 内的巨读仍必须隔离:巨型 transcript /
大 diff 只进 review-panel Workflow / subagent 的上下文,随其消亡,协调器只收蒸馏结论;协调器
内联跑构建/测试一律输出重定向文件、只读 exit code。

---

## 1. 三层控制流

三层各有不同执行语义,层与层之间是单向 spawn 关系,不可互换。

### 外层:协调器(cron 心跳 + 持久上下文)= 调度

- tick **内部**是 agent 推理(审、裁、调度不可脚本化);tick 的**触发**是确定性机械件:
  `/loop <间隔>` 的 session 级 cron 注入 tick prompt(CANON [Q])。
- 每 tick 从看板读回全部状态(有界读),tick 末写回;上下文历史仅供参考(可能已被压缩),
  一切以看板为准;无事可做即 no-op 收 tick。
- 自己不读大文件;机械操作(tmux/git)内联执行,改码一律外派(CANON [J])。

### 内层:有界动作(审核外派,机械操作内联)

每个 tick 顺序固定为"先审核、后操作":

- **审核 = 官方 Workflow `workflows/review-panel.js`**:协调器(已开 ultracode / 已 opt-in 官方
  Workflow)调 `Workflow` 工具跑随仓附带的参考脚本 `workflows/review-panel.js`,而不再手搓并发派 N 个
  reviewer subagent。脚本内部 pipeline 逐线程:stage1 并发多 lens(correctness/bounds/invariant/repro,
  Explore agent)+ 每 lens 跑 `discover-threads.py` 做资源真值对账 + 跨模型硬规则(worker 非 codex 时
  追加一条 `codex-review.sh` 腿);stage2 每线程 arbiter 收敛(不投票、对照契约 ground truth、裁不动
  则 escalate)。返回每线程一条清晰裁决(含异议、verdict、resources、escalations)。契约见
  `reviewer-spec.md`。
- **机械操作 = 协调器内联执行**:detach 起新 /goal、`inject-steer.sh` 注入、`interrupt-target.sh`
  软停、commit、串行合 main、终态回收——输出都小(sha / exit code / session-id),300K 窗口 +
  no-slurp 使内联安全且省一次 subagent 往返。规程见 `operator-spec.md`。
- **fixer subagent(按需,唯一被授权改码的外派体)**:合并冲突 / 验证回归先驭回该线程活着的
  worker,修不动才派 fixer 在该 worktree 内修到绿(契约见 `operator-spec.md`)。

review-panel.js 是强力(opus)审核。**没有单独的 checker**:巡检并入审核 Workflow(审核本就要读状态)。

### 自驱层:codex `/goal` = 长跑 worker

- 真正干活的长跑、不确定线程,在 detached tmux 里跑,由强目标契约(budget + blocked-stop)兜住。
- 挂只读审计(reviewer 读其 transcript)+ Esc 急停(`interrupt-target.sh`)。
- codex 全走 tmux,唯一驱动器是 `inject-steer.sh`(注入)与 `interrupt-target.sh`(软停):**对 live
  codex TUI 绝不手搓 raw `send-keys`**(普通 shell pane 上用 send-keys 启动 codex 二进制是允许的,见
  `codex-integration.md`)。
- **启动纪律(CANON [H])**:任何 codex 调用只能经 tmux 启动 + subagent 监控到完成;**禁用** codex 插件
  命令 / `codex exec` / companion CLI(断连 / 网关 502 即零产物)。与 `/loop` 是否在跑解耦,inline 场景
  也照办。见 `codex-integration.md`。

### 为什么 tick 内不能是确定性编排、tick 触发却必须是

确定性编排要求扇出形状、终止条件、budget 在进入时就定死;tick **内部**恰恰相反——在途线程数量与
依赖随事件动态变化、需在 review 待定时择机推进别的就绪线程,这是开放式 agent 推理。反过来,tick
的**触发**不能靠模型自觉——没有外部节拍,长跑会话会漂移或停摆——所以交给确定性机械件
(session cron 心跳,CANON [Q]);上下文的有界性同理交给机械件(auto-compact 窗口钳制),
不靠模型自清。

---

## 2. subagent 边界(动手都在 subagent 内)

这是 curryflows 最硬的结构约束,直接决定能力隔离与上下文隔离:

| 角色 | 能力 | 不能做 |
|---|---|---|
| 协调器(主 session,持久上下文 + 受控压缩) | 推理、决策、调官方 Workflow 审核、**内联机械操作**(tmux 起/驭、commit、串行合 main、终态回收;驱动 codex 只经 inject-steer / interrupt-target)、写看板 jsonl(经 board.py)、**写文档类文件**(计划 / 契约 / 说明 / 覆盖矩阵 markdown) | 不读巨型 transcript/diff(隔离进 Workflow / subagent)、构建/测试输出不整读(重定向 + exit code)、**绝不自己写/改代码(源码 / 测试 / 脚本含 Workflow `.js`)并自己调试**(CANON [J]) |
| 审核 Workflow `review-panel.js`(opus,只读) | 读 transcript/diff、跑 discover-threads、多 lens 审产物 + arbiter 收敛 | 不改代码、不操作 tmux(只读) |
| fixer subagent(按需,opus) | 在**单个线程的 worktree** 内把合并冲突 / 验证回归修到绿——唯一被授权改码的外派执行体 | 范围锁:只修那个冲突 / 回归,不顺手重构、不碰其他文件、绝不靠近 main |

关键:**巨型 transcript 绝不进协调器主 session 上下文**——它被隔离在审核 Workflow `review-panel.js`
内,Workflow 只回裁决。

**CANON [J] — 协调器的 code/doc 边界**:协调器在 main 树上**只能写文档类文件**(计划 / 契约 / 说明 /
覆盖矩阵等 markdown / 文本);**绝不自己写或改代码——源码、测试、脚本(含 Workflow `.js`)——并自己
调试**。判据:产物要被**编译 / 运行 / 调试**的 → 必须外派(curryflows worker 的 worktree 隔离 / 动态
Workflow / subagent,小任务也照此);只被人或 agent **阅读**的文档 → 协调器可直接写(含改 main 树上的
文档)。理由:协调器一旦下场写代码 + 调试,巨型 diff / 反复 rebuild / 调试上下文就灌进主 session,既撑爆
上下文又绕过跨模型 review 与 worktree 隔离(已观测失败:协调器手搓并连改 8 次 review 的 `.js`、直接在
main 树改 `tests/*.py` / `native.cpp`)。

---

## 3. 跨模型 review(worker=codex + reviewer=Claude + 不投票收敛)

worker 是 codex、reviewer 是 Claude opus,produce 与 review 天然跨模型。每 tick 派**多个** reviewer
(不同 lens,各自独立),分歧即信号:

1. **多 reviewer 一致且依据可判** → 协调器自动处理。
2. **真分歧** → 对照 ground truth(契约 / 权威文档 / GOLD oracle / 复现)裁,**不投票**;裁不动 →
   升人类决策项。
3. **需要 codex 第二意见**时,reviewer 可调 `scripts/codex-review.sh` 拉一份 codex 侧独立审核(可选,
   非每 tick 必跑);codex-review.sh 文件交付,脚本非零退出即返回失败、严禁捏造 findings。

**跨模型硬规则(CANON [G])**:跨模型 review 仅当 `worker.model != reviewer.model` 才成立。默认
worker=codex `/goal`、reviewer=Claude opus,天生跨模型。但若某线程的 worker 是 Claude subagent
(非 codex),则至少一个 reviewer 必须是 codex 腿(`scripts/codex-review.sh`)——此时
`codex-review.sh` 是**必需而非可选**,否则审核退化为单模型、跨模型保证作废。协调器必须保证 reviewer
模型集合里存在与 worker 不同的模型。

这套机制把人类决策队列过滤到极少数。reviewer 的反捏造 + 独立复验职责见 `reviewer-spec.md`。

**两条把"环境 / 独立性验证前移"的硬规则**:**seal 前 environment-precondition dry-run(CANON [O])**——
契约声明的环境前提(baseline 绿 / venv 可装 / 预期 drift)在 seal 前由 seal-gate 在一个 throwaway
worktree 上真跑(`scripts/precondition-dryrun.sh`),不成立即不予封定,把"契约假设了没验证过的前提"挡在
seal 前而非 worker STEP-0;**独立复验锁 L3(CANON [P])**——`committed→verified` 的独立复跑必须抹 venv +
删 `.so` + clean rebuild + 亲自跑,reviewer 声明实际达到的档位(L1/L2/L3),弱独立冒充强独立不认。见
`reviewer-spec.md` / `task-contracts/task.md`。

---

## 4. barrier 模型(异步、非阻断)

curryflows 把"人类必须确认"收敛成极少数 barrier,其余靠"疑问→就地跨模型 review→分歧 settle
不了才升"自动消化。人类异步处理的路径 = 在主 session 对每 tick 摘要直接回复(摘要完整列出全部
open 决策项,协调器落 `board.py resolve-decision`;board-tui 仅只读查看,CANON [R]),
**前进不等人**。

硬闸:**对外不可逆**、**跨模型真分歧**(**合 main 已自动化——`verified` 即自动合,仅验证失败才升,
CANON [L]**);另有 **seal-contract** 在开头封定 worker 的目标契约(plan-tree 交叉评审 + 人封)。barrier
与决策项格式见 `decision-surface.md`。

**启动不是 barrier(CANON [I])**:协调器主动问人类而无回答时,默认**起 `/loop`** 推进可执行的活、把
问题挂到决策面异步裁,绝不静默退回 inline。上面两类硬闸 + seal-contract 仍只挡各自的不可逆动作 / 未封
契约线程,不挡 loop 跑别的就绪线程。见 `decision-surface.md` §1b。

**决策面无弹窗(CANON [K])**:协调器 /loop 全程**绝不 `AskUserQuestion`**;barrier 与一切需人判项只经
`board.py post-decision` 进异步决策面 + 每-tick 摘要完整列出,**只 hold 相关线程、其余照推**,人类在
主 session 对摘要直接回复异步裁——回复是人有空时主动打字,不是弹窗,与本条不冲突。无依赖的下一波
直接推进,不问不停;混合波推进可推进部分、只入队需决策部分。见 `decision-surface.md` §1c。

**决策项真停其线程(CANON [N])**:入队 = 那条线程真停等人——`blocked-human` + 协调器对其 codex 注入
Esc 软停(`interrupt-target.sh`,进程存活、goal 上下文完整,**绝不 reap**;与 `verified` 的 session-reap
区分);**沉默 = 继续等,不是同意**,禁"知悉未异议 / 采纳推荐默认 / 异步 veto / 协调器对 barrier 自裁"
自行放行;人类 resolve 后 `inject-steer.sh` 注入同一 pane 续跑。[K](不弹窗)+ [N](那条线程真停)+ [M]
(其余线程有活可推)三者互补;[I] 的"没答按默认走"只管启动、绝不用于 barrier 决策项。见 `decision-surface.md` §1e。

**终端表面(CANON [R])**:人类表面是终端原生两层——T0 `board.py summary` 一行摘要常显
(`watch` / tmux status-right)、T1 `board-tui.py` 全屏 curses 看板,独立只读查看器,随时新开
一个终端 `cfx-board` 即看(无参自动发现:cwd 上溯 → 注册表挑选)。TUI 是 durable 看板的**纯只读
渲染器,零写路径**——不 resolve / reject、不开关 pause(只显示 PAUSED)、**绝不执行生命周期操作**
(起 / 驭 / commit / 合 main / 回收)——生命周期与看板的写入者只有协调器一个;关闭 TUI 不影响
推进。**决策输入面 = 主 session 对话**:每 tick 摘要完整列出全部 open 决策项,人直接回复,协调器
落 `board.py resolve-decision`;pause 由人 `touch` / `rm <project>/.curryflows/pause`(或让主
session 代执行);人类另保有 attach 到 worker pane 亲手 Esc 急停的既有权利(不经 TUI 实现)。
权威定义见 `board.md`「终端表面」。

---

## 5. speculation + commit + 资源回收

- 每个长跑 worker = **独立分支 + 独立 worktree**(默认 base `~/.cache/curryflows/worktrees/<project>/<thread-id>`,可配)。
- worker 在自己的分支/worktree 上 speculative 推进,全程不碰 main。
- **调度流水线(CANON [M])**:契约 scoping 与在途执行重叠(双水位:in-flight / sealed-ready 低于并发
  水位即补 launch / 补 scoping,绝不等上一波收官);无真依赖切片 base 启动时的 main、不等在途线程
  merged,真依赖可 base 依赖线程的 committed 分支提前起;线程就绪即单独推进整条 commit→verify→merge
  链,绝不整波同步。权威见 `coordinator.md`「调度纪律」。
- 合 main **自动化**(CANON [L]):`verified` 后协调器串行(一次一个)rebase 最新 main + 重跑验证,
  **绿则自动合(→ merged)**;冲突 / 验证回归走 **worker-first 修复链**——先驭回该线程活着的 worker
  (会话保活到 merged,CANON [B] 修订),修不动派 fixer subagent(worktree 内修到绿),协调器绝不
  亲手改码(CANON [J]);循环到绿再合、不升人类,唯真·跨模型分歧走 model-divergence。
- **终态一并回收(CANON [B] 修订)**:`merged` / `rolled-back` 后把 tmux 会话、worktree、分支一并
  回收(`reap.sh`),硬职责、不指望收尾钩子;`verified` 阶段保留会话(冲突才有活 worker 可驭回),
  `blocked-human` 绝不 reap。`discover-threads.py` 双向对账给出可回收集。
- worker 生命周期状态机(`ready → running → idle → reviewed → committed → verified →
  merged | rolled-back`,另加可从任意状态进入的 `blocked-human`;`session-reaped` 保留枚举、常规流
  不再经过)见 CANON [A],详见 `coordinator.md`。

---

## 6. per-project 状态(综合看板)

curryflows skill 本身通用,不写死任何项目路径。每个项目的运行态落在 `<project>/.curryflows/`,
不进 skill 仓(格式见 `board.md`):

- `board/threads.jsonl` — 线程台账(`discover-threads.py --board` 对账对象;含 `codex_session`、`branch`)。
- `board/decisions.jsonl` — 人类决策队列。
- `board/ticks.jsonl` — 每 tick 完整裁决(durable 历史,摘要的后备)。
- `board/backlog.jsonl` — 任务补给队列(CANON [M];dedup + 拒绝记忆)。
- 人类异步视图 = 终端表面(`board.py summary` + `board-tui.py` 只读查看器,CANON [R],见
  `board.md`「终端表面」),直接读上述 jsonl,不落额外渲染产物;决策回复走主 session 对话。
- `contracts/<thread-id>.md` — 已封每线程契约(`task-contracts/task.md` 填好的副本;`threads.jsonl`
  的 `contract` 字段指向它,seal 前置校验见 `board.py validate-contract`)。
- worktree 内 `${worktree}/.curryflows/` — 单个 worker 的证据落盘(validate 日志、findings、diff 等)。

---

## 7. 数据流

```
人类
 │  seal-contract(plan-tree 交叉评审 + 人封 worker 的目标契约)
 ▼
任务契约(task-contracts/task.md)+ backlog.jsonl(每 tick 补货,CANON [M])
 │
 │  session cron(/loop <间隔>)按节拍注入 tick prompt —— 持久上下文,auto-compact 有界(CANON [Q])
 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 协调器 tick:0 自检 → 1 读回(对账,看板为准) → 2 审核 → 3 决策    │
│ → 4 内联操作 → 5 补货 → 6 落盘 + 回摘要                            │
└───┬──────────────────────────┬──────────────────────────┬──────────┘
    │ ② 审核(官方 Workflow)     │ ④ 内联操作(起/驭/commit/  │ ①⑥ 有界读/写
    ▼                          │   串行合 main/终态回收)   ▼
┌──────────────────────┐       ▼                    ┌──────────────────────┐
│ Workflow             │  ┌──────────────────────┐  │ board/*.jsonl        │
│ review-panel.js      │  │ codex /goal worker   │  │ threads/decisions/   │
│ (opus,只读)          │  │ (detached tmux)      │  │ ticks/backlog        │
│ · 逐线程 pipeline    │  │ 强契约 budget+       │  │ (durable 真相源)    │
│ · 多 lens+discover   │  │   blocked-stop       │  └──────────────────────┘
│ · arbiter 收敛回裁决 │  │ 分支+worktree(隔离)  │
│ (隔离巨型 transcript)│  │ 冲突/回归先驭回它修  │
└──────────┬───────────┘  └──────────────────────┘
           │ 裁决(蒸馏)     (worker 修不动 → fixer subagent,唯一改码外派体)
           ▼
   协调器决策(③ 薄):一致+可判 → 自动;真分歧 → 升人类;落地已回复决策项
        │ 回主 session:一条清晰摘要(不糊弄;open 决策项完整列出,其余只给指针)
        ▼
   人类(异步看 cfx-board / board.py summary;在主 session 回复裁决,前进不等人)
```

数据流要点:契约从人类经 seal 进入,补给由每 tick 的 scoping 常设步持续填充(`backlog.jsonl`);
每 tick 先读回对账(看板为准)再审核(官方 Workflow,巨型 transcript 隔离其内)→ 决策 →
内联操作 → 写 durable 看板 + 回摘要;人类只在队尾异步看蒸馏后的决策面,决策不阻断推进。
