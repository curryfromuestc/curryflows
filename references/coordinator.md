# 协调器 tick runbook(`/loop` 动态模式)

协调器是 curryflows 三层控制流的外层调度(见 `SKILL.md`、`architecture.md`)。它以 `/loop`
动态模式运行:每个 tick 以"审核优先"推进,无就绪事项时 park 释放上下文,被事件(线程完成、人类
回复、定时唤醒)重新拉起。这一层是 agent 推理,不是确定性脚本。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`coordinator.md` — coordinator tick
> runbook + `/loop` prompt。

## 唯一硬约束:协调器上下文不被撑爆

协调器自己**不读巨型 transcript / diff、不跑长脚本、不直接操作 tmux**。这些全外派给 subagent,
subagent 的大上下文随它消亡,协调器只收回蒸馏结论。协调器会 park、会被压缩、会跨多个唤醒周期
存活,所以它的真相源是 durable 看板文件,不是上下文。

## 状态落盘:看板是真相源

```
<project>/.curryflows/board/threads.jsonl    # 每行一个线程记录
<project>/.curryflows/board/decisions.jsonl  # 每行一个决策项
<project>/.curryflows/board/ticks.jsonl      # 每行一个 tick 的完整裁决(durable 历史)
<project>/.curryflows/board/dashboard.html     # render-board.py 渲染的综合看板(人类异步视图)
```

每个 tick 开始时,协调器从看板读回状态,而不是依赖上一个 tick 残留在上下文里的记忆;tick 结束时,
把状态变更写回看板。字段格式见 `board.md`。

## tick 五步:审核 → 决策 → 操作 → 写看板 → park

每个 tick 严格按以下顺序执行。

### 1) 审核(协调器调官方 Workflow 跑 review-panel.js)

> 前提:协调器会话须已开 ultracode / 已 opt-in 官方 Workflow(与"开局同挂 ultracode + curryflows"
> 一致),才能调 `Workflow` 工具。

tick 第一件事是由协调器调用 Claude Code **官方 Workflow 工具**,跑随仓附带的参考脚本
`workflows/review-panel.js`,而不再由协调器手搓并发派 N 个 reviewer subagent。调用接口(权威,以脚本
实际为准):

```text
Workflow({ scriptPath: "<skillDir>/workflows/review-panel.js", args: {
  board, skillDir, projectDir,
  threads: [{ thread_id, worktree, branch, codex_session, contract, worker_model }] } })
→ { reviews: [{ thread, branch, worktree, verdict(pass|continue|escalate),
     findings, dissent, escalate[], resources[] }], escalations: [...] }
```

**`args` 必须作为真正的 JSON 对象传给 `Workflow` 工具,绝不预先 `JSON.stringify` 成字符串**——传字符串会
让脚本内 `args.threads === undefined` → 空数组 → 静默返回 "no threads to review" / 0 agent(已观测失败)。
**若 review-panel 返回 0 线程,缺陷在 args 形状,就地修正 args;绝不 inline 手搓一个替代 review 脚本**——那
等于把 review 引擎 + transcript 拉进协调器上下文,违反隔离约束与 **CANON [J]**(协调器不自己写 + 调代码)。

`review-panel.js` 内部承载原 reviewer/arbiter 的全部契约语义(完整契约见 `reviewer-spec.md`),
只是改由这个官方 Workflow 执行:

- **pipeline 逐线程**;**stage1 并发多 lens**(correctness / bounds / invariant / repro,
  Explore agent)+ 每 lens 跑 `python3 <skillDir>/scripts/discover-threads.py --project . --board
  ./.curryflows/board/threads.jsonl` 做资源对账(标出 `UNREGISTERED` / `RUNAWAY-SUSPECT` / 孤儿
  worktree / 可回收资源)+ 跨模型硬规则(worker 非 codex 时追加一条 `codex-review.sh` 腿)。
- **审产物**:读在途 codex worker 的 transcript/diff,对照其目标契约审:是否 drift / 捏造 /
  假实现 / 越界 / 破坏不变量;是否撞到 barrier。**巨型 transcript 隔离在 Workflow 自己的上下文里,
  绝不进协调器**,只回裁决结论 + 指向 checked-in 证据的指针。
