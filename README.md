# curryflows

把人类 review 从构建关键路径上解耦的**通用工作流协调器 skill**。

一个协调器 agent 在 review 待定时继续推进相互独立的工作,只在少数"必须确认"点阻塞;解耦期的正确性
由自动化门 + **跨模型 review(codex + Claude,分歧即信号)**守住,人类只处理真正的决策,且看的是
蒸馏后的决策面而非千行原文。

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
# 统一资源发现:所有在途 codex 会话 + 本项目的 curryflows worktree,与 board 对账,
# 标出未注册的 runaway。只读。
python3 scripts/discover-threads.py --project <项目repo> --board <项目>/.curryflows/board/threads.jsonl
```

退出码 0 = 干净;2 = 有在途资源(active codex 会话 或 curryflows worktree)未在 board 上注册
(curryflows 存在的直接动因:绝不漏掉 runaway)。

## 目录

- `SKILL.md` — 主文档 + 双 frontmatter。
- `references/` — 中文设计文档(架构 / 协调器 / 基座与门清单 / 三模板 / codex接入 / 决策面 /
  强目标契约 / goal-cookbook)。
- `scripts/` — 英文代码,被 agent 调用(`discover-threads.py` 统一发现;`inject-steer.sh` /
  `interrupt-target.sh` / `locate-codex.sh` codex 的 tmux 驱动器,吸收自 codex-goal-overseer)。
- `workflows/` — Claude Code Workflow 脚本(纯编排):三个独立模板 `feature-impl.js` /
  `perf-opt.js` / `test-gen.js`(门逻辑各自内联,无共享 base-kernel.js 文件)。
- `task-contracts/` — 给项目 copy 的任务契约骨架。

## 每个项目的运行态(不进本仓)

```
<project>/.curryflows/
  board/threads.jsonl       # 线程板(资源对账的依据)
  board/decisions.jsonl     # 人类决策队列
  temp/                     # 监督日志 / 证据
```
worktree 默认落 `~/.cache/curryflows/worktrees/<project>/<thread-id>`(可配)。
