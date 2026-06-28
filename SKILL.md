---
name: curryflows
description: >-
  通用工作流协调器 skill,把人类 review 从构建关键路径上解耦。一个 /loop 协调器并发推进多个有界
  Workflow kernel(功能实现 / 性能优化 / 构建测试 三个内置模板),每个产物经 codex + Claude
  跨模型 review + 反捏造门,在独立分支 + worktree 上 speculative 推进;只在合 main、对外不可逆、
  跨模型分歧三种情况升人类决策,其余靠"疑问→就地跨模型 review→分歧才升"自动消化。自驱的 codex
  /goal 线程挂只读审计 + Esc 急停 + 强目标契约(budget + blocked-stop),防跑飞。统一资源发现把
  所有在途 codex 会话 + worktree 对账,杜绝 runaway。触发于:"起 curryflows 协调器"、"用
  curryflows 跑功能实现/性能优化/构建测试"、"做带跨模型评审的并发开发"、"监督 codex /goal 别跑飞"、
  "排查 runaway codex 会话"、"把人类 review 从关键路径上解耦"。
user-invocable: true
argument-hint: '[start | status | feature|perf|test <任务契约路径> | oversee <codex-session-id> <pane> | viz]'
type: skill
tags: [工作流, 编排, 跨模型评审, 协调器, codex, worktree, 反捏造]
requires:
  - codex CLI (>=0.128, 支持 /goal)
  - tmux
  - git (>=2.5, 支持 worktree)
  - python3
---

# curryflows

把人类 review 从构建关键路径上解耦的通用工作流协调器。一个协调器 agent 在 review 待定时继续推进
相互独立的工作,只在少数"必须确认"点阻塞;解耦期的正确性由自动化门 + 跨模型 review 守住,人类只
处理真正的决策,且看的是蒸馏后的决策面而非千行原文。

> 本 skill 是通用件,不写死任何具体项目的路径或契约。每个项目的运行态(board / 决策队列 /
> worktree / 日志)落在目标项目里,不进 skill 仓。

## 三层控制流

1. **协调器(`/loop` 动态模式)= 外层调度**:维护在途线程图,推进就绪线程,无就绪时 park 释放
   上下文,被事件(线程完成、人类回复、定时)唤醒。这一层是 agent 推理,**不是** workflow。
2. **Workflow kernel = 内层有界任务**:确定性扇出(三个模板),结构化输出、可 resume、带 budget。
   Workflow 脚本**只编排**,所有动手(tmux/bash/编辑)在它 spawn 的 agent 内部。
3. **codex `/goal` = 自驱线程**:长程不确定调查,由强目标契约(budget + blocked-stop)+ 只读
   审计 + Esc 急停兜住。

## 跨模型 review(本 skill 的招牌)

每个高风险产物由 codex 与 Claude **各自独立** review,分歧即信号:两方一致且契约可判 → 自动处理;
仅一方报 → 对照 ground truth(契约 / 权威文档 / GOLD oracle)判,**不投票**;裁不动的真分歧 →
升人类决策项。这天然把人类决策队列过滤到极少数。

## 三个内置模板(共享同一套门,各自内联实现)

| 模板 | produce 中段 | 验证面 |
|---|---|---|
| 功能实现 | `parallel{core, docs}` | **独立黑盒测试套件** |
| 性能优化 | `parallel{策略 lanes}` + 正确性-vs-速度硬停 | benchmark + 正确性套件 |
| 构建测试 | inspect 覆盖率缺口 → 一次性生成 → repair | 测试套件 + 覆盖率 |

修 bug = 构建测试(加 RED 复现用例,reproduce-first 强制)→ 功能实现(拟合到绿),minimal-diff 收紧。

共享门(全部标准,三个文件各自内联实现,无单独 base-kernel.js):契约 fail-closed precheck → produce →(parallel→guarding join)→ 验证真跑+证据
→ 跨模型 review → verdict 洗白器 → bounded loop(fallback 偏继续 + max-rounds)→ 硬停
pre-execution gate → pre-archive 反捏造/越界 + minimal-diff → archive(验证过 + 真 diff + rollback)。