- **stage2 每线程 arbiter 收敛**:不投票、对照契约 ground truth、裁不动则 escalate;收敛在 Workflow
  内完成,协调器在第 2 步直接消费返回的 `{reviews, escalations}`。

没有单独的 checker——巡检并入 review-panel.js 的 lens。

### 2) 决策(协调器,薄)

协调器拿回 Workflow 返回的 `{reviews, escalations}`(arbiter 收敛已在 Workflow 内完成),直接消费收敛
裁决就地推理决策(不读大文件)。**所有看板写入一律走
`scripts/board.py`(`upsert-thread` / `post-decision` / `resolve-decision`),绝不手编
`threads.jsonl` / `decisions.jsonl`**(手编易写坏行,而 render-board.py 对坏行静默跳过,会无声丢
状态;board.py 写操作原子、非法枚举/缺必填一律 fail-closed):

- **消费收敛裁决**:收敛由 review-panel.js 的 arbiter 在 Workflow 内完成(不投票、对照契约 ground
  truth),协调器只消费返回值——`verdict=pass`/`continue` → 自动处理;`verdict=escalate` 及
  `escalations[]`(裁不动)→ 用 `board.py post-decision`(status=open)追加决策项,并用
  `board.py upsert-thread --state blocked-human` 把对应线程置 `blocked-human`。
- **落地人类已回复的决策项**:对 `board.py list-decisions --open` 读回的已裁决项,定下要执行的操作
  (合 main / 回滚 / 注入指令),并用 `board.py resolve-decision` 标记 resolution。
- **处置 runaway**:Workflow 返回 `resources[]` 报的 `UNREGISTERED` / `RUNAWAY-SUSPECT` / 孤儿
  worktree → 决定软停 + post 决策项;在 runaway 未被人类裁决前,本 tick 不扩张 codex 占用。
- **决定可回收集**:Workflow 返回 `resources[]` 报的跑完 / 孤儿资源 → 标记本 tick 由 operator 回收。
- **决定要不要起新 worker**:对 `state=ready` 的线程,定下分支 `curryflows/<thread-id>` + worktree +
  目标契约,交给 operator 起(尊重并发上限)。**seal-contract 前置(起 worker 前必过)**:已封契约落在
  `<project>/.curryflows/contracts/<thread-id>.md`(`task-contracts/task.md` 填好的副本,
  `threads.jsonl` 的 `contract` 字段指向它),且必须通过 `python3 <skillDir>/scripts/board.py
  validate-contract --file ./.curryflows/contracts/<thread-id>.md`(fail-closed:7 个必填字段齐且非空
  ——outcome、verification、constraints、boundaries、iteration、budget、blocked_stop;校验不过不得起
  worker)。
- **补货与水位(CANON [M] 硬项)**:in-flight(`running`)低于并发水位且 sealed-ready 池非空 → 本 tick
  **必须**补 launch 至水位;sealed-ready 池低于水位 → 本 tick **必须**起(或续)下一批 scoping,与在途
  worker 并行、绝不等上一波收官。线程就绪即单独推进,绝不等波友(权威定义见下「调度纪律(CANON [M])」)。

barrier 共 4 个取值(见 `decision-surface.md`):运行期升人类的**两类**——对外不可逆、model-divergence;
**合 main 已自动化(CANON [L]:`verified` 即自动 rebase + 重跑验证 + 合;冲突 / 验证回归 operator 自动修、不升,唯真·跨模型分歧走 model-divergence)**;
另有 seal-contract 在开头封定契约。**决策默认不阻断**:
就绪线程照推,人类决策异步进行。**绝不 `AskUserQuestion`(CANON [K])**:需人判的只 post-decision 进
`decisions.jsonl` + 摘要给指针,只 hold 该线程、其余照推;混合波推进可推进部分、只入队需决策部分;全卡住
才 park 等事件,而非弹窗。

### 3) 操作(后派 1 个 operator subagent)

把第 2 步定下的所有写动作交给一个 **operator subagent**(opus,可改)一次性执行(完整契约见
`operator-spec.md`):

