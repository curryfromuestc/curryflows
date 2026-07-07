# 综合看板 + 每 tick 摘要

看板是 curryflows 的单一真相源(source of truth),也是人类的异步视图。协调器的上下文可被
auto-compact 有损重写(CANON [Q]),跨 tick 状态**只**信看板文件。每个 tick 开始从看板读回
状态对账(有界读),tick 末把变更写回——凡是"下个 tick 需要知道"的,本 tick 必须落盘。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`board.md` — 综合看板格式
> (threads/decisions/ticks/backlog)+ 终端表面(CANON [R])+ 每 tick 摘要 schema。

## 文件布局(per-project,不进 skill 仓)

```
<project>/.curryflows/board/
  threads.jsonl     # 线程台账(discover-threads.py --board 对账对象)
  decisions.jsonl   # 人类决策队列
  ticks.jsonl       # 每 tick 完整裁决(durable 历史;读回一律 list-ticks --last K,绝不整读)
  backlog.jsonl     # 任务补给队列(CANON [M];dedup + 拒绝记忆)
<project>/.curryflows/contracts/
  <thread-id>.md    # 已封每线程契约(task-contracts/task.md 填好的副本,一线程一份)
<project>/.curryflows/
  tick-prompt.md    # 本项目实例化的 tick prompt(cron 心跳指向它,模板见 coordinator.md)
  pause             # 存在即人类接管:tick 自停(删除即恢复)
```

与 `board/` 同级,已封每线程契约落在 `<project>/.curryflows/contracts/<thread-id>.md`——
即 `task-contracts/task.md` 填好后封存的副本,一线程一份;`threads.jsonl` 的 `contract` 字段指向它。
`seal-contract` 前置校验 = `board.py validate-contract --file <path>`(fail-closed:8 个必填字段齐且
非空——`outcome`、`verification`、`constraints`、`boundaries`、`iteration`、`budget`、`blocked_stop`、
`preconditions`)+ environment-precondition dry-run(`precondition-dryrun.sh`,CANON [O])。

## 终端表面(CANON [R]):T0 常显摘要 + T1 看板 TUI

人类表面是终端原生的两层,jsonl 真相源不变:**T0** = `board.py summary` 一行摘要,用户自己
`watch` 在一个小 pane 里常显;**T1** = `board-tui.py` 全屏 curses 看板,按需在浮动 pane / popup
里开、看完即关。

```bash
# T0 常显摘要(zellij:layout 里固定一个两行高的小 pane;tmux:status-right)
watch -n 15 python3 <skillDir>/scripts/board.py summary --board <project>/.curryflows/board
# T1 看板 TUI(zellij 浮动 pane / tmux popup)
zellij run --floating --width 90% --height 85% -- \
  python3 <skillDir>/scripts/board-tui.py --board <project>/.curryflows/board
tmux display-popup -w 90% -h 85% -E \
  "python3 <skillDir>/scripts/board-tui.py --board <project>/.curryflows/board"
```

T0/T1 都由**用户**在自己的 pane 里拉起(T1 的 zellij / tmux 两行二选一)——协调器 `start` 时只把
这些命令打印给用户,不代拉。
`board-tui.py` 另有 `--render threads|decisions|backlog|ticks` 无头模式:不起 curses、向 stdout
打一帧该视图的纯文本(exit 0;jsonl 坏行 → stderr + exit 1;看板目录不存在按空看板渲染表头),
供无 TTY 的测试 / agent 读看板;不带 `--render` 且 stdout 非 TTY 时 fail-fast(exit 64,提示改用
`--render`)。

### CANON [R]:TUI 的写路径纪律(权威定义)

board-tui 是 durable 看板的**只读渲染器 + 决策输入面**。它写进系统的路径只有两条:
① `resolve-decision`——经子进程调 `board.py` CLI,TUI 自己绝不碰 jsonl;② pause 文件的创建 / 删除
(纯 flag 文件)。此外人类保有既有的 Esc 急停权,但那是 attach 到 worker pane 亲手按的,不经 TUI
实现。TUI **绝不执行生命周期操作**(起 / 驭 / commit / 合 main / 回收)——生命周期写入者只有协调器
一个;关闭 TUI 对推进零影响。

### board-tui 按键

