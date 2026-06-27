# curryflows 基座 kernel 与门清单

一句话定位:三个模板(`feature-impl.js` / `perf-opt.js` / `test-gen.js`)是三个独立 JS 文件、门逻辑各自内联,但它们跑的是**同一套门**——这份文档逐门讲清这套共享门,并标注每门在三个文件里的实现位置(函数 / 变量名)。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`base-kernel-gates.md` — 共享 kernel 与全部门。

---

## 0. 「共享门、内联实现」是什么意思

`workflows/` 下没有 `base-kernel.js` 这个单独文件;三个模板各自把同一套门内联在自己的脚本里。它们共享:同一组结构化 lane 类型(produce / validate / cross-review / arbiter / repair)、同一组 JSON schema 形状、同一套 verdict 洗白与 archive 判定逻辑。差异只在 produce 中段与各自的硬停 gate(见门 7)。下面把这套门按执行顺序逐个讲。

基座门标准顺序(出自 `SKILL.md`):

```
契约 fail-closed precheck → produce →(parallel → guarding join)→ 验证真跑+证据
→ 跨模型 review → verdict 洗白器 → bounded loop(fallback 偏继续 + max-rounds)
→ 硬停 pre-execution gate → pre-archive 反捏造/越界 + minimal-diff
→ archive(验证过 + 真 diff + rollback)
```

---

## 门 1：契约 fail-closed precheck

进 produce 前对契约逐字段校验,**缺任一必填字段直接 `throw`**(fail-closed:契约不全就拒绝开工,而不是带缺口往下跑)。同时校验 `config` 必须有 `worktree` / `branch` / `skillDir`。

实现位置(每个文件都是 `phase('precheck')` 后的 `for (const f of [...])` 循环):

| 文件 | 必填契约字段(循环里的列表) | 额外 config 校验 |
|---|---|---|
| `feature-impl.js` | `summary`, `validation_command`, `boundaries` | `C.worktree && C.branch && C.skillDir` |
| `perf-opt.js` | `summary`, `benchmark_command`, `target_metric`, `validation_command`, `strategies`(且非空) | 同上 |
| `test-gen.js` | `summary`, `test_command`, `coverage_target`, `gap_scope`, `boundaries` | 同上 |

错误信息统一带 `precheck failed (fail-closed):` 前缀。

---

## 门 2：produce(中段,模板各异)

在 precheck 之后产出候选改动。这是三个模板唯一形状不同的地方,但都遵守「只在 worktree 内动手、不越界」。

| 文件 | produce 形状 | 关键约束 |
|---|---|---|
| `feature-impl.js` | `const produce = await parallel([... produce:core, produce:docs ...])` | core lane 拟合**独立黑盒测试套件**(`K.validation_command`),不许改测试;docs lane 不许改代码或测试 |
| `perf-opt.js` | `phase('baseline')` 先真跑基线(若基线正确性不绿直接 `throw`)→ `phase('search')` `parallel(K.strategies.map(...))` 每个 strategy 跑在 `isolation:'worktree'` 各返回 `{metric, correctnessPassed, patch}` → `phase('apply')` `apply-winner` 把胜者 patch 落到 thread worktree | strategy 不可行须返回 `failed:true` 且 `metric` 等于基线,**严禁捏造 speedup** |
| `test-gen.js` | `phase('inspect')` `inspect-coverage` 找覆盖缺口 → `phase('generate')` `generate-tests`(**只生成一次**)写黑盒测试 | 测试针对公开契约,不读私有内部过拟合;只改测试文件 |

produce lane 用 `agentType:'general-purpose'`(有写权限);只读的 inspect / baseline 用 `Explore`。`parallel(...)` 是 guarding join:并发 lane 全部完成后才汇合进下一步。

---

## 门 3：run-validation(真跑 + 证据)

在 bounded loop 内每轮第一步 `phase('validate')`,**真跑**验证命令、**捕获 exit code 与完整输出落盘**、返回 `passed` 与证据路径。绝不靠声明,必须有 checked-in 证据。

实现位置:`while` 循环内 `const v = await agent('VALIDATION lane ...', {label:`validate:r${round}`, agentType:'Explore', schema: VALIDATE_SCHEMA})`。

| 文件 | 真跑命令 | `passed` 判据 | 证据落盘 |
|---|---|---|---|
| `feature-impl.js` | `(cd ${C.worktree} && ${K.validation_command})` | `exitCode==0` | `${EV}/validate-r${round}.log` |
| `perf-opt.js` | 正确性套件 + 重跑 benchmark | 正确性 `exit==0` **且** metric 仍胜过 baseline | `${EV}/validate-r${round}.log` |
| `test-gen.js` | `(cd ${C.worktree} && ${K.test_command})` 带 coverage | `exit==0`(新测试在当前代码上须通过) | `${EV}/validate-r${round}.log` |

