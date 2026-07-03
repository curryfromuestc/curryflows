# operator subagent 契约

operator 是 curryflows tick 的第三步(审核 → 决策 → **操作**)。协调器据 reviewer 裁决决策后,
派**一个** operator subagent(opus,可改)一次性执行本 tick 的所有写动作:起 / 驭 / commit / 回收 /
relaunch codex worker。operator 只执行协调器已定的决策,不自作主张。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`operator-spec.md` — operator subagent
> 契约:起/驭/回收 tmux/codex、资源生命周期、detach、回传。

## 角色与边界

- **agentType**:`general-purpose`(读写 + Bash + tmux + git)。
- **模型**:opus。
- **只执行决策**:operator 拿到的是协调器定好的动作清单(起哪些 worker、驭哪些、回收哪些、relaunch
  哪些),不重新审产物、不重新决策。
- **对目标 codex 的写只有两类**:Escape(软停)和人类裁决后注入的指令,其余全只读。
- **唯一驱动器**:`inject-steer.sh`(注入)/ `interrupt-target.sh`(软停)。**绝不手搓
  `tmux send-keys`** 操作 codex 输入框(TUI 渲染时序 racy,会静默丢消息,见 `codex-integration.md`)。

## 五类动作

### 1) 起新 worker(detach,长跑不随 operator 退出而死)

对协调器标记的 `state=ready` 线程:

```bash
# 建独立分支 + worktree;base 由协调器按 CANON [M] 定:独立切片=启动时的 main,
# 真依赖切片=依赖线程的 committed 分支(不等其 merged)
git -C <projectDir> worktree add -b curryflows/<thread-id> \
  ~/.cache/curryflows/worktrees/<project>/<thread-id> <base-ref>
# 在 detached tmux 里起一个普通 shell pane
tmux new-session -d -s cfx_<thread-id> -c <worktree>
# 该 pane 此刻还是普通 shell:用 send-keys 在其上启动 codex 二进制(CANON [F] 允许);
# 思考强度一律最高档(CANON [H])
tmux send-keys -t cfx_<thread-id> 'codex -c model_reasoning_effort=xhigh' Enter
# TUI 起来后改走 inject-steer.sh,注入已封契约(经文件传入);此后绝不再手搓 send-keys
bash <skillDir>/scripts/inject-steer.sh send cfx_<thread-id> \
  <projectDir>/.curryflows/contracts/<thread-id>.md
```

**关键:codex /goal 跑在 detached tmux 里,tmux server 常驻;operator 起完它就回传退出,长跑线程
归 tmux + 看板所有,绝不随 operator subagent 的生命周期结束而死。** 起完立即拿到 rollout 的
session-id 回传给协调器,协调器写回看板(见 `goal-contract.md`「启动后立即注册」),否则下个 tick
的 reviewer 会把它标成 `UNREGISTERED`。

**send-keys 纠正(CANON [F]):** 刚由 `tmux new-session` 起出的 pane 是普通 shell,所以用
`tmux send-keys`(或 `tmux new-session "<cmd>"`)在该 shell pane 上**启动 codex 二进制是允许的**;
但 codex TUI 一旦渲染起来,之后所有输入必须走 `inject-steer.sh`(Escape 走 `interrupt-target.sh`)
——**绝不对 live codex TUI 手搓 raw `tmux send-keys`**(TUI 渲染时序 racy,消息被静默丢弃)。worker
启动注入的目标契约取自已封副本 `<project>/.curryflows/contracts/<thread-id>.md`(`task-contracts/task.md`
填好后由 `seal-contract` 封存,见 `goal-contract.md` / CANON [D]);`threads.jsonl` 的 `contract`
字段指向它。

**codex 启动纪律(CANON [H],fail-closed)**:上面这条 tmux 路径是 operator 起 codex 的**唯一**允许方式。
**禁用** `codex exec`(headless,SSH 断连即带走进程、在途进度丢失、无法重连)、codex 插件命令
(`codex:rescue` / `codex:review` / `codex:adversarial-review`)、以及任何 companion / 远端 CLI 代理
(在 headless 之上再加一跳网关,502 / 限流即整段失败、零产物——已观测)。只有走 tmux 才能断连重连不丢
进度、且可被 subagent 监控(见 `codex-integration.md`)。起 codex **一律最高思考强度**:启动命令显式带
`-c model_reasoning_effort=xhigh`(当前 CLI 最高档),不依赖宿主 `~/.codex/config.toml`。

