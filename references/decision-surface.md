# curryflows 决策面(human decision item)

一句话定位:决策面是 curryflows 给人类看的唯一界面——经自动化门 + 跨模型 review 蒸馏后,只有真正需要人判的项才入队,人看的是结构化决策项而非千行原文;裁决路径 = 在主 session 对每 tick 摘要直接回复(摘要完整列出全部 open 项),协调器执行 `board.py resolve-decision` 落盘,board-tui 仅只读查看(CANON [R])。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`decision-surface.md` — 决策项格式 + barrier/疑问驱动。

---

## 1. barrier 模型(入队的前提)

curryflows 默认不阻塞:产生疑问 → 就地跑跨模型 review → 一致且契约可判就自动处理,**不入队**。只有以下三类才入队成决策项:

1. **分歧**(cross-review 对照 ground truth 裁不动的真分歧)。
2. **契约缺口**(契约无法判定该走哪条路)。
3. **不可逆**(对外不可逆这类硬闸;**合 main 已不在此列——`verified` 即自动合,见 CANON [L]**)。

对应的四个 barrier 取值:

| barrier | 含义 | 触发处 |
|---|---|---|
| `seal-contract` | 契约封定点(plan-tree 交叉评审 + 人封),在流程**开头**;封前过两道门:`validate-contract`(8 字段文本门)+ environment-precondition dry-run(CANON [O],在 throwaway worktree 上真跑 `preconditions`) | 起 worker 之前(封定目标契约) |
| `merge-main` | **实质退役(CANON [L])**:`verified` 即自动 rebase + 重跑验证 + 合;冲突 / 验证回归走 worker-first 修复链自动修,**不 post**;仅解决中暴露真分歧才走 `model-divergence` | 几乎不用 |
| `outward-irreversible` | 对外不可逆操作硬闸 | 协调器 / worker 遇不可逆动作 |
| `model-divergence` | 跨模型真分歧 / 契约缺口,reviewer escalate、协调器裁不动 | reviewer 裁决 → 协调器收敛 |

---

## 1b. CANON [I]:启动 fail-open(no-answer → 默认起 /loop)

barrier 是"该不该停下等人"的闸;**启动决策不在其列**。当 curryflows 主动就"要不要起协调器 / 要不要把某块可执行长跑活交给 worker"问人类、而人类在该轮**未回答**时:

- **默认动作 = 起 `/loop`**:协调器进入 tick 循环——对**契约可自动封定且过 `board.py validate-contract`** 的可执行有界 / 长跑活,seal 后在 tmux 起 worker(CANON [H]);对需要人类封契约的活(seal-contract 前置未过)以及那条未回答的问题,**原样 post 到 `decisions.jsonl`** 等人类异步裁、线程置 `blocked-human`。**绝不静默退回 inline、也不停下干等**(无可起的就绪线程时,tick 按常规 no-op 收尾、等下一次心跳,而非退回 inline)——这与"前进不等人"一致,启动本身不是 barrier。
- **例外(仍 fail-closed)**:`outward-irreversible` / `model-divergence` 两类硬闸,以及 `seal-contract` 前置(合 main 已自动化,仅验证失败才升,见 CANON [L]),仍各自挡住其**具体的不可逆动作 / 未封契约的那条线程**;但只挡那一个动作 / 那一条线程,**不挡 `/loop` 跑别的就绪线程**。即:无回答时,需要人类封契约或人类确认不可逆动作的那部分等着,其余可执行活照起。

实务:`/curryflows <自由任务>`(非字面 `start` 子命令)即视为**启动意图**;协调器可就边界 / 第一刀 **post 一个非阻断决策项**(进 `decisions.jsonl`,**绝不 `AskUserQuestion`**,见 CANON [K]),但**得不到回答时按本规则默认起 loop**,不得因"没拿到放行"而停在 inline。