## 工作流可视化(硬约定)

**每新增或修改一个 workflow 模板(`workflows/*.js`),必须随之渲染一版 HTML 图,并与模板同一次 commit。**
改了 js 却没刷新对应图,视为该改动未完成(图与模板必须同步)。

```sh
python3 scripts/workflow-viz.py workflows/            # 刷新 diagrams/ 下全部 + index.html
python3 scripts/workflow-viz.py workflows/<name>.js   # 单文件 → 同名 .html
```

`workflow-viz.py` 纯 Python 无依赖,静态提取 meta / fail-closed 门 / produce lane / bounded loop /
cross-review panel(codex+Claude 多 lens 扇出 ×N)/ codex 腿 / HARD-STOP,GP(改码)/EX(只读)配色,
hover 看 prompt 摘要;生成物落 `diagrams/`(自包含 HTML,浏览器直接开)。

## 并发隔离

每个 thread = 独立分支 + worktree(默认 `~/.cache/curryflows/worktrees/<project>/<thread-id>`,
base 可配)。并发上限可配(默认保守,大仓调低)。合 main 在 barrier 处串行:先 rebase 到最新 main、
重跑验证,冲突 settle 不了升决策项。孤儿 worktree 并入资源发现对账 + `git worktree prune`。

## 人类决策(barrier)

硬闸只剩两类:**合 main**、**对外不可逆**。其余靠"疑问→就地跨模型 review→分歧 settle 不了才升
决策项"。seal-contract 放在开头(plan-tree 交叉评审 + 人封)。

## 自驱 codex 的监督(吸收自 codex-goal-overseer)

协调器取代了原来独立的 overseer 会话:每个 tick 自己跑廉价信号(`discover-threads.py` + budget),
对深度审计 spawn 一个**只读 opus subagent** 读 transcript/diff(隔离巨型 transcript,绝不进协调器
上下文),拿回裁决;坏裁决 → 协调器跑 `interrupt-target.sh` 软停 + post 决策项。codex 全走 tmux,
唯一驱动器是 `inject-steer.sh` / `interrupt-target.sh`,绝不手搓 send-keys;有界 review 用单 prompt
+ 文件交付(不挂 overseer),自驱才种 /goal(挂监督)。

## 操作

- `start` — 在当前项目起协调器 `/loop`(见 `references/coordinator.md`)。
- `status` — 跑 `scripts/discover-threads.py --project . --board <project>/.curryflows/board/threads.jsonl`,
  列所有在途资源 + 未对账的 runaway。
- `feature|perf|test <任务契约路径>` — 以某模板起一个 thread。
- `oversee <codex-session-id> <pane>` — 给一个已在跑的 codex /goal 挂监督。
- `viz [workflow.js|workflows/]` — 跑 `scripts/workflow-viz.py` 把模板渲染成 HTML 图(新增/改模板后必跑)。

## 文档索引(references)

- `architecture.md` — 三层模型、跨模型 review、barrier 模型、Workflow↔agent 边界。
- `coordinator.md` — coordinator tick runbook + `/loop` prompt。
- `base-kernel-gates.md` — 共享 kernel 与全部门。
- `template-feature.md` / `template-perf.md` / `template-test.md`。
- `codex-integration.md` — tmux 两模式 + inject/interrupt + 文件交付。
- `goal-contract.md` — /goal 强契约(budget + blocked-stop)。
- `decision-surface.md` — 决策项格式 + barrier/疑问驱动。
- `goal-cookbook.md` — codex /goal 参考(吸收自 overseer)。

## 依赖与边界

硬依赖见 frontmatter `requires`。本 skill 不依赖 oh-my-humanize 运行时——`.omhflow` 例子只作门的
模式参考,不提升为硬约束。codex-goal-overseer 的能力已并入本 skill。