- **起新 worker**:建分支 + worktree,在 detached tmux 里起 codex /goal(用 `inject-steer.sh`
  注入封定的目标契约),回传 rollout session-id;长跑线程归 tmux/看板所有,**不随 operator 退出而死**。
- **驭在途 worker**:`inject-steer.sh` 注入人类裁决后的指令,或 `interrupt-target.sh` 软停。
- **回收资源**:对可回收集跑 `reap.sh`(`tmux kill-session` + `git worktree prune`)。
- 回传:本 tick 起了/驭了/回收了什么 + 新 session-id。

### 4) 写看板 + 回摘要

- operator 回传后,协调器**用 `board.py upsert-thread` / `resolve-decision` 把状态变更写回
  `threads.jsonl` / `decisions.jsonl`**(board.py 是看板 JSONL 的唯一写入者,写操作原子、非法枚举/缺
  必填一律 fail-closed,绝不手编);本 tick 的完整裁决**经 `board.py record-tick --file <tick.json>`**
  append 到 `ticks.jsonl`(协调器备好 `{tick, summary, reviews, decisions_made, operator}` 数据文件再调它,
  **绝不手 append / `>`**)。看板 HTML 由常驻的 serve-board 实时重渲染,协调器无需每 tick 再刷;需要离线快照时才跑
  `render-board.py` 落一份 `dashboard.html`。
- 向主 session 回**一条清晰、不糊弄的摘要**(schema 见 `board.md`):每条线程状态/进展/预算余额、
  审核裁决含异议、未验证项/风险/越界、待人类决策项、本 tick 回收的资源。完整内容只给指针。

### 5) park 或 continue

- 若仍有就绪线程、待处理项,或**水位欠账**(CANON [M]:in-flight / sealed-ready 低于并发水位且有活
  可补)→ 不 park,继续下一个 tick;
- 若无就绪事项 → arm 一个 Monitor(等线程完成 / 人类回复事件),并 ScheduleWakeup 在 1200–1800s 后
  再唤醒一次,然后停下释放上下文。被事件或定时唤醒后,从第 1 步重新开始。

## 调度纪律(CANON [M]):流水线推进,绝不整波同步

> 动机(已观测):一次实现战役峰值并发仅 4-5、约一半时间 ≤1 个 worker 在跑、波间出现 2h20m 零并发
> 空窗——契约只在上一波基本清完后才开始 scoping,sealed-ready 池永远空,worker 池饿死。瓶颈不是
> 并发上限,是契约供给。本节为权威定义,`SKILL.md` / `architecture.md` / `operator-spec.md` /
> `decision-surface.md` 交叉引用。

**[M1] 双水位(供给责任)**:并发水位 = 并发上限(默认 4,按预算 / 机器可配)。每 tick 决策必查:

- in-flight(`running`)低于水位 且 sealed-ready 池非空 → 本 tick **必须**补 launch 至水位;
- sealed-ready 池(已封契约、随时可起)低于水位 → 本 tick **必须**起(或续)下一批 scoping——
  scoping / 对抗 seal-gate 是 Workflow / subagent 的活,与在途 worker 天然并行、不占协调器上下文,
  **绝不等上一波收官再备货**。

**[M2] base 策略(依赖不拍平成波序)**:

- 无真依赖的切片:base 用**启动时的 main**,绝不等任何在途线程 merged——合并期 main 前进造成的
  漂移由 CANON [L](串行 rebase + 重验)兜底;
- 有真依赖的切片:可 base **依赖线程的 committed 分支**提前启动,依赖 merged 后 rebase 收敛,
  不必等它 merged;
- 真依赖 = 本切片的编译 / 测试 / 产出需要依赖线程的产物才能进行;"想 base 最新 main"不是依赖。

**[M3] per-thread 推进(wave 只是报告用语)**:线程一到 `idle`,下个 tick 就把**它自己**推过
review → commit → verify → merge 全链条,绝不等同批线程到齐再批处理(已观测:线程审定"干完"后
拖 1 小时才 merged,滞后期间无新 worker 补位)。合 main 仍串行(CANON [L]),但合并队列按线程就绪
顺序排,不按波;"wave"可留在摘要里当叙事标签,**不得**成为调度单元或同步屏障。