**[I] 的边界(fail-closed):`no-answer → 默认动作` 只适用于"要不要起 loop / 要不要把某块可执行活交给 worker"这类启动问题,绝不外推到 barrier 决策项。** 一个 `outward-irreversible` / `model-divergence` / 需人定的 ABI·编码·tier 决策项,**沉默一律等于继续等,不等于放行**(见 CANON [N])。把 [I] 的"问了没答就按默认走"套到 barrier 决策项上,就是已观测的越权失败模式("人类知悉未异议 → 按默认推进")。

---

## 1c. CANON [K]:协调器绝不阻塞询问;人类决策只走异步决策面(前进不等人)

**协调器在 /loop 全程绝不调用 `AskUserQuestion` 或任何阻塞式提问来 gate 推进——整个过程不应出现一次 AskUserQuestion。** 阻塞弹窗违背"前进不等人";人类界面只有一个:durable 决策面(`decisions.jsonl`)+ 每-tick 摘要里**完整列出的 open 决策项**——人类在主 session 对摘要直接回复,协调器执行 `board.py resolve-decision` 写回(board-tui 纯只读,仅供查看,CANON [R])。回复是人有空时主动打字,不是弹窗,与本条不冲突。

每 tick 对每条待推进项判一次,二选一:

- **无依赖 / 无需真决策**——选下一片 / 下一批 worker、推进节奏、并行编排、契约可自动封定的——**直接推进,不问不停**:按 plan / 北极星自主选下一波,seal(过 `validate-contract`)+ 起 worker(fail-open,CANON [I]);且按 CANON [M] 流水线化——scoping 与在途执行重叠、双水位补货、绝不等上一波收官(权威见 `coordinator.md`「调度纪律」)。
- **有真决策**——对外不可逆、跨模型真分歧、外部阻塞(env / conda ToS 等)、需人定的 ABI / 编码选择(**合 main 已自动化,仅验证失败才升,见 CANON [L]**)——`board.py post-decision` 进 `decisions.jsonl`(带 `recommendation` + `options`),把**该线程**置 `blocked-human` 且**协调器对其 codex 注入 Esc 软停**(`interrupt-target.sh`,进程存活、goal 上下文完整,**绝不 reap**),摘要完整列出该决策项;**只 hold 该线程,其余线程照推**。`recommendation` 是给人类看的建议,**协调器不得据此自行放行**——该线程停到人类明确 resolve 为止(见 CANON [N])。

**混合波**:把可推进的部分立刻起,只把需决策的部分入队——**绝不因为一波里有一项要决策就把整波停下来问**(已观测反例:把「Merge(真 barrier)」和「Next slice(无依赖)」捆进同一次 AskUserQuestion,整波停等)。若某 tick 确实无任何可推进项(全卡在 open 决策上),则本 tick **no-op 收尾**——下一次心跳照常来,人类 resolve 后的那个 tick 自然落地,**而非弹窗**。

人类有空时在主 session 对 tick 摘要直接回复(摘要完整列出每个 open 决策项),协调器执行 `board.py resolve-decision` 回写,下个 tick 落地已裁决项。**"不等人"的准确含义**:不用弹窗打断你、也不让无关线程陪着停——**不是**决策项到点自动放行。有决策项的**那条**线程一直停等你(CANON [N]),其余无依赖线程照跑(CANON [M]);你没回,那条线程就一直停着。本规则禁掉阻塞弹窗并强化 CANON [I]——CANON [I] 里的"问",从不是弹窗,而是异步决策项;但异步**不等于**不等人(见 CANON [N])。

---

## 1d. CANON [L]:合 main 自动化(verified → 自动合;只有验证失败才升人类)

**`merge-main` 不是人类决策 barrier。** worker 到 `verified`(review 已 pass + 契约级独立复跑通过、非 worker 自报)后,协调器**自动**合 main、无需人类:串行地(一次一个,避免 main 竞态)rebase 到最新 main → 重跑验证 → **绿则 `git merge`(state→merged)** → 终态一并回收。happy path 零决策、零弹窗。

**冲突和验证回归都自动修,不升人类**(修复者是该线程的 worker 或 fixer subagent——都在 worktree 里改代码,不违反 CANON [J];协调器只指派,绝不亲手改):

