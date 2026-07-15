---
name: curryflows
description: >-
  极简并发开发协调器:把任务派给在 herdr workspace 里长跑的 codex worker,用 /loop 心跳
  定期巡检防跑偏。协调器每 tick 自主决策(看什么、驭什么、何时合),验证通过自动合本地
  main,只在自己裁不动或对外不可逆时通知人类。触发于:"起 curryflows"、"用 curryflows
  跑并发开发"、"派个 codex 长跑任务"、"监督 codex 别跑飞"。
user-invocable: true
argument-hint: "[要推进的任务,自由描述]"
type: skill
requires:
  - herdr
  - codex CLI
  - git (>=2.5, worktree)
---

# curryflows

把任务派给长跑 codex worker、`/loop` 心跳巡检防跑偏的极简协调器。没有看板文件、没有辅助
脚本、没有评审面板:**herdr 就是看板**(workspace 列表 + 状态灯 + label),**协调器就是
评审员**,一切判断由协调器每个 tick 现场做出。本 skill 运行期间对用户的一切叙述用中文。

## 铁律
- **协调器不亲手改代码**:改码一律派给 codex worker;协调器只读、只驭、只合并，一些小的代码修改可以自己处理，用户有时可能会要求启动一个workfloww或者subagent，这种时候听用户的。
- **每个 worker 独立分支 + worktree**,全程不碰 main;协调器验证通过后合并**本地** main;
  **推送远端永远由人类执行**。
- **用户自己的 workspace 永不触碰**:协调器只操作自己创建的 `cfx` 前缀 workspace(新建的
  天然排在用户的后面)。
## herdr 操作手册
herdr 是有 agent 状态感知的终端复用器,CLI 即完整控制面(`herdr <子命令> --help` 可查)。
**起线程**(一条命令 = 分支 + worktree + 带名 workspace):
```bash
herdr worktree create --cwd <项目> --branch cfx/<任务名> --label "cfx <任务名>" --no-focus --json
# worktree 默认落 ~/.herdr/worktrees/<仓名>/<分支名>;返回 JSON 含 workspace_id 与 worktree 路径
herdr agent start cfx-<任务名> --cwd <worktree路径> --workspace <wid> --no-focus -- \
  codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=max
# 返回 JSON 含 pane_id;YOLO mode 下无信任目录菜单,直接就绪
herdr wait agent-status <pane_id> --status idle --timeout 90000    # 等 TUI 就绪
```
**注入任务**:
```bash
herdr pane read <pane_id> --lines 8   # 先看输入框有无残留:Esc 打断会把旧消息还原回
                                      # composer,直接 send 会把新旧文本拼接成一条
herdr agent send cfx-<任务名> "<提示词>"    # 中文 / 多行 / 引号安全落地,可 pane read 读回核验
herdr pane send-keys <pane_id> enter
herdr wait agent-status <pane_id> --status working --timeout 15000  # 确认提交生效
```
**巡检 / 细看**:
```bash
herdr agent list                      # 一条命令看全部线程:working / idle / blocked / done
herdr pane read <pane_id> --source recent-unwrapped --lines 50     # 有界读某线程近况
```
**驭 / 软停**:
```bash
herdr pane send-keys <pane_id> escape   # 软停:codex 进程存活、上下文完整,只停在途 turn
# 纠偏 = 软停后照「注入任务」再发一条;终态可能报 idle 也可能报 done,等待时两个都要认
```
**标状态 / 找人**:
```bash
herdr workspace rename <wid> "cfx <任务名> · ❓等你"    # label 就是看板,状态变了就改名
herdr notification show "需要你裁决" --body "<一句话>" --sound request
```
**回收**(线程合并或放弃后):
```bash
herdr worktree remove --workspace <wid>    # 收 worktree + 关 workspace
git -C <项目> branch -D cfx/<任务名>        # 分支要自己删
```
已实测的坑:`wait agent-status` 只认 pane_id、不认 agent 名;完成态在 `idle` / `done` 间
漂移;状态检测不认识的菜单会回落 `idle`,所以"idle 很久"值得 pane read 看一眼。
## /goal 是什么
codex 的持久化目标机制:消息以 `/goal` 开头,就把一份完成契约钉进线程——**OUTCOME**(完成
时什么应为真)、**VERIFICATION**(拿什么证据验证)、**BUDGET / BLOCKED_STOP**(何时必须停)。
codex 会跨回合对着它自驱推进、拿证据自查完成度,适合大任务与探索类、优化类工作(profiling、
复现、迁移、benchmark 调优、研究审计)。小任务直接注入普通提示词即可;大任务在提示词开头加
`/goal`。不落契约文件——/goal 文本本身就是契约,worktree 里的 diff 就是产物。
## tick:全靠协调器现场判断
`/loop <间隔>` 挂心跳，每个 tick 看什么、读多少、要不要 diff、要不要驭、
要不要合,**不设固定清单,协调器自主决定**。仅有的取向:
- 发现跑偏 → 介入(注入纠偏 / Esc 软停);
- worker 干完 → 审核 → 回收线程;
- 自己裁不动 / 对外不可逆 → rename 标记 + notification 通知,**其余线程照推**,等用户在
  主 session 回复;
- 摘要如实:进展、风险、待决,不绿洗。
## 用法
```text
/curryflows <要干的活>     # 起 /loop、开线程、开始推进
/curryflows status         # herdr agent list + 各线程一句话近况
```