### 2) 驭在途 worker

- **注入人类裁决后的指令**:`inject-steer.sh send <pane> <prompt-file>`(文本经文件传入,CJK /
  引号 / 换行不被二次解析)。
- **软停**:`interrupt-target.sh <pane>` 发单个 Escape——codex 进程存活、goal 上下文完整,只停
  在途 turn,供人类 review 后再指示。

**决策点软停(CANON [N]):线程命中真·决策点被置 `blocked-human` 时,operator 对其 codex 注入 Esc 软停
(`interrupt-target.sh`)——不是回收。** codex 进程存活、`/goal` 上下文完整,idle 在 tmux 里等人类裁决,
占用极小。**绝不对 `blocked-human` 线程 reap session / relaunch**(reap 会丢整个 goal 推理态)。人类 resolve
后,协调器派 operator 用 `inject-steer.sh` 把裁决注入**同一个** pane,codex 带完整上下文续跑,线程回
`running`——零重启、零上下文丢失。**与 `verified` 区分**:`verified`(活已干完、等合)才 reap session
(CANON [B] 分阶段,见下 §4);`blocked-human`(活没干完、等裁)只软停不 reap。

### 3) commit worker 工作到自有分支(durability,`reviewed → committed`)

reviewer 审完(线程到 `reviewed`)后,协调器决策把该 worker 的工作 commit 到它**自己的分支**
(durability,**非 merge、非 push、绝不碰 main**)。codex worker 通常只把改动留在工作树、不自行 commit,
所以这一步由 operator 在该 worktree 内执行:

```bash
bash <skillDir>/scripts/interrupt-target.sh <pane>   # 若 codex 仍在 TUI idle,先软停
git -C <worktree> add -A                              # 只在该 worktree 内,绝不靠近 main
git -C <worktree> commit -m "curryflows <thread-id>: <一句话>"
git -C <worktree> rev-parse HEAD                      # 回传 commit sha
```

commit 完把 `{thread_id, branch, commit}` 回传;协调器用 `board.py upsert-thread --state committed` 置位。
这一步是 reviewer 在 `committed → verified` 做独立复跑、以及分阶段 reap(CANON [B])的前提——没有 commit
就没有可供独立复验的稳定快照。

### 3b) 自动合 main(`verified` → `merged`,CANON [L])

线程到 `verified`(reviewer 独立复跑通过)后,协调器把**自动合 main**交给 operator——**无需人类决策**
(CANON [L])。**串行**(一次一个,避免 main 竞态):

```bash
git -C <worktree> rebase <main-ref>                          # rebase 到最新 main
# 重跑该线程契约的 VERIFICATION,**L3 独立**(CANON [P]):抹掉 venv + 删构建产物(.so)+ clean
# rebuild + 亲自跑(不复用 worker 已建的 .so、不经 worker 的 wrapper);绿才继续
git -C <main-checkout> merge --no-ff curryflows/<thread-id>  # 合入本地 main(可 revert)
```

- **绿则合**:rebase 干净 + 重跑验证通过 → `git merge` → 回传;协调器置 `state=merged`(随后分阶段 reap
  worktree + 分支)。
- **冲突 / 回归自动修,不升人类**:rebase 有冲突 → operator 在该 worktree 内**直接解决冲突** + 重跑验证,
  循环到绿再合;重跑验证失败(regression)→ 同样修到绿(是 bug、不是决策);单个 operator 搞不定的大改由
  协调器派一个 codex 修复 worker 接手(仍 worktree 隔离)。**只有解决中暴露真·跨模型分歧才回传 escalate**
  (协调器走 `model-divergence`,非 merge 决策);budget / 尝试次数耗尽未收敛 → 协调器 relaunch 续跑,**不弹窗**。
- 合的是**本地 main**(可 `git revert`);**推送到对外 / 共享远端不在此列——那属 `outward-irreversible`,
  仍是人类 barrier**。回传:`merged` 数组 `{thread_id, branch, merged_into, commit}`,失败进 `failures`。

### 4) 回收资源(用完即删,硬职责)

