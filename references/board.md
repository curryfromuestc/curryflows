# 综合看板 + 每 tick 摘要

看板是 curryflows 的单一真相源(source of truth),也是人类的异步视图。协调器会 park、会被压缩、
会跨多个唤醒周期存活,上下文不可靠;看板文件可靠。每个 tick 开始从看板读回状态,tick 末把变更写回。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`board.md` — 综合看板格式
> (threads/decisions/ticks/dashboard)+ 每 tick 摘要 schema。

## 文件布局(per-project,不进 skill 仓)

```
<project>/.curryflows/board/
  threads.jsonl     # 线程台账(discover-threads.py --board 对账对象)
  decisions.jsonl   # 人类决策队列
  ticks.jsonl       # 每 tick 完整裁决(durable 历史,主 session 摘要的后备)
  dashboard.html    # render-board.py 从上述 jsonl 渲染的 HTML 综合看板(人类异步视图)
<project>/.curryflows/contracts/
  <thread-id>.md    # 已封每线程契约(task-contracts/task.md 填好的副本,一线程一份)
```

与 `board/` 同级,已封每线程契约落在 `<project>/.curryflows/contracts/<thread-id>.md`——
即 `task-contracts/task.md` 填好后封存的副本,一线程一份;`threads.jsonl` 的 `contract` 字段指向它。
`seal-contract` 前置校验 = `board.py validate-contract --file <path>`(fail-closed:7 个必填字段齐且
非空——`outcome`、`verification`、`constraints`、`boundaries`、`iteration`、`budget`、`blocked_stop`)。

`dashboard.html` 是确定性渲染产物(自包含 HTML,浅色学术配色),不手写:

```bash
python3 <skillDir>/scripts/render-board.py --board ./.curryflows/board   # 写 dashboard.html
```

## 看板服务(serve-board):启动 / 访问 / 排错

`start` 协调器时顺带拉起 `serve-board.py`——只读、**每请求实时重渲染** + 页面自带自动刷新。按下面来,
**别再 `nohup`**。

### 启动:用 Bash 工具的后台模式,不要 nohup/setsid/&/sleep

```bash
python3 <skillDir>/scripts/serve-board.py --board ./.curryflows/board --port 8787 --host 0.0.0.0
```

- **经 Bash 工具的后台模式(`run_in_background: true`)运行这条命令**,协调器记下返回的后台任务 id。
  **绝不用 `nohup` / `setsid` / `&` / 前台 `sleep` 去拉起或等待**——本环境 sandbox 会杀掉它们(已观测
  `exit 144`,把含后台启动的整条命令一并杀死,进程根本起不来)。Bash 后台模式由 harness 托管、跨
  tick / park 存活、进程退出会回调通知协调器。
- serve-board 是 **session 级**后台进程(随协调器会话存活,跨 tick / park 不死);要它脱离会话长期常驻,
  得在 curryflows 之外手动起。

### host 绑定:决定谁能访问(runbook 默认 0.0.0.0,暴露局域网)

- 上面传 `--host 0.0.0.0`,这样**服务器 IP 直连**和**端口转发**都通——代价是只读看板**暴露到局域网**。
  介意局域网可见就去掉 `--host 0.0.0.0`(脚本默认 `127.0.0.1`,只本机 / 转发可达)。
- `--port` 默认 8787;传 `--port 0` 让它自动选空闲端口(从 stdout 读回实际 URL)。

### 访问(三选一)

1. **VSCode / Cursor 远程**:自动转发 8787 → 浏览器开 `http://127.0.0.1:8787/`(127.0.0.1 绑定即可)。
2. **纯 SSH**:`ssh -L 8787:localhost:8787 <user>@<server>` → 开 `http://127.0.0.1:8787/`(127.0.0.1 绑定即可)。
3. **服务器 IP 直连**:需 `--host 0.0.0.0` → 开 `http://<server-ip>:8787/`。

### 排错:本机 curl 回 503/502 ≠ 进程死了

很多环境本机挂了 HTTP 代理,`curl http://127.0.0.1:8787/` 会被代理拦成 **503 / 502**,这是**假象**,
不代表 serve-board 死了。真实性核验要**绕过代理**:

```bash
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/   # 200 = 正常
ss -ltnp | grep 8787                                                              # 确认在监听
```

浏览器同理打不开:把 `127.0.0.1` / `<server-ip>` 加进 `no_proxy` 白名单。

