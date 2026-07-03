# 任务契约:通用 worker(generic)

curryflows 不再按 archetype 分模板。每个长跑 worker 由**一份通用任务契约 + 强 /goal 契约**驱动。
把本文件 copy 到项目里填写,经 plan-tree 交叉评审 + 人封(seal-contract barrier)后,由协调器交给
operator 注入给 codex worker(见 `references/operator-spec.md`、`references/goal-contract.md`)。

> 缺任一必填字段,seal-contract 不予封定,worker 不许启动。

## 封定与落点

本文件 copy 到项目里填写;填好并封定后的副本落在 `<project>/.curryflows/contracts/<thread-id>.md`,
`threads.jsonl` 的 `contract` 字段指向该副本。封定 = 过**两道门**:①`board.py validate-contract --file
<path>`(fail-closed:8 个必填字段 `outcome` / `verification` / `constraints` / `boundaries` /
`iteration` / `budget` / `blocked_stop` / `preconditions` 齐且非空);②`precondition-dryrun.sh` 在
throwaway worktree 上真跑 `preconditions` 检查全过(CANON [O])。两门都过则 seal;否则不予封定、worker
不许启动。

## 必填字段(契约)

以下 8 个字段是 `board.py validate-contract` 的 fail-closed 门(缺任一即不予封定);`summary` 另列,
见下。

- `outcome`:工作完成时什么应为真(可度量的终态)。
- `verification`:证明它的**证据面**——独立可跑的测试 / benchmark / GOLD oracle 对照 / 复现步骤 /
  报告产物 / 命令输出。reviewer 用它做独立复验,**不接受 worker 自述**。凡涉及"独立复跑 / independent
  replay"的验证,**必须字面写死 L3 动作**(抹掉 venv + 删构建产物 `.so` + clean rebuild + 亲自跑),
  并要求 reviewer 报告实际达到的独立性档位——`committed → verified` 只认 L3(CANON [P],见
  `reviewer-spec.md`)。
- `preconditions`:本契约的验证 / GATE 所**依赖的环境前提**,写成**可执行 shell 检查**的列表(每项在
  base-ref 的 fresh worktree 上须 exit 0;用 `$WT` 指代该 worktree 路径)。典型:baseline 测试计数为绿、
  venv / 工具链可安装、预期 drift 形态。seal 前由 seal-gate 在一个 throwaway worktree 上**真跑一遍**
  (`scripts/precondition-dryrun.sh`),任一不成立即不予封定(CANON [O])。**这是把"契约假设了一个从没在
  真实环境验证过的前提"挡在 seal 前、而不是等 worker STEP-0 撞墙的关键**。
- `boundaries`:允许改动的范围(文件 / 目录 / 模块);其余一律不碰。
- `constraints`:工作期间什么不得回退(如:正确性套件不得变红、不改公共 API 行为)。
- `iteration`:每轮尝试后 worker 如何决定下一步试什么(每轮记录:试了什么、证据如何、下一步)。
- `budget`:硬上限(token 或轮次)。到顶 = 停 + 总结进展与 blocker,**不是完成**。
- `blocked_stop`:在当前限制下无可辩护路径时,worker 何时停下报告、什么能解锁。

其中 7 个(outcome / verification / constraints / boundaries / iteration / budget / blocked_stop)与
`goal-contract.md` 的 /goal 七字段强契约一一对应,是 worker **执行期**的目标契约;`preconditions` 是第 8 个
字段,不进 /goal 执行、而是 **seal 前**在 fresh worktree 上 dry-run 的环境门(CANON [O])。**缺任一即
不予封定、worker 不许启动**(`board.py validate-contract` 校验的正是这 8 个)。`budget` 与 `blocked_stop`
是防跑飞的两条硬规则;`preconditions` 是防"环境前提未验证"的门。

- `summary`:这个 worker 要达成什么,一句话**人类可读**的描述。它是给人看的标题,**不进
  `validate-contract` 的 fail-closed 门**(校验只看上面 8 个);仍建议填,便于看板与决策面识别。

## 可选字段

- `oracle`:若任务是"对照权威参考核验 / 反推规则",在此声明真值源(参考实现 / 规格文档 /
  黄金样本 / 不变量 / 第二独立方法 / 已知 good 基线)+ 容差。reviewer 据此独立复验。
- `minimal_diff`:`{ max_files: <int>, max_lines: <int> }`。修 bug 建议收紧,防 scope creep。
- `acceptance`:额外验收点(自然语言),供 reviewer 对照。

## 由 coordinator 填写(config)

- `skillDir`:curryflows skill 安装路径(scripts 所在)。
- `projectDir`:项目仓路径。
- `worktree`:本 worker 的独立 worktree 路径。
- `branch`:本 worker 的分支名(`curryflows/<thread-id>`)。
- `codex_effort`(默认 medium)。

## 示例

```yaml
summary: "average() 对空列表返回 0 而不是抛 ZeroDivisionError"
outcome: "空列表输入下 average() 返回 0,非空输入行为不变"
verification: "L3 独立复跑:rm -rf .venv && python -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m pytest tests/test_average.py -q 全绿(含新增空列表 RED 用例);reviewer 报告达到 L3"
preconditions:
  - "cd $WT && python -m venv .venv-cfx && .venv-cfx/bin/pip install -e . >/dev/null 2>&1"
  - "cd $WT && .venv-cfx/bin/python -m pytest tests/ -q"   # baseline 全绿是本契约 GATE 的前提
boundaries: "只改 src/stats.py;不动 tests/ 既有用例"
budget: "上限 300000 tokens。到顶即停 + 总结,不视为完成"
blocked_stop: "若空列表语义无契约依据,停下并 post 决策项,不擅自定义"
constraints: "tests/ 既有用例不得变红"
iteration: "每轮记录:改了什么、pytest 输出如何、下一步试什么"
minimal_diff: { max_files: 2, max_lines: 40 }
```

> `preconditions` 的每一项都会在 seal 前被 `precondition-dryrun.sh` 在一个 base-ref 的 throwaway
> worktree 上真跑(`$WT` = 该 worktree 路径),任一 exit≠0 即不予封定(CANON [O])。若某前提无法写成
> fresh-worktree 上的可执行检查(例如"GATE 只允许时间戳漂移"在 per-worktree 隔离下构造性不可满足),
> 那是 GATE 本身 ill-posed 的信号——应在 scoping 阶段修 GATE,而不是把不可验证的前提偷偷带进 worker。

## 修 bug 的用法

bug 修复 = reproduce-first:契约的 `verification` 必须含一个先写的 RED 复现用例(由协调器强制:无
RED 用例不予封定),worker 拟合到绿,`minimal_diff` 收紧。

## 对照权威参考核验的用法(原 ISS 那类)

填 `oracle`(真值源 + 容差):worker 先从 oracle 推导期望行为,再修差异;reviewer **独立**从同一
oracle 重新核验背离集,不信 worker 自述。oracle 是参数,契约本身不含任何领域专用词。