`EV` 定义为 `const EV = \`${C.worktree}/.curryflows\``(三个文件一致)。`VALIDATE_SCHEMA` 强制 `exitCode` / `passed` / `evidencePath` 为必填,无证据路径无法返回。

---

## 门 4：跨模型 cross-review(codex 腿 || Claude 腿 → arbiter)

`phase('cross-review')`,两腿**并发独立** review,再交 arbiter 对照 ground truth 裁,不投票。

实现位置:`const [codexF, claudeF] = await parallel([...])` 后接 `const verdict = await agent('ARBITER ...')`。

- **codex 腿**(`label: xreview:codex:r${round}`,`Explore`):写 prompt 到 `${EV}/xreview-codex-prompt-r${round}.md` → 跑 `bash ${C.skillDir}/scripts/codex-review.sh --cwd ... --out ${EV}/xreview-codex-r${round}.md --effort ${EFFORT} --timeout 900` → 读 findings。脚本非零退出返回 `{reviewer:'codex', failed:true, findings:[]}`,**严禁捏造 findings**。
- **Claude 腿**(`label: xreview:claude:r${round}`,`Explore`):独立读 `git -C ${C.worktree} diff` 对照契约产出 findings,不编辑。
- **arbiter**(`label: arbiter:r${round}`,`Explore`):输入 `codexF` + `claudeF` + `v`(validation),对照 ground truth(契约 + validation;perf 加 benchmark,test 加 negative control)裁——两方都报且可判→fix;仅一方报→对照 ground truth 判;裁不动→escalate。

各腿关注点按模板调整:feature 找 correctness bug / 契约偏离;perf 找正确性捷径、benchmark gaming、隐藏回归;test 找过拟合内部、缺口未闭合、trivially-true 断言。

`FINDINGS_SCHEMA` / `VERDICT_SCHEMA` 三个文件结构一致;`EFFORT = C.codexEffort || 'medium'`。

---

## 门 5：verdict 洗白器(`deriveRoute`)

**绝不只信 arbiter 一句话 `accept`。** 拿到 verdict 后在脚本本体(确定性代码,不在 agent 里)重新推导路由:必须 **validation 通过 + 无 fixes + 无 escalate** 才走 `accept`。这道门把「模型说 accept」与「客观可归档」解耦。

实现位置:`const route = ...` 三元表达式(每个文件 bounded loop 内,arbiter 之后):

```js
const route =
  (verdict.escalate && verdict.escalate.length) ? 'escalate'
  : (verdict.accept === true && v.passed === true && (!verdict.fixes || verdict.fixes.length === 0)) ? 'accept'
  : 'repair'
```

模板加强条件:

| 文件 | `accept` 额外条件 |
|---|---|
| `feature-impl.js` | 上述基础三条 |
| `perf-opt.js` | `v.passed` 已含「正确性绿 **且** 仍胜基线」 |
| `test-gen.js` | 额外要求 `neg.aTestFailed === true`(negative control 必须真失败过一个测试) |

escalate 总是优先:`if (verdict.escalate && verdict.escalate.length) escalations.push(...verdict.escalate)`,且 `route==='escalate'` 时 `break` 交协调器出决策项。这也解释了 fallback 偏继续——只要没明确 accept,路由落到 `repair` 继续修,而不是误判成功。

---

## 门 6：bounded loop(max-rounds)

整个 validate → (negctrl) → cross-review → verdict → repair 包在 `while (round < MAX_ROUNDS && !accepted)` 里,**有界**,不会无限循环。

实现位置:`const MAX_ROUNDS = C.maxRounds || 3`(默认 3);循环体头部 `round++`;`route==='accept'` 时 `accepted = true; break`,`route==='escalate'` 时 `break`,否则末尾跑 repair lane 后进下一轮。

| 文件 | repair lane label | repair 约束 |
|---|---|---|
| `feature-impl.js` | `repair:r${round}` | 只应用 arbiter `fixes`,保持 diff 最小,不碰无关文件 |
| `perf-opt.js` | `repair:r${round}` | 只应用 fixes,保留 speedup **且** correctness |
| `test-gen.js` | `repair-tests:r${round}` | **不重新生成**,只精修现有测试,保持黑盒,只改测试文件 |

---

## 门 7：硬停 pre-execution gate(模板特有的正确性闸)

