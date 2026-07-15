# curryflows

[herdr](https://herdr.dev) + codex + Claude Code `/loop` 的极简并发开发协调器:把活派给
长跑的 codex worker,心跳巡检防跑偏,人类只在真需要时被打扰。

## 它怎么工作

- 协调器(Claude Code 会话)按任务开线程:每线程一个独立 git 分支 + worktree + herdr
  workspace,codex 在里面长跑(`model_reasoning_effort=max`)。
- `/loop` 心跳每几分钟触发一个 tick:协调器自主巡检(`herdr agent list`、`pane read`、
  git diff,看什么自己定),跑偏就驭,干完就自审 → 合本地 main → 回收;推送远端永远归人类。
- 需要人类裁决时:workspace 改名标记 + herdr 弹通知;其余线程照推,不等人。
- **herdr 就是看板**:workspace 列表一眼看全,点进去就是活的 codex TUI。

```
herdr workspaces
  ── 你自己的 ────────────────────────
  w1  myproject       [claude ● idle]
  ...
  ── curryflows 的 ───────────────────
  w7  cfx 限流重构     [codex ▶ working]
  w8  cfx 文档迁移     [codex ⏸ ❓等你]
```

## 安装

```bash
git clone <repo-url> ~/.claude/skills/curryflows
```

依赖:[herdr](https://herdr.dev)、codex CLI、git ≥2.5(worktree)、Claude Code。

## 用法

```text
/curryflows 把 parser 模块迁移到新 API,顺便查一下慢测试
```

## 设计取向

没有看板文件、没有辅助脚本、没有评审面板——状态活在 herdr 里,判断活在协调器里,唯一的
持久物是 git 分支上的产物。全部约定见 [SKILL.md](SKILL.md),200 行读完。
