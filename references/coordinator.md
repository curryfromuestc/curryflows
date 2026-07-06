# 协调器 tick runbook(cron 心跳 + 每 tick 全新上下文)

协调器是 curryflows 三层控制流的外层调度(见 `SKILL.md`、`architecture.md`)。它的运行形态是:
一个常驻 detached tmux 会话里的**交互式 Claude 会话**,由一条 session 级 cron(`/loop <间隔>`)
把 tick prompt 按固定节拍重新注入;**每个 tick 都在全新上下文上执行**,tick 的最后一个动作用
`scripts/arm-rebirth.sh` 给自己安排一次延迟 `/clear`(CANON [Q])。tick 内部是开放式 agent 推理
(审、裁、调度不可脚本化),tick 的触发与重生是确定性机械件——两层职责分离。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`coordinator.md` — coordinator tick
> runbook + tick prompt 模板。

## CANON [Q]:tick 上下文一次性(cron 心跳 + 尾步自 arm /clear)

协调器上下文是**一次性资源**:每 tick 从看板冷启动,tick 末整体丢弃,跨 tick 状态**只**存在于
durable 看板。没有 park、没有 ScheduleWakeup、不依赖自动压缩——长战役下长驻上下文每 tick 要
全量重读自己的历史(间隔超过缓存 TTL 即全价),且压缩有损;fresh-per-tick 把这两个问题一起消掉,
空转 tick 变得廉价,间隔因此可以收紧(见「节拍与反应延迟」)。

机制事实(全部已实测,勿凭记忆推翻):

- **cron 任务挂在进程上,不挂在对话上**:`/loop <间隔> <prompt>` 建的是 session 级定时任务,
  `/clear` 之后照常按点触发,且触发的 prompt 落进清空后的新上下文。反复 /clear 无碍。
- **/clear 无法由模型自己执行**:它是客户端内置命令——模型的 Skill 工具明确拒绝内置命令;
  cron 的 payload 是作为用户消息投给模型的,payload 写 `/clear` 不会被客户端命令解析器执行
  (已实测两个触发边界均未清空)。hook 也无此能力。
- **唯一正道**:`tmux run-shell -b` 把一次性任务交给 **tmux server** 执行——它在 arm 它的
  turn 结束后仍存活(sandbox 杀得掉 `nohup`/`&`,杀不掉 server 的子进程),延迟数秒后把
  `/clear` 打进协调器自己的 pane。这正是 detached codex worker 赖以存活的同一机制。
- **挂进程不挂对话的还有两样(已实测)**:带 "ultracode" 关键词的 prompt 在 `/clear` 之后照常
  可调 Workflow 工具(per-prompt opt-in,不依赖会话状态——心跳 payload 因此必须带关键词);
  Bash 后台模式(`run_in_background`)起的进程(如 serve-board)跨 `/clear` 存活。

于是每个周期是:**cron 注入 tick prompt → tick 在新上下文上执行 → 尾步 arm 重生 → turn 结束
→ server 延迟 `/clear` → 下一次 cron 又落在空上下文**。误清与漏清都自愈:看板承载全部跨 tick
状态,误清最多损失当前 tick 未落盘的过程推理;漏清(忙检/暂停/copy-mode 跳过、tick 中途崩溃
没走到 arm)只是下一 tick 落在未清空的上下文里——那轮末尾会重新 arm,最多脏一轮。

## 唯一硬约束:单 tick 内不灌大读

fresh-per-tick 解决的是**跨 tick 累积**;**tick 内**的巨读仍必须隔离:巨型 transcript / 大 diff
只进 review-panel Workflow / subagent 的上下文,随其消亡,协调器只收蒸馏裁决。协调器内联跑
构建/测试时一律 **no-slurp**:输出重定向到文件,只读 exit code,失败才 `tail -50`。

## 状态落盘:看板是唯一真相源

