# reviewer / arbiter 契约

> **执行载体(本轮 CANON)**:reviewer / arbiter 面板不再由协调器手搓并发派 subagent,而是由
> Claude Code **官方 Workflow** 承载执行——协调器每个 tick 调 `Workflow({ scriptPath:
> "<skillDir>/workflows/review-panel.js", ... })` 跑随仓附带的参考脚本 `workflows/review-panel.js`。
> 本文件是该 Workflow 实现 reviewer / arbiter 时必须遵守的**契约规范**(lens、verdict schema、
> 反捏造、跨模型硬规则、独立复验、两个状态机介入点、arbiter 收敛不投票),语义不变,只改"由谁执行"。

reviewer 是 curryflows tick 的第一步(审核优先)。`review-panel.js` 内部 pipeline 逐线程:stage1
**并发多 lens**(一律 opus,只读),各取不同 lens;reviewer 把巨型 transcript / diff 隔离在自己上下文里,
只回一条蒸馏裁决——这是协调器主 session 不被撑爆的前提。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`reviewer-spec.md` — reviewer / arbiter
> 契约(由官方 Workflow `workflows/review-panel.js` 执行):读什么、裁决 schema、反捏造 + 独立复验、
> 清晰摘要要求。

## 角色与边界

- **agentType**:`Explore`(只读 + Bash),由 `workflows/review-panel.js` stage1 拉起。reviewer
  **不改任何代码、不操作 tmux**(写动作全归 operator —— operator 仍是 curryflows 的 subagent,**非** Workflow)。
- **模型**:opus(所有 lens / arbiter 一律强力)。
- **跨模型**:worker 是 codex、reviewer 是 Claude,produce 与 review 天然跨模型。多个 reviewer
  各取不同 lens(正确性 / 安全 / 复现 / 越界 / 不变量),各自独立,互不可见。
- **上下文隔离**:transcript 可达几百 MB,reviewer 读它、消化它,**只回裁决**;原文绝不进协调器。

## 跨模型硬规则(CANON [G],必须满足)

> **跨模型 review 仅当 `worker.model != reviewer.model` 才成立。** 协调器**必须**保证每个被审线程的
> reviewer 模型集合里存在与该线程 worker 不同的模型;否则审核退化为单模型,跨模型保证作废。

- **worker = codex `/goal`(默认)**:reviewer = Claude opus,produce 与 review 天生跨模型,
  Claude reviewer 即满足本规则。
- **worker = Claude subagent(非 codex)**:Claude opus reviewer 与 worker 同模型,**不构成跨模型**。
  此时**至少一个 reviewer 必须是 codex 腿**,通过 `scripts/codex-review.sh` 拉 codex 侧审核——
  这种情况下 `codex-review.sh` 是**必需,不是可选**;缺它则跨模型保证作废,该线程裁决不合格。
  codex 腿照 **CANON [H]** 起:在本 reviewer(subagent)内跑 `codex-review.sh`(tmux 启动 + inject-steer
  注入 + 轮询交付文件到稳定),**禁用** codex 插件 / `codex exec` / companion CLI;启动与监控同属这个
  reviewer subagent,巨型 transcript 不外溢(见 `codex-integration.md`)。

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
**已封契约**(落点见 CANON [D]:`<project>/.curryflows/contracts/<thread-id>.md`,即 `task-contracts/task.md`
填好的副本,由看板线程的 `contract` 字段指向;封定的 OUTCOME / VERIFICATION / CONSTRAINTS /
BOUNDARIES / ITERATION / BUDGET / BLOCKED_STOP)审:

- **drift**:是否偏离 OUTCOME / 在没有可辩护路径时空转。
- **捏造 / 假实现**:是否声称完成但无证据;是否把 approximate 说成 exact;是否伪造测试/数字。
- **越界**:是否改了 BOUNDARIES 之外的文件。
- **不变量**:是否破坏项目已有接口 / 行为 / 关键路径。
- **预算 / 停点**:是否到 BUDGET 仍不停、是否把"到顶"当"完成";BLOCKED_STOP 触发时是否如实停并报告。
- **barrier**:是否撞到对外不可逆 / 跨模型真分歧(合 main 已自动化——reviewer 只报 `verified` / merge-ready,不作人类 barrier,见 CANON [L])。

### 3) 独立复验(不信 worker 自述)

**完成必须基于证据,不是 worker 自认为完成。** reviewer 不接受 transcript 里"tests passed /
done"这类自述——必须**独立核对**:对照 VERIFICATION 列出的具体证据(实跑命令输出、benchmark 数字、
GOLD oracle 对照、复现步骤),自己 re-derive 或重跑。证据对不上 = 不通过,无论 worker 怎么说。

#### 独立性三档(CANON [P],必须声明实际达到哪档)

"独立复跑"不是一个二值词——它有三档,弱独立冒充强独立会让复验形同虚设(已观测两次:worker 自己 spawn
一个 subagent 冒称 independent replay;repro lens 声称独立复跑但没 rebuild native,GATE 实际还是靠
worker 的 checked-in log):

```
L1  静态复核    读 diff / grep / 看 exit code / re-derive 数字        —— 不重跑
L2  同环境复跑  复用 worker 已建的 .venv / .so 再跑一遍               —— 未隔离构建产物
L3  真独立      抹掉 venv + 删构建产物(.so 等)+ clean rebuild + 亲自跑 —— 唯一算"独立"
```