全局:`1`/`2`/`3`/`4` 切视图(Threads/Decisions/Backlog/Ticks),`j`/`k` 与方向键移动选中,
`g`/`G` 跳首 / 末行,`r` 强制重读,`R` 跑资源发现(见下「刷新模型」),`P` 开关 pause 文件,
`?` 帮助浮层(列全部按键),`q` 退出(`Q` 同义)。所有视图的头部显示看板路径、与 `summary` 同字形的计数、
PAUSED 指示;底部一行是动作结果 / 错误的瞬态状态行。

**[1] Threads**——表列:thread_id、state、attempt、budget(已花/总量 + 百分比,字段缺失留空)、
last_verdict、`updated` 距今(5m/3h/2d);选中行详情框:branch、worktree、tmux_session、
codex_session、contract 路径。

- `Enter` attach 该线程的 tmux 会话(退出 attach 即回 TUI;无 tmux_session 或 attach 失败报状态行);
- `p` peek:`tmux capture-pane` 抓该会话最近 200 行,进内置可滚动 pager(`j`/`k`/PgUp/PgDn/`q`);
- `d` 分支 diff:`git -C <worktree> diff <mainbase>...HEAD`,有 `delta` 走 `delta`,否则 `less -R`
  (设了 `$PAGER` 则尊重);
- `u` 未提交 diff:`git -C <worktree> diff HEAD`,同一管道;
- `c` 在 pager 里看契约文件(文件缺失报状态行)。

**[2] Decisions**——默认只显 open,`o` 切换显示全部;表列:id、barrier、thread、age(按
reopened 或 created 计)、summary 截断;详情框:recommendation、options(编号)、evidence 路径、
divergence、status/resolution。

- `v` 在内置 pager 里打开 evidence(相对路径按项目根解析);
- `Enter` resolve:底部单行输入(backspace 编辑、ESC 取消);该决策带 options 时输入编号 1..N 即
  选中该项文本,否则自由文本;提交即子进程 `board.py resolve-decision --status resolved`,
  board.py 的 stdout/stderr 回显在状态行,成功后重读决策;
- `x` reject:同一输入流程,理由**必填**(拒收空文本),`--status rejected`。

仅 open 决策可 resolve / reject;对非 open 行按键只报状态行错误。

**[3] Backlog**——只读;表列:backlog_id、status、dedup_key、summary;详情框:rationale、
contract、thread、reject_reason。`Enter`/`v` 在 pager 里看完整记录。

**[4] Ticks**——只读,最近 50 条、新在前(读回同 `list-ticks` 的有界 tail,绝不整读);表列:
tick、ts、summary 截断。`Enter` 在 pager 里看完整记录(JSON 缩进展开)。

### 刷新模型:被动只 stat mtime,主动发现只在 `R`

- **被动刷新**:每秒(getch 超时 1000ms)只 `stat` 四个 jsonl + pause 文件的 mtime,谁变了才重读
  谁——常态开销是每秒几次 stat,零读盘。
- **主动发现**:只有按 `R` 才跑 `discover-threads.py` 对账,stderr 摘要 + 被标记记录进 pager,
  结论进状态行(`discover: clean (exit 0)` / `discover: N untracked (exit 2)`)。被动路径**绝不**
  跑发现——协调器 tick 每轮都跑权威对账,`R` 只是人类"现在就要看"的插队,不该变成常驻开销。
- **坏行浮出**:curses 模式读回遇 JSONL 坏行,保留上一份好数据,顶部持续横幅报出文件:行号,直到
  重读成功才消——暴露损坏,绝不掩盖。

## board 写入:用 board.py,不手编 JSONL

`scripts/board.py` 是看板 JSONL 的**唯一写入者**。绝不手编 `threads.jsonl` / `decisions.jsonl`——
手编易写坏行,而所有读回都是严格 fail-closed 的:一行坏,整个读回失败(`summary` exit 1、TUI 挂
损坏横幅),看板直接不可用。所有写操作原子(同目录 temp 文件 + `os.replace`),非法枚举 / 缺必填
一律 fail-closed 拒写。

**更绝不 `>` / `truncate` / `rm` / `: >` 看板 jsonl**——要重置一个看板,先**归档**(`cp -a` 整个
board 目录;jsonl 本身就是 durable 历史)再用 board.py 重建;**任何破坏性操作前先读内容**。已观测
事故:协调器未看内容就用 `: >` 清空 `threads/decisions/ticks.jsonl`,毁掉了上一轮已完成 + 全合并
run 的 durable 看板历史。

`board.py` CLI(子命令在前,`--board <dir>`=看板目录跟在子命令后;`validate-contract` 例外,只用 `--file` 不带 `--board`):