- **rebase 有冲突** → 协调器先用 `inject-steer.sh` 把冲突任务**驭回该线程活着的 worker**(会话保活到 merged,CANON [B] 修订)resolve + 重跑该线程 VERIFICATION,循环到绿再合。冲突是"活",不是"决策"。
- **rebase 后重跑验证失败**(regression)→ 同样先驭回 worker 修到绿(是 bug,不是决策);worker 已亡或修不动 → 派 fixer subagent(该 worktree 内,范围锁),再不行由 relaunch 的修复 worker 接手(仍是 worktree 隔离)。
- budget / 尝试次数耗尽仍未收敛 → 协调器按 relaunch 续跑(受总预算上限),**仍不弹窗**。

**唯一残留升人类的**:解决过程中暴露的**真·跨模型分歧**(reviewer 对照 ground truth 裁不动)——走 `model-divergence`,**不是 merge 决策**。即 `merge-main` 作为人类 barrier **实质退役**:冲突不再是决策。

合 main 是**本地 merge**(可 `git revert`,分支保留到 `merged`),安全性由跨模型 review + 契约级独立复跑 + 合并后重跑验证兜住,不靠人类签字。**推送到对外 / 共享远端仍属 `outward-irreversible`,仍是人类 barrier**(合本地 main ≠ 对外)。

于是运行期真正升人类的 barrier 收敛为 **`outward-irreversible` + `model-divergence`**(+ `seal-contract` 前置当契约需人定、+ `merge-main` 仅上面两种失败时)。自动合入的事实进每-tick 摘要;可选 post 一个 `status=resolved` 的 merge 记录项供人类事后可见,但**不阻断**。

---

## 1e. CANON [N]:决策项真停其线程(Esc 软停、沉默不是同意)

CANON [K] 管"**怎么问人**"(只走异步决策面、绝不弹窗);**本条管"入队之后那条线程怎么办"**。两者互补——缺了 [N],[K] 会被误读成"永不等人"(已观测:协调器把 fail-open 的"没答就按默认走"套到 barrier 上,发明"知悉未异议→推进"、"异步 veto"、"协调器对 barrier 自裁 RULING",其中一次导致 688 行产物返工)。

**入队一个决策项(`post-decision`)⟺ 那条线程真的停下等人:**

- **该线程 `blocked-human` + Esc 软停其 codex**:协调器对该 pane 注入单个 Esc(`interrupt-target.sh`)——**codex 进程存活、`/goal` 上下文完整,只停在途 turn**;**绝不 reap session、绝不 relaunch**(reap 会丢掉整个 goal 推理态,relaunch 等于从头理解契约)。软停后的 worker idle 在 tmux 里等,占用极小、不算 runaway。**注意与终态区分**:回收在 `merged` / `rolled-back` 才一并执行(CANON [B] 修订,`verified` 也保留会话);`blocked-human`(活没干完、等你裁)只软停、不 reap。
- **沉默 = 继续等,不是同意**:决策项在人类**明确** resolve 前一直 `open`,该线程一直停。**禁止的越权话术**(fail-closed,写进 resolution 即违规):① "人类知悉未异议 → 按默认推进"(沉默当同意);② "采纳推荐默认"而无人类明确批准;③ "异步 veto 窗口 / 先执行后否决"(把需人批的动作先做了再留否决窗口);④ 协调器对 `outward-irreversible` / `model-divergence` 标记项自行 "RULING / 裁定"。`resolve-decision` 的 `resolution` **必须能指向一次真实的人类动作**(主 session 对话里的明确回复 / 口头指示 / 人类亲自跑 CLI `resolve-decision`),否则不得 resolve。
- **只停该线程,其余照跑**:靠 CANON [M] 的 ready 池——[K] 不弹窗、[M] 有别的活可推、[N] 该线程真停,三者互补才让"停得起"。
- **resolve 后同一会话续跑**:人类裁决后,协调器用 `inject-steer.sh` 把裁决注入**同一个** pane,软停的 codex 带完整上下文继续,**零重启、零上下文丢失**;线程从 `blocked-human` 回 `running`。