```
<project>/.curryflows/board/threads.jsonl    # 线程台账(状态机)
<project>/.curryflows/board/decisions.jsonl  # 人类决策队列
<project>/.curryflows/board/ticks.jsonl      # 每 tick 完整裁决(append-only,读回一律 list-ticks --last K)
<project>/.curryflows/board/backlog.jsonl    # 任务补给队列(CANON [M];含拒绝记忆)
<project>/.curryflows/tick-prompt.md         # 本项目实例化的 tick prompt(cron 指向它)
<project>/.curryflows/pause                  # 存在即人类接管:tick 自停 + arm-rebirth 不清
<project>/.curryflows/temp/rebirth.log       # arm/fire/skip 台账(tick 步骤 0 自检活性用)
```

凡是"下个 tick 需要知道"的信息,**必须**在本 tick 内经 `board.py` 落盘——上下文在 tick 末就没了。
写入只走 `scripts/board.py`(唯一写入者,原子 + fail-closed),绝不手编/截断/`>` 任何 jsonl。

## 节拍与反应延迟

tick 间隔 = 反应延迟量子(worker 刚 idle,最坏等一个间隔才被审)。fresh-per-tick 下空转 tick
只是"读看板 → 无事 → arm 收工"(几 k tokens),因此**默认 5m、可收紧到 3m**;把间隔选在
p95 tick 时长之上,tick 超限时下一发 cron 会排队落进未清空上下文——有界(多一轮的量),自愈。
`--delay`(arm 到 /clear 的间隔)默认 20s:足够 turn 收尾,又通常赶在下一分钟边界之前。

## start:起协调器的一次性步骤

> **启动 fail-open(CANON [I],见 `decision-surface.md`)**:`/curryflows <自由任务>` 即视为启动
> 意图;可就第一刀 post 一个非阻断决策项,但人类无回答时默认就把心跳挂起来推进,绝不停在 inline。

1. **宿主 tmux 会话**(注入面要求;zellij/终端只是可选观看外壳,`tmux attach -t cfx-coord` 即看):

   ```bash
   tmux new-session -d -s cfx-coord -c <projectDir>
   tmux send-keys -t cfx-coord 'claude' Enter        # 交互式,不是 claude -p
   ```

   协调器会话须已开 ultracode / 官方 Workflow(心跳 payload 里带 "ultracode" 关键词即可,
   见第 4 步——/clear 后每条 prompt 各自 opt-in,不依赖会话状态)。权限面预先配好
   (allowlist / acceptEdits),无人值守的 tick 不得撞权限弹窗。

2. **实例化 tick prompt**:把本文末尾的模板 copy 到 `<project>/.curryflows/tick-prompt.md`,
   填占位符(`{{PROJECT_DIR}}`、`{{SKILL_DIR}}`、`{{TMUX_SESSION}}`(在会话内
   `tmux display-message -p '#S'` 可得)、`{{WATERMARK}}` 默认 4、`{{INTERVAL}}`)。
   改 tick 行为 = 改这个文件,下个 tick 自动生效,无需重挂 cron。

3. **拉起看板服务**:`serve-board.py` 经 Bash 工具后台模式(`run_in_background: true`)启动
   (绝不 `nohup`/`&`,见 `board.md`「看板服务」)。已实测跨 `/clear` 存活(后台任务挂进程、
   不挂对话);**tick 步骤 0 仍顺手核活**(`curl --noproxy '*' … :8787` 非 200 就重拉),兜底
   进程崩溃 / 协调器会话重启。

4. **挂心跳**(在协调器 TUI 里输入;payload 必含 "ultracode"):

   ```text
   /loop 5m ultracode:完整读取 ./.curryflows/tick-prompt.md,严格按其执行一个协调器 tick。
   ```

   cron 是 session 级、约 7 天过期——tick 步骤 0 负责自检重建,不靠人记。

## tick 七步(0–6):自检 → 读回 → 审核 → 决策 → 操作 → 补货 → 落盘+重生

### 0) 自检与短路(廉价,先于一切)

- `<project>/.curryflows/pause` 存在 → 回一句 "paused",**本 tick 到此为止**(不 arm——
  arm-rebirth 的 fire 侧也会因 pause 拒清,双保险)。
- 活性:看 `temp/rebirth.log` 末行与 `list-ticks --last 1` 的时间戳,间隔异常(> 3 个间隔)
  → 摘要里如实报;cron 触发迹象消失 / 临近 7 天 → 用 CronList/CronCreate 重建心跳。
