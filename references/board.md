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

`start` 协调器时顺带后台拉起 `serve-board.py`,在本地端口 serve 看板——**每次请求实时重渲染** +
页面自带自动刷新,人类浏览器里看实时状态(SSH 机器端口转发即可):

```bash
python3 <skillDir>/scripts/serve-board.py --board ./.curryflows/board --port 8787 &
# → http://127.0.0.1:8787/
```

## board 写入:用 board.py,不手编 JSONL

`scripts/board.py` 是看板 JSONL 的**唯一写入者**。绝不手编 `threads.jsonl` / `decisions.jsonl`——
手编易写坏行,而 `render-board.py` 对坏行**静默跳过**,会无声丢状态(看板与真相脱节,且无报错)。
所有写操作原子(同目录 temp 文件 + `os.replace`),非法枚举 / 缺必填一律 fail-closed 拒写。

`board.py` CLI(子命令在前,`--board <dir>`=看板目录跟在子命令后;`validate-contract` 例外,只用 `--file` 不带 `--board`):

- `upsert-thread --id <tid> [--state S] [--branch B] [--worktree W] [--tmux-session T] [--codex-session C] [--budget-tokens N] [--budget-spent N] [--contract PATH] [--last-verdict V] [--attempt N]`
  ——按 `thread_id` 合并所给字段(无则新建),置 `updated=now`,校验 `state` 属枚举,原子重写。
- `post-decision --id <did> --barrier B --thread T --summary S --recommendation R --evidence PATH [--divergence D] [--options "a|b|c"]`
  ——追加决策(`status=open`,`resolution=null`);校验 `barrier` ∈ `{seal-contract, merge-main, outward-irreversible, model-divergence}`;`recommendation` 与 `evidence` 必须非空。
- `resolve-decision --id <did> --resolution TEXT [--status resolved|rejected]`——按 id 更新决策。
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
