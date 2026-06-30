# 任务契约:通用 worker(generic)

curryflows 不再按 archetype 分模板。每个长跑 worker 由**一份通用任务契约 + 强 /goal 契约**驱动。
把本文件 copy 到项目里填写,经 plan-tree 交叉评审 + 人封(seal-contract barrier)后,由协调器交给
operator 注入给 codex worker(见 `references/operator-spec.md`、`references/goal-contract.md`)。

> 缺任一必填字段,seal-contract 不予封定,worker 不许启动。

## 封定与落点

本文件 copy 到项目里填写;填好并封定后的副本落在 `<project>/.curryflows/contracts/<thread-id>.md`,
`threads.jsonl` 的 `contract` 字段指向该副本。封定 = 通过 `board.py validate-contract --file <path>`
(fail-closed:7 个必填字段 `outcome` / `verification` / `constraints` / `boundaries` / `iteration` /
`budget` / `blocked_stop` 齐且非空)。校验有效则 exit 0;否则非零退出并打印缺失字段列表,seal-contract
不予封定、worker 不许启动。

## 必填字段(契约)

以下 7 个字段是 `board.py validate-contract` 的 fail-closed 门(缺任一即不予封定);`summary` 另列,
见下。

- `outcome`:工作完成时什么应为真(可度量的终态)。
- `verification`:证明它的**证据面**——独立可跑的测试 / benchmark / GOLD oracle 对照 / 复现步骤 /
  报告产物 / 命令输出。reviewer 用它做独立复验,**不接受 worker 自述**。
- `boundaries`:允许改动的范围(文件 / 目录 / 模块);其余一律不碰。
- `constraints`:工作期间什么不得回退(如:正确性套件不得变红、不改公共 API 行为)。
- `iteration`:每轮尝试后 worker 如何决定下一步试什么(每轮记录:试了什么、证据如何、下一步)。
- `budget`:硬上限(token 或轮次)。到顶 = 停 + 总结进展与 blocker,**不是完成**。
- `blocked_stop`:在当前限制下无可辩护路径时,worker 何时停下报告、什么能解锁。

这 7 个字段(outcome / verification / constraints / boundaries / iteration / budget / blocked_stop)
与 `goal-contract.md` 的 /goal 七字段强契约一一对应:**缺任一即 weak goal,seal-contract 不予封定、
worker 不许启动**(`board.py validate-contract` 校验的正是这 7 个)。`budget` 与 `blocked_stop` 是防
跑飞的两条硬规则。

- `summary`:这个 worker 要达成什么,一句话**人类可读**的描述。它是给人看的标题,**不进
  `validate-contract` 的 fail-closed 门**(校验只看上面 7 个);仍建议填,便于看板与决策面识别。

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
verification: "python -m pytest tests/test_average.py -q 全绿(含新增空列表 RED 用例)"
boundaries: "只改 src/stats.py;不动 tests/ 既有用例"
budget: "上限 300000 tokens。到顶即停 + 总结,不视为完成"
blocked_stop: "若空列表语义无契约依据,停下并 post 决策项,不擅自定义"
constraints: "tests/ 既有用例不得变红"
iteration: "每轮记录:改了什么、pytest 输出如何、下一步试什么"
minimal_diff: { max_files: 2, max_lines: 40 }
```

## 修 bug 的用法

bug 修复 = reproduce-first:契约的 `verification` 必须含一个先写的 RED 复现用例(由协调器强制:无
RED 用例不予封定),worker 拟合到绿,`minimal_diff` 收紧。

## 对照权威参考核验的用法(原 ISS 那类)

填 `oracle`(真值源 + 容差):worker 先从 oracle 推导期望行为,再修差异;reviewer **独立**从同一
oracle 重新核验背离集,不信 worker 自述。oracle 是参数,契约本身不含任何领域专用词。