## worker 生命周期

worker(codex /goal,或 Claude subagent)的状态机权威枚举如下,`threads.jsonl` 的 `state` 字段全仓
统一用这一套(写入一律经 `board.py upsert-thread --state`,枚举校验 fail-closed):

```text
ready → running → idle → reviewed → committed → verified → session-reaped → merged | rolled-back
```

另有 `blocked-human`(升人类),可从任意状态进入。各状态含义:

- `ready`:契约已封、worker 未启动。
- `running`:codex /goal worker 在跑。
- `idle`:worker 到 budget / 撞 blocked-stop / 自认完成,待审。
- `reviewed`:reviewer 审完,`last_verdict` 已记。
- `committed`:工作已 commit 到自己分支(durability,非 merge 非 push)。
- `verified`:在 committed 分支的 worktree 上独立复跑通过。
- `session-reaped`:codex tmux 会话已 reap 释放进程,分支 + worktree 保留待人类 merge。
- `merged`:合入 main(终态)。
- `rolled-back`:丢弃(终态)。

`threads.jsonl` 另带可选整型字段 `attempt`(relaunch 次数,默认 1)。

### 转移执行者(谁触发每步)

每个转移都有明确的执行者,置位一律经 `board.py upsert-thread --state` 写看板:

- `ready → running`、`idle 后的 codex 操作`、`reviewed → committed` 的 **git commit**、relaunch:**operator**。
  其中 `reviewed → committed` 是把 worker 的工作 commit 到它自己的分支(durability,非 merge 非 push,
  绝不碰 main)——codex worker 通常只把改动留在工作树、不自行 commit,所以由 operator 在该 worktree 内
  执行 `git commit`(见 `operator-spec.md`「commit worker 工作到自有分支」)。
- `idle → reviewed`(审计)与 `committed → verified`(在 committed 分支 worktree 上独立复跑):**reviewer**
  (见 `reviewer-spec.md`)。
- `verified → merged`:**自动合(CANON [L])**——operator 串行 rebase 最新 main + 重跑验证 + `git merge`,
  协调器置 `merged`(**冲突 / 验证回归 → operator 在 worktree 内直接修 + 重跑验证,循环到绿再合,不升人类**;
  单个搞不定的大改派 codex 修复 worker 接手;唯解决中暴露真·跨模型分歧才走 `model-divergence`)。
- `→ blocked-human` / `→ rolled-back`、各状态置位写看板:**协调器**(决策 + `board.py`)。

### 分阶段 reap

回收分两阶段,避免在人类 merge 前误删分支 / worktree:

- 到 `verified`(已 commit + 独立复跑过,待人类 merge):只 reap 会话(`reap.sh --session ...`),置
  `session-reaped`,保留 worktree + 分支。
- 到 `merged` 或 `rolled-back`:才 reap worktree + 分支(`reap.sh --worktree ...` 与
  `reap.sh --branch ...`)。

### relaunch / 扩展(人类决策扩展某线程后)

复用现有 worktree + 分支(**不重建 worktree**),起全新 codex 会话,注入更新后的已封契约;协调器用
`board.py upsert-thread` 把 `state` 置回 `running`、`attempt` 加一、`codex_session` 更新为新的
rollout id。

## 监督拆分:谁读 transcript、谁动手、谁出廉价信号

- **深度审计 + 廉价信号 = 官方 Workflow 跑 review-panel.js**(reviewer/arbiter 契约语义不变,只是改由
  官方 Workflow 承载执行)。transcript 可达几百 MB,Workflow 把它隔离在自己上下文里,只回裁决;
  `discover-threads.py`(只读首行 + 文件 stat)也在 stage1 各 lens 顺手跑。
- **决策 = 协调器主 session**(薄,不读大文件)。
- **动手 = operator subagent(可改 opus)**。对目标 codex 的写只有两类:Escape(软停)和人类裁决后
  注入的指令,其余全只读。

## start:起协调器前的一次性步骤

`start` 操作在拉起 `/loop` 之前做这些一次性事:

> **启动 fail-open(CANON [I],见 `decision-surface.md`)**:`/curryflows <自由任务>`(非字面 `start`)
> 即视为启动意图。协调器可就第一刀 / 边界提一个非阻断澄清项,但**人类无回答时默认就起 `/loop`**——把
> 未回答的问题挂到 `decisions.jsonl` 异步裁,**绝不因"没拿到放行"而停在 inline**。启动不是 barrier;
> 两类硬闸 + seal-contract 仍只挡各自的不可逆动作 / 未封契约线程,不挡 loop 跑别的就绪线程(合 main 已自动化,见 CANON [L])。

0. **前提**:协调器会话须已开 ultracode / 已 opt-in 官方 Workflow(开局同挂 ultracode + curryflows),
   否则 tick 第一步无法调 `Workflow` 工具跑 `review-panel.js`。
1. 确保看板目录存在:`mkdir -p ./.curryflows/board`。
2. **拉起看板服务(serve-board)——用 Bash 工具的后台模式(`run_in_background: true`),绝不用
   `nohup` / `setsid` / `&` / 前台 `sleep`**(sandbox 会杀掉它们,已观测 `exit 144` 把整条命令一并杀,
   进程根本起不来):

   ```bash
   python3 <skillDir>/scripts/serve-board.py --board ./.curryflows/board --port 8787 --host 0.0.0.0
   # 经 Bash run_in_background:true 运行;--host 0.0.0.0 让 IP 直连 + 端口转发都通(暴露局域网,介意就去掉)
   ```

   起完别被代理假象骗:`curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/`
   得 `200` 才算真起来(本机代理常把它拦成 503,是假象,不代表进程死)。serve-board 只读、session 级
   后台进程(随协调器会话存活,跨 tick / park 不死),协调器每 tick 只写 jsonl,serve 端实时重渲染。
   启动 / 访问(VSCode 转发 / `ssh -L` / IP 直连)/ 排错(`no_proxy`)完整见 `board.md`「看板服务」。

## `/loop` prompt 文本范例

在目标项目根目录,以动态模式起协调器:

```text
/loop 你是 curryflows 协调器,运行在动态(self-paced)模式。看板在
./.curryflows/board/{threads.jsonl,decisions.jsonl,ticks.jsonl,dashboard.html},看板是真相源,
不是你的上下文:每个 tick 先从看板读回状态,tick 末把变更写回看板并刷新 dashboard.html。

硬约束:你(主 session)绝不读巨型 transcript/diff、不跑长脚本、不直接操作 tmux——全部外派给
subagent(一律 opus),你只收回蒸馏裁决。你在 main 树上**只写文档**(计划 / 契约 / 说明 / 覆盖矩阵),
**绝不自己写 + 调代码**(源码 / 测试 / 脚本含 Workflow `.js`)——代码活一律走 worker(worktree)/ 动态
Workflow / subagent,小任务也照此(CANON [J])。**绝不 `AskUserQuestion`**——/loop 全程零阻塞提问;需
人判的一律 post-decision 进 decisions.jsonl + 摘要给指针,只 hold 该线程、其余照推,人类异步裁(CANON [K])。

输出语言:你对我(用户)的每一条摘要 / 叙述 / 追问一律用中文,只有术语 / 标识符 / 命令 / 代码 / 路径
保留英文;读英文源码或英文文档时也不得漂移成英文叙述。

前提:本会话须已开 ultracode / 已 opt-in 官方 Workflow,才能调 Workflow 工具。

每个 tick 按序执行(审核优先 → 决策 → 操作):
1) 审核:调官方 Workflow 工具跑 review-panel.js——
   Workflow({ scriptPath: "<skillDir>/workflows/review-panel.js", args: { board, skillDir,
   projectDir, threads:[{ thread_id, worktree, branch, codex_session, contract, worker_model }] } }),
   拿回 { reviews:[{ thread, branch, worktree, verdict(pass|continue|escalate), findings, dissent,
   escalate[], resources[] }], escalations:[...] }。Workflow 内部 stage1 并发多 lens
   (correctness/bounds/invariant/repro)+ 每 lens 跑 discover-threads.py 对账资源 + 跨模型硬规则
   (worker 非 codex 追加 codex-review.sh 腿),stage2 每线程 arbiter 收敛(不投票、对照契约 ground
   truth、裁不动则 escalate);巨型 transcript 隔离在 Workflow 上下文里,绝不回灌给你。args 必须是真 JSON
   对象(别 stringify,否则 threads 丢失、静默 0 线程);返回 0 线程就修 args,**绝不 inline 手搓 review
   脚本**(CANON [J])。
2) 决策(你,薄):消费 Workflow 返回的收敛裁决(收敛已在 Workflow 内完成)——verdict=pass/continue
   则自动处理;verdict=escalate 及 escalations[] 则用 board.py post-decision 追加决策项、
   board.py upsert-thread 把线程置 blocked-human。**绝不 AskUserQuestion**:无依赖的下一波直接推进,只把
   需决策项入队 + hold 该线程、其余照推;混合波推进可推进部分、只入队需决策部分(CANON [K])。
   落地人类已回复决策项(board.py resolve-decision)。runaway(UNREGISTERED / RUNAWAY-SUSPECT /
   孤儿 worktree)→ 标记软停 + post 决策项,且本 tick 不扩张 codex。标出可回收集。**调度水位(CANON
   [M],硬项)**:in-flight 低于并发水位(=并发上限,默认 4)且有 sealed-ready 线程 → 本 tick 必须补
   launch 至水位;sealed-ready 池低于水位 → 本 tick 必须并行起/续下一批 scoping,绝不等上一波收官;
   无真依赖切片 base 启动时的 main(漂移由 CANON [L] rebase 兜底)、真依赖可 base 依赖线程 committed
   分支提前起;线程一到 idle 就单独推完 commit→verify→merge,绝不等波友,wave 只是叙事标签。
   对 state=ready 线程定下分支/worktree/目标契约(尊重并发上限);起 worker 前契约副本
   .curryflows/contracts/<thread-id>.md 必过 board.py validate-contract。所有看板写入一律走
   scripts/board.py,**绝不手编 / 截断 / `>` jsonl**(破坏性操作前先读内容——已观测:未看就清空毁掉
   durable 历史)。
3) 操作:派 1 个 operator subagent(opus,可改)执行所有写动作:detach 起 codex /goal(用
   inject-steer.sh 注入封定契约,回传 session-id,长跑线程归 tmux 不随它退出而死)、inject/
   interrupt 驭在途 worker、reap.sh 回收可回收集。codex 启动纪律(CANON [H]):只经 tmux 起
   (codex-review.sh 有界腿 / `/goal` 自驱)+ subagent 监控交付文件,**禁用 codex 插件 / `codex exec` /
   companion CLI**(断连 / 网关 502 即零产物);起 codex 一律最高思考强度(显式
   `-c model_reasoning_effort=xhigh`)。
4) 写看板 + 回摘要:用 board.py(upsert-thread / resolve-decision)把变更写回 threads.jsonl /
   decisions.jsonl(board.py 是看板 JSONL 的唯一写入者,绝不手编),**经 board.py record-tick --file
   <tick.json> 追加 ticks.jsonl**(备好数据文件再调,绝不手 append)。看板 HTML 由常驻 serve-board 实时
   渲染;向我回一条清晰不糊弄的摘要(每线程状态/进展/预算余额、
   裁决含异议、未验证/风险/越界、待决策项、本 tick 回收的资源),完整内容只给指针。
5) park 或 continue:有就绪事项或水位欠账(CANON [M])就继续;否则 arm Monitor 等线程完成/人类回复,
   ScheduleWakeup 1200-1800s 后再唤醒,然后停下省上下文。

硬闸:运行期**两类**(默认不阻断推进,人类异步处理)——对外不可逆、model-divergence;**合 main 自动化
(CANON [L]:`verified` 即 operator 串行 rebase + 重跑验证 + 合;冲突 / 验证回归 operator 自动修、不升,唯真·跨模型分歧走 model-divergence)**;另有
seal-contract 在开头封定 worker 目标契约(barrier 取值共 4 个)。
```