**二分(消灭"自裁式 barrier"第三态):** 一个"点"要么对照契约 ground truth **能判 → 就地裁、不入队**(arbiter / 协调器裁,只在 tick 摘要留痕),要么 **判不动 → 入队 + 该线程 [N] 真停 + 等人**。**没有"入队了又自己裁"的中间态**:标了 `outward-irreversible` / `model-divergence` 就意味着判不动、必等人;若其实能判,就别入队、直接裁。reviewer 的 `verdict=escalate` 语义是"我裁不动",它只能流向人类,**不能被协调器替换成自己的 RULING**。

**seal-contract 修订同理**:改变"什么算合格交付"的契约修订(outcome / verification / boundaries / blocked_stop / ABI / tier 层级)= 真决策,**重封(人批)前 worker 不得越过被改动的边界**,不许"先执行、开 veto 窗口"。纯机械对齐(引用行号 re-pin、base rebase 收敛)不改验收语义,不算决策、不入队。

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
5. 人类只在这条队列上异步工作:**裁决路径 = 在主 session 对 tick 摘要直接回复**——协调器每 tick 摘要把全部 open 决策项完整列出(id、`summary` / `divergence`、`options` 编号、`recommendation`、`evidence` 路径),人在对话里回一句(选编号或自由文本)即完成裁决,协调器执行 **`board.py resolve-decision --id <did> --resolution <text> [--status resolved|rejected]` 写回**。要翻细节时用 board-tui 的 Decisions 视图**只读查看**(`v` 键在内置 pager 里打开 evidence artifact,`Enter` 看完整记录)——TUI 零写路径,不做裁决(CANON [R])。`board.py` 是 `decisions.jsonl` 的唯一写入者,**绝不手编 `decisions.jsonl`**(手编易写坏行,而读回严格 fail-closed,一行坏整个决策面读回失败)——**前进不等人**。
6. 人类回复由协调器经 `board.py resolve-decision` 写回看板;下一个心跳 tick 读回 `list-decisions` 时落地对应 thread(落地两条路见第 5 节)。

`outward-irreversible` 硬闸不经 reviewer 裁决,由协调器遇不可逆动作时直接组装决策项 post 到 `decisions.jsonl`;`merge-main` **实质退役**——冲突 / 验证回归都走 worker-first 修复链自动修(不违反 CANON [J]),happy path 与失败路径均不入队,仅解决中暴露真·跨模型分歧才走 `model-divergence`(见 CANON [L]);seal-contract 在流程开头由 plan-tree 交叉评审 + 人封产生。

---

## 5. 裁决怎么落地:扩展(relaunch)而非回滚

人类对某项的回复落成 `resolution`(协调器经 `board.py resolve-decision` 写回,见第 4 节第 5 步)后,协调器在下个 tick 读回(`board.py list-decisions --open` 把已 resolved 项收敛掉),按裁决落地对应 thread。**裁决落地是「扩展」还是「回滚」由人类选的 `option` 决定**,两条路都不手编 JSONL——thread 状态 / `attempt` / `codex_session` 经 `board.py upsert-thread` 写,decision 状态经 `board.py resolve-decision` 写。

- **扩展(relaunch)**:人类选了一个让线程**继续推进**的 option(如第 3 节示例里的「排队(贴合不丢请求)」或「补契约后重跑」)。此时协调器**复用该线程现有 worktree + 分支(不重建 worktree)**,起一个**全新 codex 会话**,注入更新后的已封契约;通过 `board.py upsert-thread` 把该线程 `state` 置回 `running`、`attempt` 加一、`codex_session` 更新为新 rollout id。这是扩展,**不是回滚**——既有进度、分支与 worktree 全部保留(relaunch 操作细节见 `coordinator.md`)。
- **回滚(rollback)**:人类裁决放弃该线程时才走 `rolled-back` 终态(回收 worktree + 分支)。扩展决策**不得**误落成回滚。

`blocked-human` 线程在裁决落地后即离开该状态:扩展 → `running`,回滚 → `rolled-back`。
