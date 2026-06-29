# reviewer subagent 契约

reviewer 是 curryflows tick 的第一步(审核优先)。协调器每个 tick **并发派多个** reviewer
subagent(一律 opus,只读),各取不同 lens。reviewer 把巨型 transcript / diff 隔离在自己上下文里,
只回一条蒸馏裁决——这是协调器主 session 不被撑爆的前提。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`reviewer-spec.md` — reviewer subagent
> 契约:读什么、裁决 schema、反捏造 + 独立复验、清晰摘要要求。

## 角色与边界

- **agentType**:`Explore`(只读 + Bash)。reviewer **不改任何代码、不操作 tmux**(写动作全归
  operator)。
- **模型**:opus(所有 subagent 一律强力)。
- **跨模型**:worker 是 codex、reviewer 是 Claude,produce 与 review 天然跨模型。多个 reviewer
  各取不同 lens(正确性 / 安全 / 复现 / 越界 / 不变量),各自独立,互不可见。
- **上下文隔离**:transcript 可达几百 MB,reviewer 读它、消化它,**只回裁决**;原文绝不进协调器。

## 每个 reviewer 必做的三件事

### 1) 对账资源真值(只读)

```bash
python3 <skillDir>/scripts/discover-threads.py --project . \
  --board ./.curryflows/board/threads.jsonl
```

判读(精确语义见脚本头注与 `codex-integration.md`):

- **exit 2** = 有在途资源(active codex 会话或 `curryflows/*` worktree)未登记到看板 → runaway。
- **`UNREGISTERED`** = active 会话的 `session_id` 不在看板 `codex_session` 集合里。
- **`RUNAWAY-SUSPECT`** = active 且 rollout 体积 ≥ `--runaway-mb`(默认 50MB)。
- **孤儿 worktree** = `curryflows/*` 分支 worktree 未被看板追踪。
- **可回收** = 看板有记录但其 codex 会话已 idle 跑完 / worktree 已 merged-or-rolled-back。

reviewer 只**报告**这些;软停与回收是协调器决策后由 operator 执行。

### 2) 审产物(只读,对照目标契约)

对每个在途 / 刚完成的 codex worker,读其 transcript + `git -C <worktree> diff`,对照该 worker 的
目标契约(`task-contracts/task.md` / `goal-contract.md` 封定的 OUTCOME / VERIFICATION / CONSTRAINTS /
BOUNDARIES / ITERATION / BUDGET / BLOCKED_STOP)审:

- **drift**:是否偏离 OUTCOME / 在没有可辩护路径时空转。
- **捏造 / 假实现**:是否声称完成但无证据;是否把 approximate 说成 exact;是否伪造测试/数字。
- **越界**:是否改了 BOUNDARIES 之外的文件。
- **不变量**:是否破坏项目已有接口 / 行为 / 关键路径。
- **预算 / 停点**:是否到 BUDGET 仍不停、是否把"到顶"当"完成";BLOCKED_STOP 触发时是否如实停并报告。
- **barrier**:是否撞到合 main / 对外不可逆 / 跨模型真分歧。

### 3) 独立复验(不信 worker 自述)

**完成必须基于证据,不是 worker 自认为完成。** reviewer 不接受 transcript 里"tests passed /
done"这类自述——必须**独立核对**:对照 VERIFICATION 列出的具体证据(实跑命令输出、benchmark 数字、
GOLD oracle 对照、复现步骤),自己 re-derive 或重跑只读检查。证据对不上 = 不通过,无论 worker 怎么说。

## 反捏造硬规则

- **证据指向 checked-in artifact,不是 prose**:裁决里引用的证据必须是 worktree 内 `.curryflows/`
  落盘的文件路径(log / findings / diff / reproducer),不能是一段自然语言。
- **调 codex 第二意见时严禁捏造**:若本 reviewer 用 `scripts/codex-review.sh` 拉 codex 侧审核,
  脚本非零退出即返回 `{reviewer:'codex', failed:true, findings:[]}`,**严禁补造 findings**。
- **裁决必含异议**:若与其它 lens / 与 worker 自述不一致,必须如实写出分歧,不许为了"通过"而抹平。

## 裁决 schema(reviewer 的回传)

每个 reviewer 回一个对象(协调器据多个 reviewer 的裁决收敛):

| 字段 | 类型 | 说明 |
|---|---|---|
| `thread` | string | 被审线程 / 分支标识 |
| `lens` | string | 本 reviewer 的视角(correctness / security / repro / bounds / invariant …) |
| `verdict` | enum | `pass` \| `continue` \| `escalate` \| `runaway`(对资源对账项) |
| `findings` | array | 每项 `{title, severity, evidence(路径), reproducible(步骤或测试缺口)}` |
| `dissent` | string \| null | 与其它视角 / worker 自述的分歧;无则 null,**不许省略字段** |
| `unverified` | array | 本 lens 未能独立核实的点(强制如实列出) |
| `resources` | array | 资源对账结果:`{kind: unregistered\|runaway-suspect\|orphan\|reclaimable, ref, evidence}` |

`verdict=escalate` 的项还需给 `{divergence, recommendation}`,供协调器组装决策项(见
`decision-surface.md`)。

## 清晰、不糊弄

reviewer 的裁决是协调器回主 session 摘要的原料。**禁止绿洗**:`unverified` / `dissent` /
`findings` 不许为了简洁而省。一条只写"通过"而不附独立复验证据的裁决,视为不合格。
