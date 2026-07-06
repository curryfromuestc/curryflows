# 操作规程(协调器内联执行)

操作是 curryflows tick 的第 4 步(审核 → 决策 → **操作**)。自 CANON [Q] 起,例行操作由
**协调器在主 session 内联执行**——300K 压缩窗口 + no-slurp 纪律使内联安全,
且省掉一次 subagent 冷启动往返(速度)。本文件是这些操作的权威规程:命令、护栏、回传字段。

> 历史沿革:这些动作曾由一个独立的 operator subagent 执行;`ticks.jsonl` 记录里的 `operator`
> 字段名保留不变。**唯一仍必须外派的执行体**是 fixer subagent(见下「fixer subagent 契约」)
> ——因为它要改代码,而协调器绝不亲手改码(CANON [J])。
>
> 在 `SKILL.md` 的 references 索引中,本文件登记为:`operator-spec.md` — 操作规程:
> 起/驭/commit/合 main/回收 codex worker 的命令与护栏、fixer 契约、回传字段。

## 边界与纪律

- **只执行已定的决策**:操作步执行的是决策步(tick 第 3 步)定下的动作清单,不在执行中重新审
  产物、重新决策。
- **对目标 codex 的写只有两类**:Escape(软停)和人类裁决后注入的指令,其余全只读。
- **唯一驱动器**:`inject-steer.sh`(注入)/ `interrupt-target.sh`(软停)。**绝不手搓
  `tmux send-keys`** 操作 codex 输入框(TUI 渲染时序 racy,会静默丢消息,CANON [F];在普通
  shell pane 上用 send-keys 启动 codex 二进制是允许的)。
- **绝不亲手改码(CANON [J])**:协调器不 Edit/Write 源码/测试/脚本、不自己调试;合并冲突与
  验证回归走「worker-first 修复链」(见 §3b)。
- **no-slurp**:任何构建/测试/rebase 输出 `> file 2>&1`,只读 exit code,失败才 `tail -50`;
  大 diff 归 review-panel Workflow。

## 五类动作

### 1) 起新 worker(detach,长跑不随本 tick 结束而死)

对决策步选定的 `sealed-ready` 任务 / `state=ready` 线程:

```bash
# 建独立分支 + worktree;base 由 CANON [M2] 定:独立切片=启动时的 main,
# 真依赖切片=依赖线程的 committed 分支(不等其 merged)
git -C <projectDir> worktree add -b curryflows/<thread-id> \
  ~/.cache/curryflows/worktrees/<project>/<thread-id> <base-ref>
# 在 detached tmux 里起一个普通 shell pane
tmux new-session -d -s cfx_<thread-id> -c <worktree>
# 该 pane 此刻还是普通 shell:用 send-keys 启动 codex 二进制(CANON [F] 允许);
# 思考强度一律最高档(CANON [H])
tmux send-keys -t cfx_<thread-id> 'codex -c model_reasoning_effort=xhigh' Enter
# TUI 起来后改走 inject-steer.sh,注入已封契约(经文件传入);此后绝不再手搓 send-keys
bash <skillDir>/scripts/inject-steer.sh send cfx_<thread-id> \
  <projectDir>/.curryflows/contracts/<thread-id>.md
```

**关键**:codex /goal 跑在 detached tmux 里,tmux server 常驻,长跑线程归 tmux + 看板所有,
与协调器的 tick 生命周期完全解耦。起完**当场**拿 rollout session-id 写回看板
(`board.py upsert-thread --codex-session …`)——上下文随时可能被压缩,没有"稍后补登"可言,
不登记即下 tick 被 reviewer 标 `UNREGISTERED`(见 `goal-contract.md`「启动后立即注册」)。

**起前必过两道 seal 门**(CANON [O],顺序执行,任一不过不得起):
①`board.py validate-contract --file <contract>`(8 必填字段,fail-closed);
②派一个 subagent 跑 `precondition-dryrun.sh --project . --base <base-ref> --contract <c>`,
在 throwaway worktree 上真跑 `preconditions`,`all_ok` 才封。

**codex 启动纪律(CANON [H],fail-closed)**:上述 tmux 路径是起 codex 的**唯一**允许方式。
**禁用** `codex exec`(headless,断连即丢)、codex 插件命令、companion / 远端 CLI 代理
(网关 502 即零产物,已观测)。启动命令**显式**带 `-c model_reasoning_effort=xhigh`,
不依赖宿主配置。

### 2) 驭在途 worker

- **注入指令**(人类裁决落地 / 冲突修复任务):`inject-steer.sh send <pane> <prompt-file>`
  (文本经文件传入,CJK / 引号 / 换行不被二次解析)。
- **软停**:`interrupt-target.sh <pane>` 发单个 Escape——codex 进程存活、goal 上下文完整,
  只停在途 turn。

**决策点软停(CANON [N])**:线程被置 `blocked-human` 时,对其 codex 注入 Esc 软停——**不是
回收**。软停的 worker idle 在 tmux 里等人类裁决,占用极小。**绝不对 `blocked-human` 线程
reap session / relaunch**(reap 丢掉整个 goal 推理态)。人类 resolve 后把裁决注入**同一个**
pane,codex 带完整上下文续跑,线程回 `running`——零重启、零上下文丢失。

### 3) commit worker 工作到自有分支(durability,`reviewed → committed`)