- `upsert-thread --id <tid> [--state S] [--branch B] [--worktree W] [--tmux-session T] [--codex-session C] [--budget-tokens N] [--budget-spent N] [--contract PATH] [--last-verdict V] [--attempt N]`
  ——按 `thread_id` 合并所给字段(无则新建),置 `updated=now`,校验 `state` 属枚举,原子重写。
- `post-decision --id <did> --barrier B --thread T --summary S --recommendation R --evidence PATH [--divergence D] [--options "a|b|c"]`
  ——追加决策(`status=open`,`resolution=null`);校验 `barrier` ∈ `{seal-contract, merge-main, outward-irreversible, model-divergence}`;`recommendation` 与 `evidence` 必须非空。
- `resolve-decision --id <did> --resolution TEXT [--status resolved|rejected]`——按 id 更新决策。
- `record-tick --board <dir> --file <tick.json>`——把一条 tick 记录 append 到 `ticks.jsonl`(durable 历史)。`tick.json` 是协调器备好的**数据文件**(`{tick:int, summary:str, reviews?, decisions_made?, operator?, ts?}`;`tick`/`summary` 必填非空,fail-closed);board.py 仍是唯一写入者、原子 append。**这是写 `ticks.jsonl` 的唯一正道**(绝不手 append / `>`)。
- `upsert-backlog --id <bid> [--summary S] [--status candidate|scoping|sealed-ready|launched|rejected] [--dedup-key K] [--rationale R] [--contract PATH] [--thread T] [--reject-reason R]`
  ——按 `backlog_id` 合并(无则新建,新建必须给 `--summary`);两道 fail-closed 门:新条目的
  `dedup_key` 撞已有条目即拒(复提必须复用原条目 id,历史可见);`status=rejected` 必带
  `--reject-reason`(拒绝记忆必须说得出为什么)。
- `list-threads` / `list-decisions [--open]` / `list-backlog [--status S]`——只读 JSONL dump
  (协调器廉价读回状态)。
- `list-ticks [--last N]`——**有界**读 tick 历史(只解析并校验返回窗口内的行);tick 的 rehydrate
  一律用它,绝不整读 `ticks.jsonl`。
- `summary`——向 stdout 打**恰好一行**状态摘要:
  `cfx ▶<running> ⏸<blocked-human> ⚑<open 决策> ◆<sealed-ready>[ | <state>:<n> ...][ | PAUSED]`。
  首段恒在(含零,`watch` pane 形状稳定);第二段只列其余**非终态**线程状态的非零计数
  (ready/idle/reviewed/committed/verified/session-reaped,按枚举序;全零则整段省略;终态
  merged/rolled-back 不显示);pause 文件存在时缀 ` | PAUSED`。只读,给 `watch -n 15` /
  tmux status-right 常显用;jsonl 坏行 → stderr 报错 + exit 1。
- `validate-contract --file PATH`——fail-closed seal 前置(见上「文件布局」);有效 exit 0,否则非零并打印缺失字段列表。

## threads.jsonl(每行一个线程)

```json
{"thread_id": "feat-rate-limit",
 "state": "ready|running|idle|reviewed|committed|verified|session-reaped|merged|rolled-back|blocked-human",
 "branch": "curryflows/feat-rate-limit",
 "worktree": "/home/u/.cache/curryflows/worktrees/proj/feat-rate-limit",
 "tmux_session": "cfx_feat-rate-limit",
 "codex_session": "<rollout uuid|null>",
 "budget_tokens": 4000000, "budget_spent": 1200000,
 "contract": "<project>/.curryflows/contracts/feat-rate-limit.md",
 "attempt": 1,
 "last_verdict": "pass|continue|escalate|null", "updated": "<ts>"}
```

`state` 权威枚举(全仓统一):
`ready -> running -> idle -> reviewed -> committed -> verified -> merged | rolled-back`;
另加 `blocked-human`(升人类,可从任意状态进入)与 `session-reaped`(枚举保留、常规流不再经过,
见 CANON [B] 修订)。含义:

- `ready`=契约已封未启动;`running`=codex `/goal` worker 在跑;
- `idle`=worker 到 budget / blocked-stop / 自认完成,待审;`reviewed`=reviewer 审完,`last_verdict` 已记;
- `committed`=工作已 commit 到自己分支(durability,非 merge 非 push);
- `verified`=在 committed 分支 worktree 上独立复跑通过(**会话保留**,合并冲突才有活 worker 可驭回);
- `session-reaped`=codex tmux 会话被提前 reap(罕见;常规流会话活到 merged);
- `merged`=合 main(终态);`rolled-back`=丢弃(终态)。