- 看板核对:`list-threads --open`、`list-decisions --open`、`list-backlog` 与上一 tick 记录
  对比,**无任何变化且无 worker 状态翻转、水位不欠账** → no-op tick:跳过 1–5,直接第 6 步
  只 arm 不 record-tick(ticks.jsonl 不记空转)。

### 1) rehydrate(全部有界读)

```bash
python3 <skillDir>/scripts/board.py list-threads   --board ./.curryflows/board --open
python3 <skillDir>/scripts/board.py list-decisions --board ./.curryflows/board --open
python3 <skillDir>/scripts/board.py list-backlog   --board ./.curryflows/board
python3 <skillDir>/scripts/board.py list-ticks     --board ./.curryflows/board --last 5
```

加上项目北极星文档(如 `plan.md`,若有)。**绝不整读 `ticks.jsonl`**(append-only,会涨到 MB)。
上下文里没有历史——这些读回就是你的全部记忆,缺什么状态说明上一 tick 没落盘,是缺陷,修落盘。

### 2) 审核(调官方 Workflow 跑 review-panel.js,只读)

只送需审线程(`idle`/`committed`,以及在途 `running` 的巡检):

```text
Workflow({ scriptPath: "<skillDir>/workflows/review-panel.js", args: {
  board, skillDir, projectDir,
  threads: [{ thread_id, worktree, branch, codex_session, contract, worker_model, state }] } })
→ { reviews: [{ thread, branch, worktree, verdict(pass|continue|escalate),
     findings, dissent, independence_tier(L1|L2|L3|n/a), escalate[], resources[] }], escalations: [...] }
```

语义不变(契约见 `reviewer-spec.md`):stage1 并发多 lens + 资源对账 + 跨模型硬规则([G]),
stage2 arbiter 收敛不投票;巨型 transcript 隔离在 Workflow 内。`state` 必传(repro lens 据此判
进度审 vs `committed→verified`);**只有 `independence_tier=L3` 才把 committed 线程置 verified**
(CANON [P])。`args` 必须是真 JSON 对象(勿 stringify);返回 0 线程就修 args 形状,
**绝不 inline 手搓替代 review**(CANON [J])。

### 3) 决策(薄)

- `verdict=pass`/`continue` → 自动处理;`escalate` 及 `escalations[]` → `board.py post-decision`
  (status=open)+ `upsert-thread --state blocked-human` + 对该线程 codex 注入 Esc 软停
  (`interrupt-target.sh`,进程存活、goal 上下文完整,**绝不 reap**)。**escalate = arbiter 裁不动
  → 归人类,绝不替换成协调器自己的 RULING;沉默不是同意**(CANON [N])。
- 落地人类已明确 resolve 的决策项:`board.py resolve-decision`(resolution 指向真实人类动作)
  + `inject-steer.sh` 把裁决注入**同一个** pane 续跑(线程回 `running`,零重启)。
- runaway(`UNREGISTERED`/`RUNAWAY-SUSPECT`/孤儿 worktree)→ 软停 + post 决策项,本 tick
  不扩张 codex 占用。
- **绝不 `AskUserQuestion`**(CANON [K]):需人判项只入队 + 摘要给指针,只 hold 该线程,其余照推。

### 4) 操作(协调器内联执行,逐线程推进,绝不成波)

第 2/3 步定下的所有写动作由协调器**亲自跑**(命令细则与回传字段见 `operator-spec.md`「操作
规程」)。逐线程:谁就绪推谁(CANON [M3]),同一 tick 里完全可以 merge t3、launch t7、steer t2。

- **起新 worker**:建分支+worktree → detached tmux 起 codex(`-c model_reasoning_effort=xhigh`,
  CANON [H])→ TUI 起来后 `inject-steer.sh` 注入已封契约 → 回收 session-id 当场
  `upsert-thread` 登记。**起前必过两道 seal 门**:①`validate-contract`(8 字段);
  ②派 subagent 跑 `precondition-dryrun.sh`(throwaway worktree 真跑 preconditions,CANON [O])。