在「正式推进」之前的一道硬停,各模板按其风险点定制;不满足直接早退(`return` 一个 `failed` 结果,不进后续门)。

| 文件 | 硬停内容 | 实现位置 |
|---|---|---|
| `perf-opt.js` | **正确性-vs-速度**:候选只有在「正确性仍绿 **且** 真的胜过 baseline」时才 eligible;无 eligible 直接 `return {status:'failed', reason:'no correctness-preserving speedup found'}` | `const eligible = candidates.filter(c => c.correctnessPassed===true && !c.failed && (lowerIsBetter ? c.metric<baseline.metric : c.metric>baseline.metric))`;`if (eligible.length===0) { ... return ... }`;另有 baseline 处的 `if (baseline.correctnessPassed !== true) throw` |
| `test-gen.js` | **有效性负控(negative control)**:在隔离 worktree 注入一个故障,新测试**必须至少失败一个**,证明套件不是 trivially-true;`neg.aTestFailed` 进 accept 判据 | `phase('meaningfulness')` 的 `const neg = await agent('MEANINGFULNESS NEGATIVE CONTROL ...', {isolation:'worktree', schema: NEGCTRL_SCHEMA})`;prompt 强制「未真观察到失败不得报 `aTestFailed=true`」 |
| `feature-impl.js` | 无独立硬停 gate(其正确性闸即独立黑盒套件本身,由门 3 真跑守住) | — |

---

## 门 8：pre-archive guard + minimal-diff

`phase('archive')` 第一步,只读检查**真 diff**,报告反捏造信号 / 越界 / diff 规模 / rollback 覆盖。这是入库前的反捏造与越界闸。

实现位置:`const guard = await agent('PRE-ARCHIVE GUARD ...', {label:'pre-archive-guard', agentType:'Explore', schema: GUARD_SCHEMA})`。

guard 报告字段(`GUARD_SCHEMA`):
- `fabricationSignals`(数组):如「无真实代码改动」「低语义填充」「越界文件(超出 `${K.boundaries}`)」「过早产出最终 artifact」「不持久的证据引用」;perf 额外含 benchmark gaming / 正确性套件被削弱;test 额外含「改了被测代码」「trivially-true 测试」。
- `filesChanged` / `linesChanged`(int)。
- `rollbackCovered`:除非 worktree/branch 外有改动(即 drop 分支能完全回退),否则 `true`。

**minimal-diff** 仅 `feature-impl.js` 有显式判定:`GUARD_SCHEMA` 多一个 `withinMinimalDiff` 字段,由 guard 对照契约 `K.minimal_diff` 算:
```js
const mdMaxFiles = (K.minimal_diff && K.minimal_diff.max_files) || 999999
const mdMaxLines = (K.minimal_diff && K.minimal_diff.max_lines) || 999999
// withinMinimalDiff = filesChanged <= mdMaxFiles AND linesChanged <= mdMaxLines
```
perf 与 test 的 guard prompt 含越界检查但不带 `withinMinimalDiff` 字段。

---

## 门 9：archive gate(fail-closed)

最终入库判定,**纯确定性代码**(不在 agent 里),全部条件 AND,任一不满足即非 `ready-to-merge`。这是 fail-closed 的最后一闸:accepted + validation 通过 + 真 diff + rollback 覆盖 + 无 fabrication 信号(feature 再加 minimal-diff 内,test 再加 negctrl 真失败过)。

实现位置:`const archiveOk = ...`,随后 `const status = archiveOk ? 'ready-to-merge' : escalations.length ? 'blocked-human' : 'failed'`。

| 文件 | `archiveOk` 全部 AND 条件 |
|---|---|
| `feature-impl.js` | `accepted && lastValidation.passed===true && guard.filesChanged>0 && guard.rollbackCovered===true && guard.withinMinimalDiff===true && guard.fabricationSignals.length===0` |
| `perf-opt.js` | `accepted && lastValidation.passed===true && guard.filesChanged>0 && guard.rollbackCovered===true && guard.fabricationSignals.length===0` |
| `test-gen.js` | `accepted && lastValidation.passed===true && lastNeg.aTestFailed===true && guard.filesChanged>0 && guard.rollbackCovered===true && guard.fabricationSignals.length===0` |

每个模板最终 `return { template, status, branch, worktree, rounds, ..., escalations }`。`status` 决定后续:`ready-to-merge` 进合 main barrier;`blocked-human` 表示有 escalations 待人类决策;`failed` 表示未达标且无可升项。`escalations` 由协调器 post 成决策项(见 `decision-surface.md`)。
