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
| `seal-contract` | 契约封定点(plan-tree 交叉评审 + 人封),在流程**开头** | 起 worker 之前(封定目标契约) |
| `merge-main` | 合 main 硬闸:rebase 最新 main + 重跑验证,冲突 settle 不了升此项 | 协调器合 main barrier |
| `outward-irreversible` | 对外不可逆操作硬闸 | 协调器 / worker 遇不可逆动作 |
| `model-divergence` | 跨模型真分歧 / 契约缺口,reviewer escalate、协调器裁不动 | reviewer 裁决 → 协调器收敛 |

---

## 1b. CANON [I]:启动 fail-open(no-answer → 默认起 /loop)

barrier 是"该不该停下等人"的闸;**启动决策不在其列**。当 curryflows 主动就"要不要起协调器 / 要不要把某块可执行长跑活交给 worker"问人类、而人类在该轮**未回答**时:

- **默认动作 = 起 `/loop`**:协调器进入 tick 循环——对**契约可自动封定且过 `board.py validate-contract`** 的可执行有界 / 长跑活,seal 后在 tmux 起 worker(CANON [H]);对需要人类封契约的活(seal-contract 前置未过)以及那条未回答的问题,**原样 post 到 `decisions.jsonl`** 等人类异步裁、线程置 `blocked-human`。**绝不静默退回 inline、也不停下干等**(无可起的就绪线程时,loop 按常规 park 等事件,而非退回 inline)——这与"前进不等人"一致,启动本身不是 barrier。
- **例外(仍 fail-closed)**:`merge-main` / `outward-irreversible` / `model-divergence` 三类硬闸,以及 `seal-contract` 前置,仍各自挡住其**具体的不可逆动作 / 未封契约的那条线程**;但只挡那一个动作 / 那一条线程,**不挡 `/loop` 跑别的就绪线程**。即:无回答时,需要人类封契约或人类确认不可逆动作的那部分等着,其余可执行活照起。

实务:`/curryflows <自由任务>`(非字面 `start` 子命令)即视为**启动意图**;协调器可就边界 / 第一刀提一个非阻断的澄清项,但**得不到回答时按本规则默认起 loop**,不得因"没拿到放行"而停在 inline。

---

## 2. 纪律(什么入队、什么不入队)

- **agreement + 契约可判 → 自动处理,不入队。** 这是 curryflows 把人类决策队列压到极少数的根本机制(reviewer 收敛规则见 `reviewer-spec.md`)。
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
| `divergence` | string | 分歧 / 缺口 / 不可逆点的具体描述(来自 reviewer `escalate` 裁决) |
| `evidence` | string | 指向 checked-in artifact / reproducer 的路径(**不是 prose**),如 `${worktree}/.curryflows/validate-r2.log`、`xreview-codex-r1.md` |
| `recommendation` | string | 有依据的建议;**必填**,**必须引用契约 / 权威依据** |
| `options` | array | 可选项列表(供人类选择) |
| `status` | string | 决策项状态(如 open / resolved) |
| `resolution` | string \| null | 人类裁决结果;未决时为空 |

字段对应关系(来自 reviewer 裁决,见 `reviewer-spec.md`):`verdict=escalate` 的 reviewer 裁决每项含 `{title, divergence, evidence, recommendation}`(`title`/`divergence`/`recommendation` 必填),协调器收齐多个 reviewer 的裁决、对真分歧裁不动者据此组装成上面的完整决策项,补上 `id` / `barrier` / `thread` / `summary` / `options` / `status` / `resolution`。

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

1. reviewer 裁决 `verdict=escalate` 的项(对照 ground truth 裁不动),给出 `{title, divergence, evidence, recommendation}`(见 `reviewer-spec.md`)。
2. 协调器在决策步收齐多个 reviewer 的裁决;一致且依据可判 → 自动处理,**不入队**;真分歧裁不动 → 升人类。
3. 协调器把升人类的项组装成完整决策项,把对应线程在看板置 `blocked-human`。
4. **协调器**把这些项 **post 到 `<project>/.curryflows/board/decisions.jsonl`**(per-project 运行态,不进 skill 仓;与 `threads.jsonl` 同目录)。
5. 人类只在这条队列上异步工作:逐条看 `summary` / `divergence` / `evidence`(点开 artifact)/ `recommendation`,在 `options` 里选;裁决结果**通过 `board.py resolve-decision --id <did> --resolution <text> [--status resolved|rejected]` 写回**——`board.py` 是 `decisions.jsonl` 的唯一写入者,**绝不手编 `decisions.jsonl`**(手编易写坏行,而 `render-board.py` 对坏行静默跳过会无声丢决策)——**前进不等人**。
6. 人类回复构成一个事件,唤醒 park 的协调器 `/loop` 在下个 tick 落地对应 thread(落地两条路见第 5 节)。

merge-main 与 outward-irreversible 两类硬闸不经 reviewer 裁决,由协调器在合 main barrier / 遇不可逆动作时直接组装决策项 post 到同一 `decisions.jsonl`;seal-contract 在流程开头由 plan-tree 交叉评审 + 人封产生。

---

## 5. 裁决怎么落地:扩展(relaunch)而非回滚

人类在某项写回 `resolution`(经 `board.py resolve-decision`,见第 4 节第 5 步)后,协调器在下个 tick 读回(`board.py list-decisions --open` 把已 resolved 项收敛掉),按裁决落地对应 thread。**裁决落地是「扩展」还是「回滚」由人类选的 `option` 决定**,两条路都不手编 JSONL——thread 状态 / `attempt` / `codex_session` 经 `board.py upsert-thread` 写,decision 状态经 `board.py resolve-decision` 写。

- **扩展(relaunch)**:人类选了一个让线程**继续推进**的 option(如第 3 节示例里的「排队(贴合不丢请求)」或「补契约后重跑」)。此时协调器**复用该线程现有 worktree + 分支(不重建 worktree)**,起一个**全新 codex 会话**,注入更新后的已封契约;通过 `board.py upsert-thread` 把该线程 `state` 置回 `running`、`attempt` 加一、`codex_session` 更新为新 rollout id。这是扩展,**不是回滚**——既有进度、分支与 worktree 全部保留(relaunch 操作细节见 `coordinator.md`)。
- **回滚(rollback)**:人类裁决放弃该线程时才走 `rolled-back` 终态(回收 worktree + 分支)。扩展决策**不得**误落成回滚。

`blocked-human` 线程在裁决落地后即离开该状态:扩展 → `running`,回滚 → `rolled-back`。
