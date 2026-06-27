# template-perf:性能优化模板

`workflows/perf-opt.js`(`meta.name = 'curryflows-perf-opt'`)。在多个竞争策略之间搜索一个**有实测依据的提速**,前提是**不回退正确性**。脚本只编排,所有动手发生在 spawn 的 agent 内部。

## 它解决什么

性能优化的核心约束写进一个 perf 特有的硬停(正确性-vs-速度):任何提速只有在正确性套件保持 green 的前提下才被接受。多策略各自在隔离 worktree 里跑,回传 patch + 指标,胜者被应用到线程 worktree,再走标准 validate / cross-review / archive。目标:绝不接受牺牲正确性的提速,并防 benchmark gaming(测错对象)。

## contract 字段(precheck fail-closed 校验)

`args.contract`(代码中 `K`)。precheck 对以下字段逐个校验,缺失即抛 `precheck failed (fail-closed): contract missing required field "<f>"`;`strategies` 额外要求非空(`f === 'strategies' && !K.strategies.length` 同样抛错):

- `summary` — 必填。优化描述。
- `benchmark_command` — 必填。基准测量命令,baseline / 策略 lane / validate 都跑它。
- `target_metric` — 必填。被测指标名;agent 回报的 `metric` 即该指标的数值。
- `validation_command` — 必填。**正确性套件**命令(与速度无关的正确性判据)。
- `strategies` — 必填且非空,字符串数组(形如 `"name: how"`)。每个元素扇出一条策略 lane。
- `lower_is_better` — 可选,默认 `true`(`K.lower_is_better !== false`)。指标方向语义;为 `false` 时表示越大越好。

config 同样 fail-closed:`if (!C.worktree || !C.branch || !C.skillDir)` 缺失即抛 `precheck failed: config requires worktree, branch, skillDir`。

## 流程(produce 中段)

### baseline(必须正确性 green)

`phase('baseline')`,`agentType: 'Explore'`,`METRIC_SCHEMA`。在 `C.worktree` 跑 `benchmark_command` 与 `validation_command`,原始输出写 `<worktree>/.curryflows/baseline.log`,回报 `{ metric, raw, correctnessPassed }`。

硬性 precheck:`if (baseline.correctnessPassed !== true)` 抛 `precheck failed (fail-closed): correctness suite is not green at baseline; fix correctness before optimizing`。基线不正确就不允许优化。

### parallel 策略 lane(各自 worktree 隔离)

`phase('search')`,`parallel(K.strategies.map(...))`。每条 lane `agentType: 'general-purpose'`、`isolation: 'worktree'`、`CAND_SCHEMA`。策略 lane 改的是同一份代码,所以各自跑在独立 worktree 避免互相 clobber,只回传 patch + 指标:`{ strategy, metric, correctnessPassed, patch (git diff 文本), summary, failed? }`。策略不可行时返回 `metric` 等于 baseline、`failed:true` 并在 summary 解释,**不准编造提速**。

## 它特有的门(硬停:正确性-vs-速度)

策略搜索后的 pre-apply 硬停:
```
lowerIsBetter = K.lower_is_better !== false
eligible = candidates.filter(c =>
  c.correctnessPassed === true && !c.failed &&
  (lowerIsBetter ? c.metric < baseline.metric : c.metric > baseline.metric))
```
只有**正确性 green 且确实 beat baseline** 的候选 eligible。`if (eligible.length === 0)` 直接 return 整体 `status: 'failed'`(reason: `no correctness-preserving speedup found`)。eligible 按方向排序取 `winner = eligible[0]`。

### apply 胜者 patch 到 thread worktree

`phase('apply')`,`agentType: 'general-purpose'`。把 `winner.patch` 应用到 `C.worktree` 并在 `C.branch` 提交;patch 应用不干净时按同一策略手工重实现以匹配。

## 验证面

`validate` lane(`agentType: 'Explore'`)同时跑正确性套件 `(cd <worktree> && validation_command)` 与重跑 `benchmark_command`,完整输出写 `validate-r<round>.log`,回报 `{ exitCode, passed, evidencePath, tail }`,其中 **`passed` = 正确性 exit==0 AND metric 仍 beat baseline**。验证面 = benchmark + 正确性套件。

pre-archive guard(`GUARD_SCHEMA`)回报 `fabricationSignals`(检测项含 benchmark gaming、correctness suite weakened、out-of-scope files beyond boundaries)、`filesChanged`、`linesChanged`、`rollbackCovered`。

## 接受判据

arbiter(`VERDICT_SCHEMA`)按 ground truth(contract + benchmark + 正确性结果)而非投票裁决。cross-review 显式让两腿找:correctness-breaking shortcuts、benchmark gaming、undefined behavior、hidden regressions。verdict 洗白器推导 route:
```
route = escalate 非空 -> 'escalate'
      : accept===true && v.passed===true && fixes 空 -> 'accept'
      : 'repair'
```
接受要求 arbiter accept 且 validation passed(正确性 green 且仍 beat baseline)且 fixes 空且 escalate 空。

最终 `archiveOk = accepted && lastValidation.passed===true && guard.filesChanged>0 && guard.rollbackCovered===true && guard.fabricationSignals.length===0`。

## loop 重入点

bounded loop `while (round < MAX_ROUNDS && !accepted)`,每轮 validate → cross-review(codex || claude → arbiter)。route==='accept' 则 `accepted=true; break`;route==='escalate' 则 `break`;否则进入 `phase('apply')` 的 `repair:r<round>` lane,只应用 `verdict.fixes`,要求保持 diff 最小、同时保住提速与正确性,然后回循环顶部重新 validate。重入点 = repair lane → 下一轮 validate。

## 返回结构

```
{
  template: 'perf',
  status,                 // 'ready-to-merge' | 'blocked-human' | 'failed'
  branch, worktree, rounds,
  baseline, winner, candidates,
  validation,             // 最后一轮 validate
  verdict,                // 最后一轮 arbiter verdict
  guard,
  escalations,
}
```

硬停未命中(`eligible.length === 0`)时提前 return:`{ template:'perf', status:'failed', branch, worktree, baseline, candidates, escalations:[], reason:'no correctness-preserving speedup found' }`。

正常路径 status 派生:`archiveOk ? 'ready-to-merge' : escalations.length ? 'blocked-human' : 'failed'`。

## config 字段

`args.config`(代码中 `C`):

- `skillDir` — 必填(fail-closed)。`codex-review.sh` 脚本路径前缀。
- `projectDir` — 项目目录。
- `worktree` — 必填(fail-closed)。线程 worktree;证据落 `<worktree>/.curryflows`(`EV`)。注意:策略 lane 另在各自隔离 worktree 里跑。
- `branch` — 必填(fail-closed)。线程分支;apply 在此分支提交。
- `maxRounds` — 默认 `3`(`MAX_ROUNDS`)。
- `codexEffort` — 默认 `'medium'`(`EFFORT`),传给 `codex-review.sh --effort`。
