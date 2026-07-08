# 协调器 tick runbook(cron 心跳 + 持久上下文 + 受控压缩)

协调器是 curryflows 三层控制流的外层调度(见 `SKILL.md`、`architecture.md`)。它的运行形态是:
一个常驻 detached tmux 会话里的**交互式 Claude 会话**,由一条 session 级 cron(`/loop <间隔>`)
把 tick prompt 按固定节拍重新注入;上下文跨 tick 持续存在,由 auto-compact 有界(CANON [Q])。
tick 内部是开放式 agent 推理(审、裁、调度不可脚本化),tick 的触发是确定性机械件(session
cron)——两层职责分离。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`coordinator.md` — coordinator tick
> runbook + tick prompt 模板。

## CANON [Q]:持久上下文 + 受控压缩(看板是唯一记忆)

协调器上下文是**可消耗资源,不是记忆**:上下文跨 tick 持续存在,增长到 auto-compact 阈值时被
压缩成一段几千 token 的有损摘要(丢什么不可控)。协调器进程启动时用
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000` 把有效窗口钳制在 300K——长上下文的注意力退化与
成本都被该上限兜住,压缩在 tick 间隙自然发生即可,无需自我 `/clear`(历史设计曾用
arm-rebirth.sh 每 tick 自清,已废弃:copy-mode 等粘滞状态会让注入静默失效,且每 tick 失忆使
同类操作失误按 tick 周期复发)。

推论(每 tick 的纪律):

- **上下文里的历史仅供参考**:它可能已被压缩改写。每 tick 以看板读回对账开始,记忆与看板
  不一致一律以看板为准;跨 tick 状态**只**经 `board.py` 落盘,绝不依赖"我记得"。
- **教训要落 durable 层**:操作失误的修复必须落到脚本 / `tick-prompt.md` / 契约,只写进
  ticks.jsonl 会随读回窗口(`--last K`)滚出而复发,写进上下文会随压缩丢失。

机制事实(已实测,勿凭记忆推翻):

- **cron 任务挂在进程上,不挂在对话上**:`/loop <间隔> <prompt>` 建的是 session 级定时任务,
  跨压缩照常按点触发。
- **ultracode 是 per-prompt opt-in**:带 "ultracode" 关键词的 prompt 才可调 Workflow 工具,
  不依赖会话状态——心跳 payload 必须带关键词。
- **Bash 后台模式(`run_in_background`)起的长跑后台任务挂进程、不挂对话**,长跑
  存活;`nohup`/`&` 会被工具沙箱杀掉,不可用。

## 唯一硬约束:单 tick 内不灌大读

300K 窗口兜住的是**跨 tick 累积**;**tick 内**的巨读仍必须隔离:巨型 transcript / 大 diff
只进 review-panel Workflow / subagent 的上下文,随其消亡,协调器只收蒸馏裁决。协调器内联跑
构建/测试时一律 **no-slurp**:输出重定向到文件,只读 exit code,失败才 `tail -50`。

## 状态落盘:看板是唯一真相源

```
<project>/.curryflows/board/threads.jsonl    # 线程台账(状态机)
<project>/.curryflows/board/decisions.jsonl  # 人类决策队列
<project>/.curryflows/board/ticks.jsonl      # 每 tick 完整裁决(append-only,读回一律 list-ticks --last K)
<project>/.curryflows/board/backlog.jsonl    # 任务补给队列(CANON [M];含拒绝记忆)
<project>/.curryflows/tick-prompt.md         # 本项目实例化的 tick prompt(cron 指向它)
<project>/.curryflows/pause                  # 存在即人类接管:tick 自停
```

凡是"下个 tick 需要知道"的信息,**必须**在本 tick 内经 `board.py` 落盘——上下文随时可能被
压缩改写,不可依赖。写入只走 `scripts/board.py`(唯一写入者,原子 + fail-closed),绝不
手编/截断/`>` 任何 jsonl。

## 节拍与反应延迟

tick 间隔 = 反应延迟量子(worker 刚 idle,最坏等一个间隔才被审)。空转 tick 只是"读看板 →
无事 → 收工"(几 k tokens),因此**默认 5m、可收紧到 3m**;把间隔选在 p95 tick 时长之上,
tick 超限时下一发 cron 会排队,tick 之间天然串行——有界(多一轮的量),自愈。

## start:起协调器的一次性步骤

> **启动 fail-open(CANON [I],见 `decision-surface.md`)**:`/curryflows <自由任务>` 即视为启动
> 意图;可就第一刀 post 一个非阻断决策项,但人类无回答时默认就把心跳挂起来推进,绝不停在 inline。

1. **宿主 tmux 会话**(注入面要求;zellij/终端只是可选观看外壳,`tmux attach -t cfx-coord` 即看):

   ```bash
   tmux new-session -d -s cfx-coord -c <projectDir>
   # 交互式(不是 claude -p);显式钳制压缩窗口,不依赖宿主 shell 配置(CANON [Q])
   tmux send-keys -t cfx-coord 'CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000 claude' Enter
   ```

   协调器会话须已开 ultracode / 官方 Workflow(心跳 payload 里带 "ultracode" 关键词即可,
   见第 4 步——/clear 后每条 prompt 各自 opt-in,不依赖会话状态)。权限面预先配好
   (allowlist / acceptEdits),无人值守的 tick 不得撞权限弹窗。

2. **实例化 tick prompt**:把本文末尾的模板 copy 到 `<project>/.curryflows/tick-prompt.md`,
   填占位符(`{{PROJECT_DIR}}`、`{{SKILL_DIR}}`、`{{TMUX_SESSION}}`(在会话内
   `tmux display-message -p '#S'` 可得)、`{{WATERMARK}}` 默认 4、`{{INTERVAL}}`)。
   改 tick 行为 = 改这个文件,下个 tick 自动生效,无需重挂 cron。

3. **把看板表面的两条启动命令打印给用户**(CANON [R]:T0/T1 是用户自己的终端 / pane,协调器
   只打印、不代拉,见 `board.md`「终端表面」):

   ```bash
   # T0 常显摘要(zellij:layout 里固定一个两行高的小 pane;tmux:status-right)
   watch -n 15 python3 <skillDir>/scripts/board.py summary --board <project>/.curryflows/board
   # T1 看板 TUI(纯只读独立查看器;随时新开一个终端)
   ln -sf <skillDir>/scripts/board-tui.py ~/.local/bin/cfx-board   # 一次性
   cfx-board   # 任意目录:cwd 上溯找板,找不到列注册表挑选;也可 --board 显式指定
   ```

4. **挂心跳**(在协调器 TUI 里输入;payload 必含 "ultracode"):

   ```text
   /loop 5m ultracode:完整读取 ./.curryflows/tick-prompt.md,严格按其执行一个协调器 tick。
   ```

   cron 是 session 级、约 7 天过期——tick 步骤 0 负责自检重建,不靠人记。

## tick 七步(0–6):自检 → 读回 → 审核 → 决策 → 操作 → 补货 → 落盘

### 0) 自检与短路(廉价,先于一切)

- `<project>/.curryflows/pause` 存在 → 回一句 "paused",**本 tick 到此为止**。
- 活性:`list-ticks --last 1` 的时间戳间隔异常(> 3 个间隔)→ 摘要里如实报;
  cron 触发迹象消失 / 临近 7 天 → 用 CronList/CronCreate 重建心跳。
- 看板核对:`list-threads --open`、`list-decisions --open`、`list-backlog` 与上一 tick 记录
  对比,**无任何变化且无 worker 状态翻转、水位不欠账** → no-op tick:跳过 1–5,只回一句摘要,
  不 record-tick(ticks.jsonl 不记空转)。

### 1) rehydrate(全部有界读)

```bash
python3 <skillDir>/scripts/board.py list-threads   --board ./.curryflows/board --open
python3 <skillDir>/scripts/board.py list-decisions --board ./.curryflows/board --open
python3 <skillDir>/scripts/board.py list-backlog   --board ./.curryflows/board
python3 <skillDir>/scripts/board.py list-ticks     --board ./.curryflows/board --last 5
```

加上项目北极星文档(如 `plan.md`,若有)。**绝不整读 `ticks.jsonl`**(append-only,会涨到 MB)。
上下文里的历史仅供参考(可能已被压缩改写)——这些读回才是记忆,缺什么状态说明上一 tick 没
落盘,是缺陷,修落盘。

### 2) 审核(调官方 Workflow 跑 review-panel.js,只读)

只送需审线程(`idle`/`committed`,以及在途 `running` 的巡检)。**args 由单一事实源生成、
绝不手拼**(事故 wf_3a62dfb1:手拼成裸 id 字符串数组,面板烧 206K tokens 审字面量 undefined):

```bash
python3 <skillDir>/scripts/board.py panel-args --board ./.curryflows/board --threads <id1,id2>
```

把它输出的 JSON **作为真 JSON 对象**(勿 stringify)传给 Workflow:

```text
Workflow({ scriptPath: "<skillDir>/workflows/review-panel.js", args: <panel-args 的输出> })
→ { reviews: [{ thread, branch, worktree, verdict(pass|continue|escalate),
     findings, dissent, independence_tier(L1|L2|L3|n/a), escalate[], resources[] }], escalations: [...] }
