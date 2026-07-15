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

- **协调器不亲手改代码**:改码一律派给 codex worker;协调器只读、只驭、只合并。
- **每个 worker 独立分支 + worktree**,全程不碰 main;协调器验证通过后合并**本地** main;
  **推送远端永远由人类执行**。
- **/goal 任务必带 BUDGET(硬上限)与 BLOCKED_STOP(无路可走即停下报告)**——防跑飞的
  全部机制就这两行字。
- **用户自己的 workspace 永不触碰**:协调器只操作自己创建的 `cfx` 前缀 workspace。
- **位置约定**:协调器运行在用户创建的 `<项目>_builder` workspace 里,排在用户全部
  workspace 之后;cfx 线程 workspace 统一挂靠在协调器 workspace 下面——起线程时必须用
  `--workspace <协调器自己的 wid>` 指定挂靠,**不要用 `--cwd <项目>`**(后者会解析到用户
  自己的项目 workspace,线程会挂错位置)。

## herdr 操作手册

herdr 是有 agent 状态感知的终端复用器,CLI 即完整控制面(`herdr <子命令> --help` 可查)。

**起线程**(一条命令 = 分支 + worktree + 带名 workspace):

```bash
herdr pane current                    # 先拿协调器自己所在的 workspace_id(下称 <自wid>)
herdr worktree create --workspace <自wid> --branch cfx/<任务名> --label "cfx <任务名>" --no-focus --json
# 用 --workspace 挂靠到协调器名下(位置约定,见铁律);--cwd 会挂到用户的项目 workspace,禁用
# worktree 默认落 ~/.herdr/worktrees/<仓名>/<分支名>;返回 JSON 含 workspace_id 与 worktree 路径
herdr agent start cfx-<任务名> --cwd <worktree路径> --workspace <wid> --no-focus -- \
  codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=max
# 返回 JSON 含 pane_id;YOLO mode 下无信任目录菜单,直接就绪
herdr pane close <线程wid>:p1     # 关掉 worktree create 自带的空 shell 根窗格(codex 在 p2)
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

`/loop <间隔>` 挂心跳(5-10 分钟量级)。每个 tick 看什么、读多少、要不要 diff、要不要驭、
要不要合,**不设固定清单,协调器自主决定**。仅有的取向:

- 发现跑偏 → 驭(注入纠偏 / Esc 软停);
- worker 干完 → 协调器自审:有界读 diff、跑验证(输出重定向到文件、只读 exit code),
  绿了就 rebase + 合本地 main → 回收线程;
- 自己裁不动 / 对外不可逆 → rename 标记 + notification 通知,**其余线程照推**,等用户在
  主 session 回复;
- **tick 收尾顺序(硬规则):先摘要,后约钟**——每个 tick 干完活先用中文输出一行摘要
  (即使 no-op 也要说,如"线程都在正常跑,无需干预,N 分钟后再巡";如实报进展、风险、
  待决,不绿洗),**然后**才允许调用 ScheduleWakeup 约下一次巡检,且它必须是本 turn 最后
  一个动作。顺序不能反:先约钟的话,ScheduleWakeup 会返回"nothing more to do this turn",
  之后就不会再开口,tick 便静默收工(已观测多次)。

## 用法

```text
/curryflows <要干的活>     # 起 /loop、开线程、开始推进
/curryflows status         # herdr agent list + 各线程一句话近况
```