- **凡是判 `verified` 的复验一律锁 L3**:必须 `rm -rf` worker 的 venv、删掉所有构建产物(`*_native*.so`
  等)、从干净树 clean rebuild、直接跑测试(不经 worker 的 wrapper)。用 worker 已建的 `.so`(L2)或只读
  检查(L1)都**不足以**判 `verified`。
- **worker 谱系内的"复跑"不算独立**:worker 自己、或 worker spawn 的 subagent(同 session 谱系)跑出的
  replay 一律**不采信**;独立性来自 reviewer **另起**的执行,不是 worker 声称的。
- **reviewer 必须在裁决里声明本次实际达到的档位**(`independence_tier` = L1 / L2 / L3 / n/a);诚实报"我
  只做到 L2 / 没 rebuild"是**对的、要保留**——但那就**不能**给出 `verified` 级结论(见下状态机介入点)。
  契约的 VERIFICATION 本就该把 L3 动作写死(见 `task-contracts/task.md`),逼 reviewer 做到。

## reviewer 在状态机里的两次介入(CANON [A])

reviewer 在线程状态机里有两个**不同**的介入点,二者职责不可混淆(状态枚举见 CANON [A]:
`ready -> running -> idle -> reviewed -> committed -> verified -> session-reaped -> merged | rolled-back`,
另有可从任意状态进入的 `blocked-human`):

- **`idle -> reviewed`(审计)**:worker 到 budget / 触发 blocked-stop / 自认完成而落 `idle` 后,
  reviewer 执行上面"三件事"的对账资源 + 审产物 + 初步独立核对,把结论写成裁决,`last_verdict`
  记到看板(`board.py upsert-thread --last-verdict ...`),协调器据此把 state 置 `reviewed`。
  此处**不**改代码、**不** commit。
- **`committed -> verified`(独立复验,**锁 L3**)**:worker 工作已 commit 到自己分支(`committed`,只保证
  durability,非 merge 非 push)后,reviewer 在**该 committed 分支的 worktree 上独立复跑** VERIFICATION
  列出的测试 / 命令 / oracle 对照,且必须达到 **L3**(抹 venv + 删 `.so` + clean rebuild + 亲自跑,
  CANON [P])——L3 通过才置 `verified`;只做到 L1 / L2、或达不到 L3、或证据对不上,则**不进 `verified`**
  (回 `continue` / `escalate`,并在 `independence_tier` 如实报实际档位)。这是"不信 worker 自述"的硬
  复验,对应上面"3) 独立复验"。
  注:阶段化 reap 在 `verified` 才 reap 会话置 `session-reaped`、保留 worktree+分支待人类 merge(CANON [B])。

## 反捏造硬规则

- **证据指向 checked-in artifact,不是 prose**:裁决里引用的证据必须是 worktree 内 `.curryflows/`
  落盘的文件路径(log / findings / diff / reproducer),不能是一段自然语言。
- **调 codex 第二意见时严禁捏造**:若本 reviewer 用 `scripts/codex-review.sh` 拉 codex 侧审核,
  脚本非零退出即返回 `{lens:'codex', verdict:'failed', failed:true, findings:[]}`,**严禁补造 findings**。
- **裁决必含异议**:若与其它 lens / 与 worker 自述不一致,必须如实写出分歧,不许为了"通过"而抹平。

## 裁决 schema(reviewer 的回传)

每个 lens reviewer 回一个对象(stage2 由每线程 arbiter 收敛:**不投票**、对照契约 ground truth、
裁不动则 escalate;`review-panel.js` 最终把收敛后的 `reviews[] / escalations[]` 回传协调器):

| 字段 | 类型 | 说明 |
|---|---|---|
| `lens` | string | 本 reviewer 的视角(correctness / security / repro / bounds / invariant …);codex 腿填 `codex` |
| `verdict` | enum | `pass` \| `continue` \| `escalate` \| `runaway`(资源对账项) \| `failed`(codex 腿脚本非零退出) |
| `findings` | array | 每项 `{title, severity, evidence(路径), reproducible(步骤或测试缺口)}` |
| `dissent` | string \| null | 与其它视角 / worker 自述的分歧;无则 null,**不许省略字段** |
| `unverified` | array | 本 lens 未能独立核实的点(强制如实列出) |
| `resources` | array | 资源对账结果:`{kind: unregistered\|runaway-suspect\|orphan\|reclaimable, ref, evidence}` |
| `failed` | bool | 本 lens(典型为 codex 腿)是否因脚本非零退出而失败;失败时 `findings` 必须为空 |

per-lens 对象**不含** `thread`——arbiter 收敛后每个外层 `reviews[]` 项才叠加 `thread / branch /
worktree`;其 `escalate[]` 每项给 `{title, divergence, evidence(路径), recommendation}`,供协调器
组装决策项(见 `decision-surface.md`)。**`verdict=escalate` = arbiter 裁不动 → 归人类**:协调器只把它
**组装成决策项**(该线程 `blocked-human` + Esc 软停等人,CANON [N]),**绝不把 `escalate` 替换成协调器
自己的 RULING、也不据 `recommendation` 自行放行**——`recommendation` 是给人类看的建议(CANON [N])。

## 清晰、不糊弄

reviewer 的裁决是协调器回主 session 摘要的原料。**禁止绿洗**:`unverified` / `dissent` /
`findings` 不许为了简洁而省。一条只写"通过"而不附独立复验证据的裁决,视为不合格。
