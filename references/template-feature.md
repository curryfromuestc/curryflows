# template-feature:功能实现模板

`workflows/feature-impl.js`(`meta.name = 'curryflows-feature-impl'`)。在隔离的 branch + worktree 上实现一个功能,目标是让一个**独立的黑盒测试套件**通过。本模板**不自写测试**:被拟合的套件来自别的来源(典型为 test-gen 模板的产出),在本模板里被当作 spec。脚本只编排,所有动手(编辑 / bash / 驱动 codex)发生在它 spawn 的 agent 内部。

## 它解决什么

把"实现功能"约束成"让一个外部黑盒套件从红变绿"。测试与实现分离是黑盒保证:实现 lane 被显式禁止改测试,只能改源码,因此 `validation_command` 的通过结果不会被实现者污染,可作为独立判据。未被跨模型 review settle 的真分歧不阻塞脚本,而是收进 `escalations` 列表回传给协调器,由协调器 post 成人类决策项。

## contract 字段(precheck fail-closed 校验)

`args.contract`(代码中 `K`)。precheck 阶段对以下字段逐个校验,缺失即抛错 `precheck failed (fail-closed): contract missing required field "<f>"`:

- `summary` — 必填。功能描述,传给 core 与 docs lane。
- `validation_command` — 必填。独立黑盒套件的命令。实现 lane 用它当 spec,validate lane 用 `(cd <worktree> && <validation_command>)` 真跑。
- `boundaries` — 必填。边界约束;传给所有 lane,并在 pre-archive guard 里作为越界文件判据。
- `acceptance` — 可选。接受标准描述(schema 中标为可选,precheck 不强制)。
- `minimal_diff` — 可选,对象 `{ max_files, max_lines }`。给 archive 阶段的 minimal-diff 门用;缺省时上限取 `999999`(等于不约束)。

config 同样 fail-closed:`if (!C.worktree || !C.branch || !C.skillDir)` 任一缺失即抛 `precheck failed (fail-closed): config requires worktree, branch, skillDir`。

## produce 中段

`phase('produce')`,`parallel([core, docs])` 两条并发 lane,均 `agentType: 'general-purpose'`,均限定只在 `C.worktree` 内工作,schema 为 `PRODUCE_SCHEMA`(`{ filesChanged, summary }`):

- `produce:core` — 实现 `summary`,使套件 `validation_command` 通过。prompt 显式声明"你没写这个套件,把它当 spec";"只改源码,不准改测试套件"。
- `produce:docs` — 为 `summary` 编写 / 更新文档;"不准改代码或测试"。

两条 lane 都不写测试。黑盒套件是别的来源、当 spec。

## 它特有的门

基座标准门之外,本模板特有的是 **minimal-diff 门**(可选,bug-fix 模式收紧)。archive 阶段读取 `K.minimal_diff.max_files` / `max_lines`(缺省 `999999`),pre-archive guard 回报 `withinMinimalDiff = (filesChanged <= mdMaxFiles AND linesChanged <= mdMaxLines)`,该布尔进入 archive gate。修 bug 时通过收紧 `minimal_diff` 上限,限制改动范围。

GUARD_SCHEMA 还要求 `fabricationSignals`(数组)、`filesChanged`(int)、`linesChanged`(int)、`rollbackCovered`(bool)。`fabricationSignals` 检测项包括:no real code change、low-semantic padding、out-of-scope files beyond boundaries、premature final artifacts、nondurable evidence refs。

## 验证面

`validate` lane(`agentType: 'Explore'`,VALIDATE_SCHEMA)真跑 `(cd <worktree> && validation_command)`,捕获 exit code 与输出,把完整输出写到 `<worktree>/.curryflows/validate-r<round>.log`,回报 `{ exitCode, passed (exitCode==0), evidencePath, tail }`。验证面就是这个**独立黑盒套件**。

## 接受判据

arbiter(`VERDICT_SCHEMA`)按 ground truth 而非投票裁决,回报 `{ accept, fixes, escalate }`。verdict 洗白器不信任裸 `accept`,自行推导 route:

```
route = escalate 非空            -> 'escalate'
      : accept===true && v.passed===true && fixes 空 -> 'accept'
      : 'repair'
```

即接受要求:**arbiter accept 且 validation passed 且 fixes 空 且 escalate 空**。

最终 `archiveOk` 进一步要求:
```
accepted && lastValidation.passed===true && guard.filesChanged>0
&& guard.rollbackCovered===true && guard.withinMinimalDiff===true
&& guard.fabricationSignals.length===0
```

## loop 重入点

bounded loop `while (round < MAX_ROUNDS && !accepted)`,每轮:validate → cross-review(`xreview:codex` 与 `xreview:claude` 并发 → arbiter)。

- route==='accept':`accepted = true; break`。
- route==='escalate':`break`,交给协调器变决策项。
- 否则 route==='repair':进入 `phase('produce')` 的 `repair:r<round>` lane,只应用 arbiter 给出的 `verdict.fixes`,保持 diff 最小,然后回到循环顶部重新 validate。

重入点即 repair lane → 下一轮 validate。fallback 偏继续:无法判 accept / escalate 时默认 repair,直到 `MAX_ROUNDS`。

## 返回结构

```
{
  template: 'feature',
  status,                 // 'ready-to-merge' | 'blocked-human' | 'failed'
  branch, worktree, rounds,
  produce,                // parallel{core,docs} 结果
  validation,             // 最后一轮 validate
  verdict,                // 最后一轮 arbiter verdict
  guard,                  // pre-archive guard
  escalations,            // 未 settle 的真分歧;协调器 post 成人类决策项
}
```

status 派生:
```
status = archiveOk ? 'ready-to-merge'
       : escalations.length ? 'blocked-human'
       : 'failed'
```

## config 字段

`args.config`(代码中 `C`):

- `skillDir` — 必填(fail-closed)。skill 目录;codex-review.sh 脚本路径前缀。
- `projectDir` — 项目目录。
- `worktree` — 必填(fail-closed)。线程隔离 worktree;所有 lane 工作目录,证据落 `<worktree>/.curryflows`(`EV`)。
- `branch` — 必填(fail-closed)。线程分支。
- `maxRounds` — 默认 `3`(`MAX_ROUNDS`)。bounded loop 上限。
- `codexEffort` — 默认 `'medium'`(`EFFORT`)。传给 `codex-review.sh --effort`。

## 修 bug 的编排

修 bug = test-gen(加 RED 复现用例,reproduce-first 由 coordinator 强制)→ feature-impl(拟合到绿),minimal-diff 收紧。即先由 test-gen 产出一个在当前(有 bug 的)代码上会失败的复现用例并入黑盒套件,再用本模板把实现拟合到该套件通过;reproduce-first 是协调器层的强制,不是本脚本内逻辑。