## board 写入:用 board.py,不手编 JSONL

`scripts/board.py` 是看板 JSONL 的**唯一写入者**。绝不手编 `threads.jsonl` / `decisions.jsonl`——
手编易写坏行,而 `render-board.py` 对坏行**静默跳过**,会无声丢状态(看板与真相脱节,且无报错)。
所有写操作原子(同目录 temp 文件 + `os.replace`),非法枚举 / 缺必填一律 fail-closed 拒写。

**更绝不 `>` / `truncate` / `rm` / `: >` 看板 jsonl**——要重置一个看板,先**归档**(`render-board.py` 落
一份 HTML 快照)再用 board.py 重建;**任何破坏性操作前先读内容**。已观测事故:协调器未看内容就用 `: >`
清空 `threads/decisions/ticks.jsonl`,毁掉了上一轮已完成 + 全合并 run 的 durable 看板历史。

`board.py` CLI(子命令在前,`--board <dir>`=看板目录跟在子命令后;`validate-contract` 例外,只用 `--file` 不带 `--board`):

- `upsert-thread --id <tid> [--state S] [--branch B] [--worktree W] [--tmux-session T] [--codex-session C] [--budget-tokens N] [--budget-spent N] [--contract PATH] [--last-verdict V] [--attempt N]`
  ——按 `thread_id` 合并所给字段(无则新建),置 `updated=now`,校验 `state` 属枚举,原子重写。
- `post-decision --id <did> --barrier B --thread T --summary S --recommendation R --evidence PATH [--divergence D] [--options "a|b|c"]`
  ——追加决策(`status=open`,`resolution=null`);校验 `barrier` ∈ `{seal-contract, merge-main, outward-irreversible, model-divergence}`;`recommendation` 与 `evidence` 必须非空。
- `resolve-decision --id <did> --resolution TEXT [--status resolved|rejected]`——按 id 更新决策。
- `record-tick --board <dir> --file <tick.json>`——把一条 tick 记录 append 到 `ticks.jsonl`(durable 历史)。`tick.json` 是协调器备好的**数据文件**(`{tick:int, summary:str, reviews?, decisions_made?, operator?, ts?}`;`tick`/`summary` 必填非空,fail-closed);board.py 仍是唯一写入者、原子 append。**这是写 `ticks.jsonl` 的唯一正道**(绝不手 append / `>`)。
- `list-threads` / `list-decisions [--open]`——只读 JSONL dump(协调器廉价读回状态)。
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
`ready -> running -> idle -> reviewed -> committed -> verified -> session-reaped -> merged | rolled-back`;
另加 `blocked-human`(升人类,可从任意状态进入)。含义:

- `ready`=契约已封未启动;`running`=codex `/goal` worker 在跑;
- `idle`=worker 到 budget / blocked-stop / 自认完成,待审;`reviewed`=reviewer 审完,`last_verdict` 已记;
- `committed`=工作已 commit 到自己分支(durability,非 merge 非 push);
- `verified`=在 committed 分支 worktree 上独立复跑通过;
- `session-reaped`=codex tmux 会话已 reap 释放进程,分支 + worktree 保留待人类 merge;
- `merged`=合 main(终态);`rolled-back`=丢弃(终态)。

分阶段 reap:`verified` 时只 reap 会话(`reap.sh --session ...`)置 `session-reaped`,保留 worktree + 分支;
`merged` / `rolled-back` 才 reap worktree + 分支(`reap.sh --worktree ...` 与 `--branch ...`)。

`attempt`(可选整型,默认 1)=relaunch 次数:人类决策扩展某线程后复用现有 worktree + 分支(不重建),
起全新 codex 会话注入更新后的已封契约,协调器把 `state` 置回 `running`、`attempt` 加一、`codex_session` 更新为新 rollout id。

`codex_session`、`branch` 被 `discover-threads.py` 用于对账。`budget_*` 让 reviewer / 协调器算出
预算余额(摘要必填项)。

## decisions.jsonl(每行一个决策项)

人类决策队列。完整字段语义 + 示例见 `decision-surface.md`。状态在 `open` / `resolved` / `rejected`
之间流转;`barrier` ∈ `seal-contract` | `merge-main` | `outward-irreversible` | `model-divergence`;
`evidence` 必须指向 checked-in artifact 路径(非 prose);`recommendation` 必填且引用契约 / 权威依据。

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