- **驭 / 软停**:一律 `inject-steer.sh` / `interrupt-target.sh`(CANON [F]:对 live codex TUI
  绝不手搓 raw send-keys)。
- **commit 到自有分支**(`reviewed→committed`):在该 worktree 内 add+commit,回 sha。
- **合 main(CANON [L],串行,一次一个)**:rebase 最新 main → **L3 重验**(抹 venv + 删构建
  产物 + clean rebuild + 亲自跑;输出重定向文件,读 exit code)→ 绿则 `merge --no-ff` → 置
  `merged`。**冲突 / 验证回归:协调器绝不亲手改码([J] 亮线)**——修复链依次为:
  ① `inject-steer.sh` 把冲突/回归任务驭回**该线程还活着的 worker**(会话保活到 merged,
  CANON [B] 修订;它带着分支全部上下文,最快);② worker 已亡或修不动 → 派一个 fixer
  subagent 在该 worktree 内修到绿;③ 仍不收敛 → relaunch 续跑。唯解决中暴露真·跨模型分歧
  才走 `model-divergence`。合的是本地 main;推远端仍是 `outward-irreversible` 人类闸。
- **回收(CANON [B] 修订,全部推迟到终态)**:`merged`/`rolled-back` 后一并 `reap.sh`
  session + worktree + branch。**`verified` 不再提前 reap 会话**(保活等 merge,冲突可驭回);
  `blocked-human` 绝不 reap。`session-reaped` 状态保留在枚举,常规流不再经过。

**no-slurp 纪律**贯穿本步:任何构建/测试/rebase 输出 `> file 2>&1`,只读 exit code,失败才
`tail -50`;大 diff 归 review Workflow,不进协调器。

### 5) 补货与探索(每 tick 常设,CANON [M])

- **内联判断**(廉价):in-flight(`running`)对并发水位;`list-backlog --status sealed-ready`
  对水位;对照北极星文档看前沿还有什么没切。
- 双水位欠账即行动:sealed-ready 非空且 in-flight 低 → 本 tick 就 launch 至水位;
  sealed-ready 低 → **同步派一个 scoping subagent** 读仓库/plan 生成候选(重活隔离在它上下文),
  候选依次过 `validate-contract` + `precondition-dryrun.sh` 两道门,过门即
  `upsert-backlog --status sealed-ready`,未熟则 `--status candidate|scoping`。
- **落盘交接是硬规则**:scoping 产物必须在**本 tick 内**写进 `backlog.jsonl` / contracts——
  跨 /clear 的会话内通知不可依赖,一律文件交接。
- **去重与拒绝记忆**由 `board.py` 强制:`dedup_key` 全队列唯一,新 id 撞键即拒(复提必须复用
  原条目、历史可见);`rejected` 必带 `reject_reason`。被否任务复活必须是有意识动作,不许每
  tick 重新冒出来。

### 6) 落盘 + 回摘要 + arm 重生(最后一个动作)

- 状态变更 `upsert-thread`/`resolve-decision`/`upsert-backlog` 写回;本 tick 完整裁决备成
  数据文件后 `board.py record-tick --file <tick.json>`(no-op tick 不记)。
- 向主 session 回**一条清晰、不糊弄的摘要**(schema 见 `board.md`:每线程状态/进展/预算、
  裁决含异议、未验证/风险、待决策项、回收清单、backlog 水位;完整内容只给指针)。
- **最后一个工具调用**:

  ```bash
  bash <skillDir>/scripts/arm-rebirth.sh arm --pane <tmux-session> --project . --delay 20
  ```

  然后收尾输出,结束本 turn。fire 侧五道 guard(pane 进程 allowlist / copy-mode / busy /
  pause / 存在性)+ `temp/rebirth.log` 台账见脚本头注;跳过即自愈,勿重试勿等待。

## 调度纪律(CANON [M]):流水线推进,绝不整波同步

> 动机(已观测):一次实现战役峰值并发仅 4-5、约一半时间 ≤1 个 worker 在跑、波间 2h20m 零并发
> 空窗——契约只在上一波清完后才 scoping,sealed-ready 池永远空,worker 池饿死。瓶颈不是并发
> 上限,是契约供给。本节为权威定义,`SKILL.md` / `architecture.md` / `operator-spec.md` /
> `decision-surface.md` 交叉引用。

