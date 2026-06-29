# curryflows

把人类 review 从构建关键路径上解耦的**通用工作流协调器 skill**。

一个 `/loop` 协调器以"审核优先"并发推进多个在 tmux 里长跑的 codex `/goal` worker:每个 tick 先并发
派多个强力(opus)reviewer subagent 审产物 + 对账资源,协调器据裁决决策,再派一个 operator subagent
去操作 tmux(起 / 驭 / 回收 codex)。worker 是 codex、reviewer 是 Claude,**天然跨模型**;裁决只回
一条清晰摘要给主 session,完整证据落 durable 看板,**人类异步看、异步决策,默认不阻断推进**——只在
合 main、对外不可逆、跨模型真分歧三种 barrier 才升人类。

唯一硬约束:**协调器(主 session)上下文绝不被巨型 transcript / diff 撑爆**——重活全在 subagent 里
完成,subagent 的大上下文随它消亡,协调器只收蒸馏结论。

完整设计见 [`SKILL.md`](SKILL.md) 与 `references/`。

## 安装

本仓是一个 Claude Code skill。安装方式 = `git clone` 进 skills 目录:

```sh
git clone <repo-url> ~/.claude/skills/curryflows
```

依赖(见 `SKILL.md` frontmatter `requires`):codex CLI(≥0.128,支持 `/goal`)、tmux、
git(≥2.5,worktree)、python3。

## 现在可跑

```sh
# 统一资源发现:所有在途 codex 会话 + 本项目的 curryflows worktree,与看板对账,
# 标出未注册的 runaway。只读。退出码 0=干净;2=有在途资源未在看板登记;64=用法错。
python3 scripts/discover-threads.py --project <项目repo> \
  --board <项目>/.curryflows/board/threads.jsonl

# 把看板 jsonl 渲染成自包含 HTML 看板(浅色学术配色,浏览器直接开)。
python3 scripts/render-board.py --board <项目>/.curryflows/board   # → dashboard.html

# 起本地端口 serve 看板:每次请求实时重渲染 + 页面自动刷新(start 协调器时顺带后台拉起)。
python3 scripts/serve-board.py --board <项目>/.curryflows/board --port 8787
# → http://127.0.0.1:8787/  (SSH 机器端口转发即可在浏览器看实时状态)

# 回收用完的资源(operator 每 tick 的硬职责):tmux 会话 + worktree + curryflows 分支,带安全护栏。
bash scripts/reap.sh --session <tmux-session> --worktree <path> --project <项目repo> --branch <name>
```

## 三层控制流

1. **协调器(`/loop` 动态模式)= 外层调度**:极薄,只做推理、决策、派发、写看板;自己不读大文件、
   不跑脚本。无就绪事项时 park,被事件唤醒。
2. **subagent 派发 = 内层有界动作**:每 tick 先并发派多个 reviewer subagent(opus,只读)审产物 +
   对账资源,协调器决策后再派一个 operator subagent(opus,可改)操作 tmux/codex。所有 subagent 一律 opus。
3. **codex `/goal` = 自驱 worker**:真正干活的长跑线程,在 detached tmux 里跑,由强目标契约
   (budget + blocked-stop)+ 只读审计 + Esc 急停兜住。

## 目录

- `SKILL.md` — 主文档 + 双 frontmatter。
- `references/` — 中文设计文档(架构 / 协调器 tick runbook / reviewer 契约 / operator 契约 /
  综合看板 + 摘要 schema / codex 接入 / 强目标契约 / 决策面 / goal-cookbook)。
- `scripts/` — 英文代码,被 agent 调用:`discover-threads.py`(统一资源发现)、
  `render-board.py`(jsonl → HTML 看板)、`serve-board.py`(本地端口 serve 实时看板)、
  `reap.sh`(资源回收)、`inject-steer.sh` / `interrupt-target.sh` / `locate-codex.sh`
  (codex 的 tmux 驱动器)、`codex-review.sh`(可选的 codex 第二意见腿)。
- `task-contracts/` — `task.md` 通用任务契约骨架(给项目 copy 填写,经 seal-contract 人封)。

## 每个项目的运行态(不进本仓)

```
<project>/.curryflows/
  board/threads.jsonl       # 线程台账(资源对账的依据)
  board/decisions.jsonl     # 人类决策队列
  board/ticks.jsonl         # 每 tick 完整裁决(durable 历史)
  board/dashboard.html      # render-board.py 渲染的 HTML 综合看板
  temp/                     # 监督日志 / 证据
```
worktree 默认落 `~/.cache/curryflows/worktrees/<project>/<thread-id>`(可配)。
