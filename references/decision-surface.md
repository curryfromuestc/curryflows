# curryflows 决策面(human decision item)

一句话定位:决策面是 curryflows 给人类看的唯一界面——经自动化门 + 跨模型 review 蒸馏后,只有真正需要人判的项才入队,人看的是结构化决策项而非千行原文。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`decision-surface.md` — 决策项格式 + barrier/疑问驱动。

---

## 1. barrier 模型(入队的前提)

curryflows 默认不阻塞:产生疑问 → 就地跑跨模型 review → 一致且契约可判就自动处理,**不入队**。只有以下三类才入队成决策项:

1. **分歧**(cross-review 对照 ground truth 裁不动的真分歧)。
2. **契约缺口**(契约无法判定该走哪条路)。
3. **不可逆**(合 main、对外不可逆这类硬闸)。

对应的四个 barrier 取值:

| barrier | 含义 | 触发处 |
|---|---|---|
| `seal-contract` | 契约封定点(plan-tree 交叉评审 + 人封),在流程**开头** | 进 Workflow 之前 |
| `merge-main` | 合 main 硬闸:rebase 最新 main + 重跑验证,冲突 settle 不了升此项 | 协调器合 main barrier |
| `outward-irreversible` | 对外不可逆操作硬闸 | 协调器 / 线程遇不可逆动作 |
| `model-divergence` | 跨模型真分歧 / 契约缺口,arbiter escalate 出来 | 模板 arbiter → `escalations` |

---

## 2. 纪律(什么入队、什么不入队)

- **agreement + 契约可判 → 自动处理,不入队。** 这是 curryflows 把人类决策队列压到极少数的根本机制(见门 4/5,`base-kernel-gates.md`)。
- **只有分歧 / 契约缺口 / 不可逆 → 入队。**
- **`recommendation` 必填**:每个决策项必须带一个有依据的建议,人类是在「确认 / 否决一个有依据的建议」,不是从零裁决。
- **`evidence` 指向 checked-in artifact,不是 prose**:必须指向 worktree 内 `.curryflows/` 落盘的证据(log / findings / diff / reproducer),不能是一段自然语言描述。
- **`recommendation` 必须引用契约 / 权威依据**(契约字段、权威文档、GOLD oracle、negative control 结果等),不能是无据主张。

---

## 3. 决策项 JSON schema

决策项是一行 JSON(JSONL),字段如下:

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 决策项唯一标识 |
| `barrier` | enum | `seal-contract` \| `merge-main` \| `outward-irreversible` \| `model-divergence` |
| `thread` | string | 产生该项的 thread 标识(对应 board `threads.jsonl` 里的线程 / 分支) |
| `summary` | string | 一句话讲清要人判什么 |
| `divergence` | string | 分歧 / 缺口 / 不可逆点的具体描述(来自 arbiter `escalate[].divergence`) |
| `evidence` | string | 指向 checked-in artifact / reproducer 的路径(**不是 prose**),如 `${worktree}/.curryflows/validate-r2.log`、`xreview-codex-r1.md` |
| `recommendation` | string | 有依据的建议;**必填**,**必须引用契约 / 权威依据** |
| `options` | array | 可选项列表(供人类选择) |
| `status` | string | 决策项状态(如 open / resolved) |
| `resolution` | string \| null | 人类裁决结果;未决时为空 |

字段对应关系(来自模板 `VERDICT_SCHEMA` 的 `escalate` 项):arbiter 产出的 `escalate[]` 每项含 `{title, divergence, evidence, recommendation}`(`title`/`divergence`/`recommendation` 为 schema 必填),协调器据此组装成上面的完整决策项,补上 `id` / `barrier` / `thread` / `summary` / `options` / `status` / `resolution`。

示例(一行 JSONL,此处折行仅为可读):

```json
{
  "id": "dec-20260627-0007",
  "barrier": "model-divergence",
  "thread": "curryflows/feat-rate-limit",
  "summary": "rate limiter 在 burst 边界的语义两模型判断不一致",
  "divergence": "codex 认为应丢弃超额请求,Claude 认为应排队;契约只写了 max_rps 未定 burst 行为",
  "evidence": "/repo/.curryflows/validate-r2.log; /repo/.curryflows/xreview-codex-r2.md",
  "recommendation": "按契约 acceptance 第 3 条(强调低延迟)倾向丢弃;但契约未显式定义 burst,属契约缺口,需人封定",
  "options": ["丢弃超额(贴合低延迟)", "排队(贴合不丢请求)", "补契约后重跑"],
  "status": "open",
  "resolution": null
}
```

---

## 4. 决策项怎么产生、落到哪

1. 模板内 arbiter 对照 ground truth 裁不动的项 → 写进 verdict 的 `escalate` 数组(`VERDICT_SCHEMA.escalate`)。
2. bounded loop 内 `if (verdict.escalate && verdict.escalate.length) escalations.push(...verdict.escalate)`,且路由 `escalate` 时 `break`,把控制权交回协调器。
3. 模板最终 `return` 的对象里带 `escalations`;`status` 为 `blocked-human` 即表示有待人类决策项。
4. **协调器**把这些 `escalations` 组装成完整决策项,**post 到 `<project>/.curryflows/board/decisions.jsonl`**(per-project 运行态,不进 skill 仓;与 `threads.jsonl` 同目录)。
5. 人类只在这条队列上工作:逐条看 `summary` / `divergence` / `evidence`(点开 artifact)/ `recommendation`,在 `options` 里选,写回 `resolution`。
6. 人类回复构成一个事件,唤醒 park 的协调器 `/loop` 继续推进对应 thread。

merge-main 与 outward-irreversible 两类硬闸不经 arbiter,由协调器在合 main barrier / 遇不可逆动作时直接组装决策项 post 到同一 `decisions.jsonl`;seal-contract 在流程开头由 plan-tree 交叉评审 + 人封产生。