```

语义不变(契约见 `reviewer-spec.md`):stage1 并发多 lens + 资源对账 + 跨模型硬规则([G]),
stage2 arbiter 收敛不投票;巨型 transcript 隔离在 Workflow 内。`state` 必传(repro lens 据此判
进度审 vs `committed→verified`);**只有 `independence_tier=L3` 才把 committed 线程置 verified**
(CANON [P])。裸 id / 缺字段会被面板 fail-fast 拒绝(`error: input-error`,不 spawn 任何
agent)——收到就重新 `panel-args`,**绝不 inline 手搓替代 review**(CANON [J])。

### 3) 决策(薄)

- `verdict=pass`/`continue` → 自动处理;`escalate` 及 `escalations[]` → `board.py post-decision`
  (status=open;同 id 重开用 `--reopen`,绝不重复 post)→ 再 `upsert-thread --state
  blocked-human`(board.py fail-closed 强制该顺序:无对应 open decision 的 blocked-human 会被
  拒——等人必须先让人看得见)+ 对该线程 codex 注入 Esc 软停(`interrupt-target.sh`,进程存活、
  goal 上下文完整,**绝不 reap**)。**escalate = arbiter 裁不动 → 归人类,绝不替换成协调器自己
  的 RULING;沉默不是同意**(CANON [N])。
- 落地人类已明确回复的决策项(**回复渠道 = 主 session 对话**:tick 摘要完整列出每个 open 项,
  人对摘要直接回一句即裁决,人不需要打开任何别的界面;board-tui 纯只读,CANON [R]):**若裁决
  实质修改了已封契约的约束(授权新面 / 放宽边界 / 改 gate 条款),必须先重封契约——amend 条款 +
  `validate-contract` 过绿——再放行 worker**;
  跳过重封,下轮审核必然裁"违反已封契约",同一件事要付两次人类决策(事故:fp16 ftz 授权后
  未重封,worker 771K token 交付被再次 blocked-human)。然后 `board.py resolve-decision`
  (resolution 指向真实人类动作)+ `inject-steer.sh` 把裁决注入**同一个** pane 续跑(线程回
  `running`,零重启)。
- runaway(`UNREGISTERED`/`RUNAWAY-SUSPECT`/孤儿 worktree)→ 软停 + post 决策项,本 tick
  不扩张 codex 占用。
- **绝不 `AskUserQuestion`**(CANON [K]):需人判项只入队 + 摘要完整列出该项,只 hold 该线程,其余照推。
- **重列与老化催办**:每个 open 决策项在**每 tick** 摘要里完整重列(id、问题、options、
  recommendation、evidence 路径,schema 见 `board.md`)直到关闭——主 session 就是决策面,人对
  摘要回一句即裁决;`list-decisions --open` 输出带 `age_hours`,超过 24h 的 open decision 必须在
  本 tick 摘要**置顶**(人不回就一直等,催办是协调器的职责)。

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
  sealed-ready 低 → **同步派一个 scoping subagent** 读仓库/plan 生成候选(重活隔离在它上下文;
  素材来源:北极星文档的未切前沿、已 merged 线程记录的挂账/后续项、panel findings 里标记的
  非阻断改进),候选依次过 `validate-contract` + `precondition-dryrun.sh` 两道门,过门即
  `upsert-backlog --status sealed-ready`,未熟则 `--status candidate|scoping`。
- **供给枯竭不许静默空转**:若 in-flight 低于水位、sealed-ready 空、且 scoping 也生成不出候选
  (北极星枯竭 / 方向不明),必须在本 tick 摘要**置顶**报"供给枯竭"并 `post-decision` 请人类
  给方向——连续多个 tick 记"backlog 空,不补"而不升级,等于把产线饿死藏在流水账里。
- **落盘交接是硬规则**:scoping 产物必须在**本 tick 内**写进 `backlog.jsonl` / contracts——
  跨 /clear 的会话内通知不可依赖,一律文件交接。
- **去重与拒绝记忆**由 `board.py` 强制:`dedup_key` 全队列唯一,新 id 撞键即拒(复提必须复用
  原条目、历史可见);`rejected` 必带 `reject_reason`。被否任务复活必须是有意识动作,不许每
  tick 重新冒出来。

### 6) 落盘 + 回摘要(最后一步)

- 状态变更 `upsert-thread`/`resolve-decision`/`upsert-backlog` 写回;本 tick 完整裁决备成
  数据文件后 `board.py record-tick --file <tick.json>`(no-op tick 不记;summary 声称"待用户"
  而决策面为空时 board.py 会告警——同 tick 内把决策 post 上去)。
- 向主 session 回**一条清晰、不糊弄的摘要**(schema 见 `board.md`:每线程状态/进展/预算、
  裁决含异议、未验证/风险、**每个 open 决策项完整列出**(id、问题、options、recommendation、
  evidence 路径;每 tick 重列直到关闭,`age_hours`>24 置顶催办)、回收清单、backlog 水位;
  完整裁决 / transcript 只给指针,evidence 只给路径不贴正文)。
- **教训沉淀**:本 tick 若有操作失误(错误派发、错误路径、误判),修复必须落 durable 层
  (改脚本 / 改 `tick-prompt.md` / amend 契约),只写进 tick 记录会随读回窗口滚出而复发。
  然后结束本 turn。

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
你是 curryflows 协调器。本条消息由 session cron 注入。你的上下文可能已被 auto-compact 压缩
改写:其中的历史仅供参考,唯一真相源是看板文件,记忆与看板不一致一律以看板为准(CANON [Q])。
配置:PROJECT_DIR={{PROJECT_DIR}};SKILL_DIR={{SKILL_DIR}};BOARD={{PROJECT_DIR}}/.curryflows/board;
TMUX_SESSION={{TMUX_SESSION}};并发水位={{WATERMARK}};tick 间隔={{INTERVAL}}。
输出语言:对用户的一切叙述/摘要一律中文,仅术语/命令/路径保留英文。

硬规则(全程有效):你绝不 Edit/Write 代码文件(源码/测试/脚本)也绝不自己调试——改码一律
worker / fixer subagent,你只指派(CANON [J])。绝不 AskUserQuestion;需人判项一律
board.py post-decision 入队 + 只停该线程,其余照推(CANON [K]/[N],沉默不是同意)。对 live
codex TUI 只用 inject-steer.sh / interrupt-target.sh,绝不手搓 send-keys(CANON [F])。起
codex 只经 tmux + 显式 -c model_reasoning_effort=xhigh,禁用 codex 插件/codex exec/companion
(CANON [H])。任何构建/测试输出重定向到文件只读 exit code,失败才 tail -50;巨型
transcript/diff 只进 Workflow/subagent,绝不进你的上下文。所有看板写入只走
{{SKILL_DIR}}/scripts/board.py(注意:board.py 在 SKILL_DIR 下,项目里没有副本),绝不手编/
截断 jsonl。凡下个 tick 需要知道的,本 tick 必须落盘——上下文会被压缩,不可依赖。

按序执行一个 tick(细则见 {{SKILL_DIR}}/references/coordinator.md,冲突以本文为准):
0) 自检:若 {{PROJECT_DIR}}/.curryflows/pause 存在 → 只回复 "paused" 并结束。
   心跳 cron 消失/临近 7 天过期则重建。看板对比上一 tick 记录零变化、
   无状态翻转、水位不欠账 → no-op:只回一句摘要,不 record-tick。
1) 读回(全部有界,命令逐字执行):
   python3 {{SKILL_DIR}}/scripts/board.py list-threads --board $BOARD --open
   python3 {{SKILL_DIR}}/scripts/board.py list-decisions --board $BOARD --open
   python3 {{SKILL_DIR}}/scripts/board.py list-backlog --board $BOARD
   python3 {{SKILL_DIR}}/scripts/board.py list-ticks --board $BOARD --last 3
   加 tmux ls 对账 cfx_* 真实态、git -C <各 worktree> log -1 看 HEAD 推进;绝不整读 ticks.jsonl。
2) 审核(只送 idle/goal-achieved/committed 及需巡检的 running 线程):args 一律由单一事实源
   生成,绝不手拼 threads[]:
   python3 {{SKILL_DIR}}/scripts/board.py panel-args --board $BOARD --threads <id1,id2>
   把其输出作为真 JSON 对象(勿 stringify)传
   Workflow({ scriptPath: "{{SKILL_DIR}}/workflows/review-panel.js", args: <上面的输出> });
   收到 error:input-error 就重新 panel-args;independence_tier=L3 才可置 verified(CANON [P]);
   绝不 inline 手搓替代 review(CANON [J])。
3) 决策:pass/continue 自动处理;escalate → post-decision(同 id 重开用 --reopen)→
   upsert-thread --state blocked-human(顺序强制:先有 open decision 才许 blocked-human)→
   interrupt-target.sh 软停(绝不 reap、绝不自裁 RULING)。落地人类已回复项(回复渠道 =
   主 session 对话:人对 tick 摘要直接回一句即裁决,board-tui 纯只读不承载裁决):裁决若实质
   修改已封契约约束,必须先重封(amend + validate-contract 过绿)再放行 worker,否则下轮必然
   契约冲突;然后 resolve-decision + inject-steer.sh 注回同一 pane 续跑。runaway → 软停 +
   入队,本 tick 不扩张。open decision 每 tick 在摘要里完整重列;age_hours>24 → 置顶催办。
4) 操作(你内联执行,逐线程,绝不成波):起 worker(两道 seal 门:validate-contract +
   派 subagent 跑 precondition-dryrun.sh)/ 驭 / commit / 串行合 main(rebase → L3 重验 →
   绿则 merge --no-ff 本地 main,勿 push,推远端=barrier)。冲突或回归:先 inject-steer.sh
   驭回该线程活着的 worker 修(会话保活到 merged),不行再派 fixer subagent,你自己绝不改码。
   merged/rolled-back 后才逐资源 reap.sh(session+worktree+branch);verified 不 reap 会话;
   blocked-human 绝不 reap。
5) 补货(常设,主动):数 in-flight 与 sealed-ready 对水位({{WATERMARK}});欠 launch 即补至
   水位;sealed-ready 低则本 tick 就派 scoping subagent 对照北极星/已 merged 线程挂账/panel
   非阻断 findings 生成候选 → 过两道 seal 门 → upsert-backlog(dedup-key 必填;rejected 必带
   reject-reason;产物本 tick 落盘)。供给枯竭(连候选都生成不出)→ 摘要置顶报 + post-decision
   请人类给方向,绝不连续静默空转。
6) 落盘+摘要:upsert-* 写回;非 no-op tick 把 {tick,summary,reviews,decisions_made,operator,
   risks_unverified} 备成 JSON 文件经 board.py record-tick 追加(如实,不绿洗);回一条不糊弄
   的中文摘要(每线程状态/进展/预算、裁决含异议、未验证项、每个 open 决策项完整列出——
   id/问题/options/recommendation/evidence 路径,每 tick 重列直到关闭、超 24h 置顶催办,人对
   摘要直接回复即裁决、回收清单、backlog 水位;完整裁决/transcript 只给指针,evidence 只给
   路径不贴正文)。本 tick 若有操作失误,修复落 durable 层(脚本/tick-prompt.md/契约),
   不能只写 tick 记录。然后结束本 turn。
```