终态一并回收(CANON [B] 修订):`merged` / `rolled-back` 后把会话、worktree、分支一并 reap
(`reap.sh --session` / `--worktree` / `--branch`,逐资源调用);`blocked-human` 绝不 reap。

`attempt`(可选整型,默认 1)=relaunch 次数:人类决策扩展某线程后复用现有 worktree + 分支(不重建),
起全新 codex 会话注入更新后的已封契约,协调器把 `state` 置回 `running`、`attempt` 加一、`codex_session` 更新为新 rollout id。

`codex_session`、`branch` 被 `discover-threads.py` 用于对账。`budget_*` 让 reviewer / 协调器算出
预算余额(摘要必填项)。

## decisions.jsonl(每行一个决策项)

人类决策队列。完整字段语义 + 示例见 `decision-surface.md`。状态在 `open` / `resolved` / `rejected`
之间流转;`barrier` ∈ `seal-contract` | `merge-main` | `outward-irreversible` | `model-divergence`;
`evidence` 必须指向 checked-in artifact 路径(非 prose);`recommendation` 必填且引用契约 / 权威依据。

## backlog.jsonl(每行一个候选任务,补给队列)

tick 常设补货步(CANON [M])的落点:scoping 产出的候选任务、已过 seal 门待起的任务、以及被否
任务的**拒绝记忆**都在这里,跨 tick 存活。

```json
{"backlog_id": "b-rate-limit-burst",
 "summary": "define burst semantics for the rate limiter",
 "status": "candidate|scoping|sealed-ready|launched|rejected",
 "dedup_key": "rate-limit-burst",
 "rationale": "north-star §3; blocked twice on undefined burst behavior",
 "contract": "<project>/.curryflows/contracts/b-rate-limit-burst.md",
 "thread": "<launched 后指向 thread_id|null>",
 "reject_reason": "<status=rejected 时必填>", "updated": "<ts>"}
```

生命周期 `candidate → scoping → sealed-ready → launched`(或任意点 `rejected`)。两条 fail-closed
纪律由 `board.py` 强制:`dedup_key` 全队列唯一——被否任务不能换个 id 每 tick 重新冒出来,复提必须
复用原条目、历史可见;`rejected` 必带 `reject_reason`。`sealed-ready` 的条目必须已过两道 seal 门
(`validate-contract` + `precondition-dryrun.sh`,CANON [O]),协调器 launch 时只认 `sealed-ready`。

## ticks.jsonl(每行一个 tick 的完整裁决)

主 session 摘要的 durable 后备——摘要只给指针,完整内容在这里,人类要深究时翻它:

```json
{"tick": 42, "ts": "<ts>",
 "reviews": [ {reviewer 裁决对象, 见 reviewer-spec.md} ],
 "decisions_made": [ "..." ],
 "operator": {launched, steered, reaped, failures},
 "summary": "<本 tick 回给主 session 的那条摘要原文>"}
```

**写入经 `board.py record-tick --file <tick.json>`(唯一正道,fail-closed:`tick` 为 int + `summary` 非空)**:
协调器每 tick 末备好这份 JSON 数据文件(写数据文件不违反 CANON [J]),再调 board.py 原子 append;**绝不手
append / `>` `ticks.jsonl`**(否则 tick 历史要么写坏、要么因"禁手编"而永远空)。

## 每 tick 摘要 schema(回主 session 的那条)

回主 session 的摘要必须紧凑,但**禁止绿洗**。它是一段结构化文本 / 对象,缺以下任一项不算合格:

| 段 | 内容 | 硬规则 |
|---|---|---|
| `threads` | 每条在跑线程:状态 / 本 tick 实质进展 / 预算余额 | 进展要具体,不能是"在推进中" |
| `verdicts` | 审核裁决,**含异议**(哪个 reviewer 报了什么、是否有跨模型分歧) | 不许只报"通过";有 dissent 必列 |
| `unverified` | 未验证项 / 风险 / 越界 | **强制如实暴露**,无则显式写"无" |
| `decisions` | 待人类决策项(若有),指向 decisions.jsonl 的 id | 指针,不复述全文 |
| `reaped` | 本 tick 回收了哪些资源 | 列出 session / worktree / branch |
| `pointers` | 完整裁决 / transcript 的路径 | 只给路径,正文不进主 session |

一条只写"本 tick 一切正常、继续"的摘要视为不合格——它隐瞒了未验证项与异议,违反"清晰、不糊弄"。