codex worker 通常只把改动留在工作树,由协调器在该 worktree 内 commit(非 merge、非 push、
绝不碰 main):

```bash
bash <skillDir>/scripts/interrupt-target.sh <pane>   # 若 codex 仍在 TUI idle,先软停
git -C <worktree> add -A
git -C <worktree> commit -m "curryflows <thread-id>: <一句话>"
git -C <worktree> rev-parse HEAD                      # 记录 commit sha
```

commit 后 `board.py upsert-thread --state committed`。这是 reviewer 做 `committed → verified`
L3 独立复验、以及后续合 main 的前提——没有 commit 就没有稳定快照。

### 3b) 自动合 main(`verified → merged`,CANON [L],串行)

线程到 `verified`(review-panel L3 独立复跑通过)后**自动合、无需人类**。串行,一次一个,
避免 main 竞态:

```bash
git -C <worktree> rebase <main-ref> > <worktree>/.curryflows/rebase.log 2>&1
# 重跑该线程契约的 VERIFICATION,L3 独立(CANON [P]):抹 venv + 删构建产物 + clean rebuild +
# 亲自跑;输出重定向文件,只读 exit code,失败 tail -50
git -C <main-checkout> merge --no-ff curryflows/<thread-id>   # 合入本地 main(可 revert)
```

- **绿则合** → `board.py upsert-thread --state merged` → 进入 §4 回收。
- **冲突 / 验证回归 —— worker-first 修复链(协调器绝不亲手改码,CANON [J])**:
  1. **驭回原 worker**:`inject-steer.sh` 把冲突/回归任务注入**该线程还活着的 codex**
     (会话按 CANON [B] 修订保活到 merged)——它带着分支的全部推理上下文,是最快的修复者;
  2. worker 已亡 / 修不动 → **派 fixer subagent**(见下契约)在该 worktree 内修到绿;
  3. 仍不收敛(budget / 尝试次数耗尽)→ relaunch 续跑,**不弹窗**。
  唯修复中暴露**真·跨模型分歧**才升 `model-divergence`(不是 merge 决策)。
- 合的是**本地 main**;**推送对外 / 共享远端属 `outward-irreversible`,仍是人类 barrier**。

### 4) 回收资源(CANON [B] 修订:全部推迟到终态)

- `state=merged` / `rolled-back` 后,**逐个资源**调 `reap.sh`,一并回收
  session + worktree + branch(退出码各自归属):

  ```bash
  bash <skillDir>/scripts/reap.sh --session <tmux-session> --project <projectDir>
  bash <skillDir>/scripts/reap.sh --worktree <path>       --project <projectDir>
  bash <skillDir>/scripts/reap.sh --branch <name>         --project <projectDir>
  ```

- **`verified` 阶段保留 codex 会话**(idle 占用极小):这是 §3b worker-first 修复链的前提——
  提前 reap 会话,冲突就没人可驭回。`session-reaped` 状态保留在枚举,常规流不再经过。
- **`blocked-human` 绝不 reap**(与 `verified` 区分:前者活没干完等裁,后者等合)。
- 回收是每 tick 硬职责,不指望收尾钩子;可回收集来自 review-panel 的资源对账
  (`discover-threads.py`),**回收前该资源必须已被决策步判为可回收**。

### 5) relaunch / 扩展在途线程

复用现有 worktree + 分支(**绝不重建**),起全新 codex 会话,注入更新后的已封契约(命令同
§1 的 tmux 三步);`upsert-thread` 置回 `running`、`attempt` 加一、`codex_session` 更新。

## fixer subagent 契约(唯一被授权改码的临时执行体)

worker-first 修不动时,协调器派**一个** fixer subagent(agentType `general-purpose`,opus 级):

- **范围锁**:只在**该线程的 worktree** 内工作,只修那个冲突 / 那个回归,不顺手重构、不碰
  别的文件;绝不靠近 main 树。
- **完成判据**:该线程契约的 VERIFICATION 重跑到绿(输出落 `${worktree}/.curryflows/` 证据
  文件),回传 `{thread_id, commit, evidence_path, summary}`。
- **失败如实回报**:修不到绿就报 blocker + 已试路径,协调器走 relaunch 或升级,fixer 不得
  扩大范围硬修。
- fixer 的大上下文(diff / 构建日志)随它消亡,协调器只收结论。

## 回传字段(`ticks.jsonl` 记录的 `operator` 段)

操作步的结果记进本 tick `record-tick` 数据文件的 `operator` 字段(字段名与历史 schema 兼容):

| 字段 | 类型 | 说明 |
|---|---|---|
| `launched` | array | 本 tick 起的 worker:`{thread_id, codex_session, branch, worktree, tmux_session}` |
| `steered` | array | 本 tick 驭的 worker:`{thread_id, action: inject\|interrupt, ref}` |
| `committed` | array | 本 tick commit 的线程:`{thread_id, branch, commit}` |
| `merged` | array | 本 tick 自动合入 main 的线程(CANON [L]):`{thread_id, branch, merged_into, commit}` |
| `reaped` | array | 本 tick 回收的资源:`{kind: session\|worktree\|branch, ref, exit_code}` |
| `failures` | array | 失败的动作 + 退出码 + 原因(**如实回报,不绿洗**) |

任何脚本非零退出都进 `failures` 如实记录,不得当成功。