对协调器标记的可回收集(跑完 / 孤儿,由 reviewer 经 `discover-threads.py` 对账得出),**逐个资源**
调 `reap.sh`,以便每个资源的退出码可独立归属到回传 `reaped[].exit_code`:

```bash
bash <skillDir>/scripts/reap.sh --session <tmux-session> --project <projectDir>   # kind=session
bash <skillDir>/scripts/reap.sh --worktree <path>       --project <projectDir>   # kind=worktree
bash <skillDir>/scripts/reap.sh --branch <name>         --project <projectDir>   # kind=branch
```

**分阶段 reap(CANON [B]):** 回收按线程 state 分两阶段,绝不一次性删光:

- `state=verified`(已 commit 到自己分支 + 在 committed 分支 worktree 上独立复跑过,待人类 merge):
  **只**调 `reap.sh --session <tmux-session>` reap 掉 codex tmux 会话、释放进程,**保留 worktree +
  分支**;线程随后置 `session-reaped`。
- `state=merged`(已合 main)或 `state=rolled-back`(已丢弃):此时才调 `reap.sh --worktree <path>`
  与 `reap.sh --branch <name>` reap 掉 worktree + 分支。

每个资源仍**逐个**调 `reap.sh` 单独执行,退出码各自归属到回传 `reaped[].exit_code`(schema 不变)。

`reap.sh` 做:`tmux kill-session`(若存在)/ `git worktree remove --force` + `git worktree prune` /
`git branch -D <name>`(force;护栏:拒删 main/master/当前分支)。**回收是每 tick 硬职责,不指望任何
收尾钩子**——这正是补上"清理是死代码、资源无限堆积"的坑。**回收前该资源必须已被协调器判为可回收**
(session 阶段:线程已到 `verified`;worktree + 分支阶段:人类已裁决 `merged` / `rolled-back`);
operator 不擅自回收在途 worker,也不自行判断
分支是否已合入——是否回收由上游决策定。

### 5) relaunch / 扩展在途线程(复用 worktree + 分支,起新会话)

人类决策扩展某线程后(在已封契约上追加 / 改写产出范围,经 `seal-contract` 前置校验重封),协调器把该
线程交给 operator relaunch。**复用现有 worktree + 分支,绝不重建 worktree**;在该 worktree 上起一个
全新 codex 会话,注入更新后的已封契约:

```bash
# 不动 worktree / 分支,只在原 worktree 上起新 detached 会话(send-keys 规则同 CANON [F])
tmux new-session -d -s cfx_<thread-id> -c <worktree>
tmux send-keys -t cfx_<thread-id> 'codex -c model_reasoning_effort=xhigh' Enter   # shell pane 启动 codex(允许);最高思考强度(CANON [H])
# TUI 起来后改走 inject-steer.sh,注入扩展后的已封契约;此后绝不再手搓 send-keys
bash <skillDir>/scripts/inject-steer.sh send cfx_<thread-id> \
  <projectDir>/.curryflows/contracts/<thread-id>.md
```

起完拿到新 rollout 的 session-id 回传协调器。协调器随后用 `board.py upsert-thread` 把 state 置回
`running`、`attempt` 加一、`codex_session` 更新为新 rollout id(看板写入只走 board.py,见
`board.md` / CANON [C][E])。relaunch 的结果按 `launched` 数组同形回传:`branch` / `worktree` 为复用
的原值,`codex_session` / `tmux_session` 为新会话值。

## 回传 schema

operator 回一个对象给协调器(协调器据此写回看板):

| 字段 | 类型 | 说明 |
|---|---|---|
| `launched` | array | 本 tick 起的 worker:`{thread_id, codex_session, branch, worktree, tmux_session}` |
| `steered` | array | 本 tick 驭的 worker:`{thread_id, action: inject\|interrupt, ref}` |
| `committed` | array | 本 tick commit 的线程:`{thread_id, branch, commit}` |
| `merged` | array | 本 tick 自动合入 main 的线程(CANON [L]):`{thread_id, branch, merged_into, commit}` |
| `reaped` | array | 本 tick 回收的资源:`{kind: session\|worktree\|branch, ref, exit_code}` |
| `failures` | array | 失败的动作 + 退出码 + 原因(**如实回报,不绿洗**) |

任何脚本非零退出都进 `failures` 如实回报,不得当成功。
