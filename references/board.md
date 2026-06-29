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
```

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

## threads.jsonl(每行一个线程)

```json
{"thread_id": "feat-rate-limit",
 "state": "ready|running|build-done|blocked-human|merged|rolled-back",
 "branch": "curryflows/feat-rate-limit",
 "worktree": "/home/u/.cache/curryflows/worktrees/proj/feat-rate-limit",
 "tmux_session": "cfx_feat-rate-limit",
 "codex_session": "<rollout uuid|null>",
 "budget_tokens": 4000000, "budget_spent": 1200000,
 "overseer": "attached|null", "contract": "task-contracts/task.md@<sealed-ref>",
 "last_verdict": "pass|continue|escalate|null", "updated": "<ts>"}
```

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
