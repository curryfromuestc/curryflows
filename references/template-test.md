# template-test:构建测试模板

`workflows/test-gen.js`(`meta.name = 'curryflows-test-gen'`)。产出一个**独立的黑盒测试套件**,作为 feature 与 perf 模板验证时所对照的 oracle。测试针对**公共契约**而非实现内部。脚本只编排,所有动手发生在 spawn 的 agent 内部。

## 它解决什么

构建可信的黑盒 oracle。测试只测公共契约 / 可观察行为,不读内部以免 over-fit。本模板的关键门是 test 特有的硬停——**有效性负控**:在隔离 worktree 注入一个故障后,至少一个新测试必须失败,以此证明套件不是恒真(trivially-true)。通过该套件成为下游 feature / perf 模板的 oracle。

## contract 字段(precheck fail-closed 校验)

`args.contract`(代码中 `K`)。precheck 对以下字段逐个校验,缺失即抛 `precheck failed (fail-closed): contract missing required field "<f>"`:

- `summary` — 必填。任务描述。
- `test_command` — 必填。运行测试套件(带 coverage)的命令;inspect / generate / validate / 负控均用它。
- `coverage_target` — 必填。覆盖率目标。
- `gap_scope` — 必填。缺口范围;inspect 在此范围内找未覆盖行为,generate 在此范围内补测试,负控在此范围内注入故障。
- `boundaries` — 必填。边界约束;传给各 lane,并在 pre-archive guard 作越界判据。

config 同样 fail-closed:`if (!C.worktree || !C.branch || !C.skillDir)` 缺失即抛 `precheck failed: config requires worktree, branch, skillDir`。

## 流程(produce 中段)

### inspect 覆盖率缺口

`phase('inspect')`,`agentType: 'Explore'`,`PRODUCE_SCHEMA`。在 `C.worktree` 跑 `test_command` 带 coverage,识别 `gap_scope` 内未覆盖行为,缺口报告写 `<worktree>/.curryflows/gap-report.md`,回报 `{ filesChanged:[], summary (优先级排序的缺口) }`。

### generate 一次

`phase('generate')`,`agentType: 'general-purpose'`,`PRODUCE_SCHEMA`。**只运行一次**(run ONCE)。按 `gap-report.md` 编写黑盒测试关闭 `gap_scope` 缺口;测公共契约 / 可观察行为,不读私有内部 over-fit;测试须可被 `test_command` 运行;**只改测试文件,不改被测代码**。

## 它特有的门(硬停:有效性负控)

`phase('meaningfulness')`,`agentType: 'general-purpose'`、`isolation: 'worktree'`、`NEGCTRL_SCHEMA`。在一个全新的隔离 worktree 里,向 `gap_scope` 内的被测代码注入**一个**合理故障(如翻转比较、off-by-one、删掉一个 guard),再跑 `test_command`。**至少一个新生成的测试必须失败**,以此证明套件非恒真。回报 `{ faultInjected, aTestFailed, whichFailed, summary }`,并要求:未真正观察到失败不得报 `aTestFailed=true`。

该硬停的结果 `aTestFailed` 进入接受判据与 archive gate。

## 验证面

`validate` lane(`agentType: 'Explore'`,`VALIDATE_SCHEMA`)在 `C.worktree` 跑 `(cd <worktree> && test_command)` 带 coverage:**新测试在当前(假定正确的)代码上必须 PASS**。完整输出写 `validate-r<round>.log`,回报 `{ exitCode, passed (exit==0), coverage (gap_scope 内百分比,如可得), evidencePath, tail }`。验证面 = 测试套件 + 覆盖率,外加上面的负控。

cross-review 让 codex || claude 两腿独立评测试质量:是否黑盒(不 over-fit 内部)、是否关闭声明的缺口、是否有恒真 / 同义反复断言、是否 over-fit。

## 接受判据

arbiter(`VERDICT_SCHEMA`)按 ground truth(contract + validation + 负控)而非投票裁决。verdict 洗白器推导 route:
```
route = escalate 非空 -> 'escalate'
      : accept===true && v.passed===true && neg.aTestFailed===true && fixes 空 -> 'accept'
      : 'repair'
```
接受要求:**arbiter accept 且 validation passed 且负控 `aTestFailed===true` 且 fixes 空且 escalate 空**。

最终:
```
archiveOk = accepted && lastValidation.passed===true && lastNeg.aTestFailed===true
          && guard.filesChanged>0 && guard.rollbackCovered===true
          && guard.fabricationSignals.length===0
```
pre-archive guard(`GUARD_SCHEMA`)检测 `fabricationSignals`(检测项:是否只改了测试文件 / 是否动了被测代码 / 恒真测试 / 越界 beyond boundaries)、`filesChanged`、`rollbackCovered`。

## loop 重入点

bounded loop `while (round < MAX_ROUNDS && !accepted)`,每轮:validate → meaningfulness(负控)→ cross-review(codex || claude → arbiter)。route==='accept' 则 `accepted=true; break`;route==='escalate' 则 `break`;否则进入 `phase('generate')` 的 `repair-tests:r<round>` lane——**不重新生成,只精修**现有测试(per arbiter fixes),保持黑盒、只改测试文件,然后回循环顶部重新 validate。重入点 = repair-tests lane → 下一轮 validate(负控每轮重跑)。

## 返回结构

```
{
  template: 'test',
  status,                 // 'ready-to-merge' | 'blocked-human' | 'failed'
  branch, worktree, rounds,
  gaps,                   // inspect 缺口报告结果
  gen,                    // generate 一次性产出
  validation,             // 最后一轮 validate
  negativeControl,        // 最后一轮负控(lastNeg)
  verdict,                // 最后一轮 arbiter verdict
  guard,
  escalations,
}
```

status 派生:`archiveOk ? 'ready-to-merge' : escalations.length ? 'blocked-human' : 'failed'`。

## config 字段

`args.config`(代码中 `C`):

- `skillDir` — 必填(fail-closed)。`codex-review.sh` 脚本路径前缀。
- `projectDir` — 项目目录。
- `worktree` — 必填(fail-closed)。线程 worktree;证据落 `<worktree>/.curryflows`(`EV`)。负控另在隔离 worktree 注入故障。
- `branch` — 必填(fail-closed)。线程分支。
- `maxRounds` — 默认 `3`(`MAX_ROUNDS`)。
- `codexEffort` — 默认 `'medium'`(`EFFORT`),传给 `codex-review.sh --effort`。

## 它产出的套件成为下游 oracle

archive 通过(测试通过 + 负控 `aTestFailed==true` + 真 diff + rollback 覆盖)的套件,即 feature 模板 `validation_command` 与 perf 模板 `validation_command`(正确性)所对照的独立黑盒 oracle。