**[M1] 双水位(供给责任,落在 backlog.jsonl 上,tick 第 5 步常设执行)**:并发水位 = 并发上限
(默认 4,可配)。每 tick 必查:in-flight 低于水位且 `sealed-ready` 池非空 → 本 tick **必须**补
launch 至水位;`sealed-ready` 池低于水位 → 本 tick **必须**起 scoping 补货——scoping 是
subagent 的活,与在途 worker 天然并行,**绝不等上一波收官再备货**。

**[M2] base 策略(依赖不拍平成波序)**:无真依赖切片 base **启动时的 main**,绝不等在途线程
merged(漂移由 CANON [L] rebase+重验兜底);真依赖切片可 base 依赖线程的 committed 分支提前起。
真依赖 = 本切片的编译/测试/产出需要依赖线程的产物;"想 base 最新 main"不是依赖。

**[M3] per-thread 推进(wave 只是报告用语)**:线程一到 `idle`,下个 tick 就把**它自己**推过
review → commit → verify → merge 全链条,绝不等同批线程到齐再批处理。合 main 仍串行
(CANON [L]),但按线程就绪顺序排,不按波;"wave" 只准出现在摘要叙事里,**不得**成为调度单元
或同步屏障。tick 间隔是反应延迟量子——空转 tick 廉价,间隔往小调,别往大调。

## worker 生命周期

状态机权威枚举不变(CANON [A],写入一律经 `board.py upsert-thread --state`,fail-closed):

```text
ready → running → idle → reviewed → committed → verified → merged | rolled-back
```

另有 `blocked-human`(可从任意状态进入)、`session-reaped`(枚举保留,常规流不再经过——见
CANON [B] 修订)。各状态含义与转移执行者:

- `ready → running`(起 worker)、`reviewed → committed`(git commit)、`verified → merged`
  (CANON [L] 自动合)、relaunch:**协调器内联执行**(第 4 步)。
- `idle → reviewed`(审计)与 `committed → verified`(L3 独立复验,CANON [P]):**review-panel
  Workflow**(见 `reviewer-spec.md`)。
- `→ blocked-human` / `→ rolled-back` 置位:**协调器**(决策 + board.py)。

**回收时点(CANON [B],修订:终态一并)**:回收全部推迟到终态——`merged` / `rolled-back` 后一并
reap session + worktree + branch(逐资源调 `reap.sh`,退出码各自归属);`verified` 阶段**保留
codex 会话**(idle 占用极小),使合并冲突 / 验证回归能经 `inject-steer.sh` 驭回原 worker 修复;
`blocked-human` 绝不 reap(reap 会丢整个 goal 推理态)。

**relaunch / 扩展**:复用现有 worktree + 分支(绝不重建),起全新 codex 会话注入更新后的已封
契约;`upsert-thread` 置回 `running`、`attempt` 加一、`codex_session` 更新。

## 监督拆分:谁读 transcript、谁动手

- **深度审计 + 廉价信号 = review-panel Workflow**(几百 MB transcript 隔离在其内,只回裁决;
  `discover-threads.py` 在 stage1 各 lens 顺手跑)。
- **决策 + 机械动作 = 协调器内联**(fresh 上下文 + no-slurp 使其安全;对 codex 的写只有
  Escape 和人类裁决后注入的指令两类)。
- **改代码 = worker / fixer subagent,永远不是协调器**(CANON [J] 亮线:协调器绝不 Edit/Write
  源码/测试/脚本并自己调试;它只指派)。

## tick prompt 模板(copy 到 `<project>/.curryflows/tick-prompt.md` 后填占位符)

```text
你是 curryflows 协调器。本条消息由 session cron 注入,你正运行在一个全新上下文上:没有任何
对话记忆,唯一真相源是看板文件。配置:PROJECT_DIR={{PROJECT_DIR}};SKILL_DIR={{SKILL_DIR}};
TMUX_SESSION={{TMUX_SESSION}};并发水位={{WATERMARK}};tick 间隔={{INTERVAL}}。
输出语言:对用户的一切叙述/摘要一律中文,仅术语/命令/路径保留英文。

硬规则(全程有效):你绝不 Edit/Write 代码文件(源码/测试/脚本)也绝不自己调试——改码一律
worker / fixer subagent,你只指派(CANON [J])。绝不 AskUserQuestion;需人判项一律
board.py post-decision 入队 + 只停该线程,其余照推(CANON [K]/[N],沉默不是同意)。对 live
codex TUI 只用 inject-steer.sh / interrupt-target.sh,绝不手搓 send-keys(CANON [F])。起
codex 只经 tmux + 显式 -c model_reasoning_effort=xhigh,禁用 codex 插件/codex exec/companion
(CANON [H])。任何构建/测试输出重定向到文件只读 exit code,失败才 tail -50;巨型
transcript/diff 只进 Workflow/subagent,绝不进你的上下文。所有看板写入只走
{{SKILL_DIR}}/scripts/board.py,绝不手编/截断 jsonl。凡下个 tick 需要知道的,本 tick 必须落盘。

按序执行一个 tick(细则见 {{SKILL_DIR}}/references/coordinator.md,冲突以本文为准):
0) 自检:若 ./.curryflows/pause 存在 → 只回复 "paused" 并结束(不 arm)。查
   temp/rebirth.log 末行与 list-ticks --last 1 的时间戳,异常(>3 个间隔)在摘要报;心跳 cron
   消失/临近 7 天过期则重建。若看板对比上一 tick 记录零变化、无状态翻转、水位不欠账 →
   no-op:跳到第 6 步只 arm、不 record-tick。
1) 读回(全部有界):board.py list-threads --open / list-decisions --open / list-backlog /
   list-ticks --last 5(--board ./.curryflows/board),加项目北极星文档;绝不整读 ticks.jsonl。
2) 审核:Workflow({ scriptPath: "{{SKILL_DIR}}/workflows/review-panel.js", args: { board,
   skillDir, projectDir, threads:[{ thread_id, worktree, branch, codex_session, contract,
   worker_model, state }] } });args 必须真 JSON 对象;只送 idle/committed/巡检线程;
   independence_tier=L3 才可置 verified(CANON [P]);返回 0 线程改 args,绝不手搓替代 review。
3) 决策:pass/continue 自动处理;escalate → post-decision + blocked-human + interrupt-target.sh
   软停(绝不 reap、绝不自裁 RULING);落地人类已 resolve 项(resolve-decision +
   inject-steer.sh 注回同一 pane 续跑);runaway → 软停 + 入队,本 tick 不扩张。
4) 操作(你内联执行,逐线程,绝不成波):起 worker(两道 seal 门:validate-contract +
   派 subagent 跑 precondition-dryrun.sh)/ 驭 / commit / 串行合 main(rebase → L3 重验 →
   绿则 merge)。冲突或回归:先 inject-steer.sh 驭回该线程活着的 worker 修(会话保活到
   merged),不行再派 fixer subagent,你自己绝不改码。merged/rolled-back 后才逐资源 reap.sh
   (session+worktree+branch);verified 不 reap 会话;blocked-human 绝不 reap。
5) 补货(常设):数 in-flight 与 sealed-ready 对水位({{WATERMARK}});欠 launch 即补至水位;
   sealed-ready 低则同步派 scoping subagent 生成候选 → 过两道 seal 门 → upsert-backlog
   (dedup-key 必填;rejected 必带 reject-reason;产物本 tick 落盘,不留在对话里)。
6) 落盘+重生:upsert-* 写回;非 no-op tick 把 {tick,summary,reviews,decisions_made,operator}
   备成 JSON 文件经 board.py record-tick 追加;回一条不糊弄的中文摘要(每线程状态/进展/预算、
   裁决含异议、未验证项、待决策项、回收清单、backlog 水位,完整内容只给指针);最后一个工具
   调用执行:bash {{SKILL_DIR}}/scripts/arm-rebirth.sh arm --pane {{TMUX_SESSION}}
   --project {{PROJECT_DIR}} --delay 20,然后结束本 turn。
```
