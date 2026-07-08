# curryflows

把人类 review 从构建关键路径上解耦的**通用工作流协调器 skill**。

一个 cron 心跳驱动、**持久上下文 + 受控压缩**的 `/loop` 协调器(CANON [Q])以"审核优先"并发推进多个在
tmux 里长跑的 codex `/goal` worker:每个 tick 先调官方 Workflow 跑 `workflows/review-panel.js`
(跨模型多 lens reviewer + arbiter 收敛,opus、只读)审产物 + 对账资源,协调器据裁决决策,**内联**
操作 tmux(起 / 驭 / commit / 合 main / 回收 codex;改码一律外派 worker / fixer subagent)。
worker 是 codex、reviewer 是 Claude,**天然跨模型**;裁决只回
一条清晰摘要给主 session,完整证据落 durable 看板,**人类异步看、异步决策,默认不阻断推进**——合 main
验证过即自动合,只在对外不可逆、跨模型真分歧才升人类。

上下文纪律:跨 tick 状态**只**信 durable 看板——协调器上下文持久但可被 auto-compact 有损重写
(建议 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000`),上下文里的历史仅供参考,每 tick 重新读回
看板对账;tick 内的巨型 transcript / diff 只进 Workflow / subagent 的隔离上下文,随之消亡,
协调器只收蒸馏结论。

两条与 `/loop` 解耦的硬纪律:**codex 启动纪律(CANON [H])**——任何 codex 调用只经 tmux 启动 + subagent
监控到完成,**禁用** codex 插件(`codex:rescue` 等)/ `codex exec` / companion CLI(断连 / 网关 502 即零
产物);inline 场景也照办。**启动 fail-open(CANON [I])**——主动问人类而无回答时默认就**起 `/loop`** 推进
+ 把问题挂决策面异步裁,绝不静默退回 inline、也不停下干等。

完整设计见 [`SKILL.md`](SKILL.md) 与 `references/`。

## 与 ultracode 官方 Workflow 的分工

开局同时挂 **ultracode + curryflows**,按任务动态切:

- **确定性 / 有界任务**(实现一批、评审 diff、研究、内层扇出 + 对抗验证)→ **ultracode 官方 Workflow,
  不用 curryflows**(原生更省事)。
- **非确定 / 长跑 / 跨会话 / 把人类 review 从关键路径解耦** → **curryflows**。

外层 `/loop` 定时心跳(session 级 cron,`/clear` 后仍按点触发)是 Claude Code 内置原语,curryflows
**不重造它**;其不可替代价值收窄为该心跳之上、Workflow 工具做不到的:**tmux 跨会话长跑的自驱 codex
/goal 群 + 防 runaway 对账 + durable 异步人类决策面 + worker 生命周期/终态回收**。判据:能在一次有界 episode 内跑完的交给 ultracode;要在
tmux 里长跑、跨会话、防跑飞、人类异步裁决的才是 curryflows。curryflows 自己的 review 步骤就是用官方
Workflow(`workflows/review-panel.js`)实现的。

## 安装

本仓是一个 Claude Code skill。安装方式 = `git clone` 进 skills 目录:

```sh
git clone <repo-url> ~/.claude/skills/curryflows
```

依赖(见 `SKILL.md` frontmatter `requires`):codex CLI(≥0.128,支持 `/goal`)、tmux、
git(≥2.5,worktree)、python3。

## 现在可跑

```sh
# 统一资源发现:所有在途 codex 会话 + 本项目的 curryflows worktree,与看板对账,
# 标出未注册的 runaway。只读。退出码 0=干净;2=有在途资源未在看板登记;64=用法错。
python3 scripts/discover-threads.py --project <项目repo> \
  --board <项目>/.curryflows/board/threads.jsonl

# 一行状态摘要(T0 常显面:用户 watch -n 15 挂小 pane / tmux status-right;只读):
python3 scripts/board.py summary --board <项目>/.curryflows/board
# → cfx ▶2 ⏸1 ⚑1 ◆3 | ready:1 | PAUSED  (首段恒在含零;再列非零的其余非终态计数;pause 存在缀 PAUSED)

# 全屏终端看板(T1 按需面:curses TUI,独立只读查看器;CANON [R]:纯只读、零写路径,
# 决策在主 session 对 tick 摘要直接回复、由协调器落 resolve-decision,关闭不影响推进):
ln -sf "$(pwd)/scripts/board-tui.py" ~/.local/bin/cfx-board   # 一次性建符号链接
cfx-board                                          # 任意目录:cwd 上溯找板,找不到列注册表挑选(注册表空 → exit 2 + 提示)
python3 scripts/board-tui.py --board <项目>/.curryflows/board                    # 显式指定
python3 scripts/board-tui.py --board <项目>/.curryflows/board --render threads   # 无 TTY 的单帧文本输出
python3 scripts/board-tui.py --render boards       # 无头列出看板注册表挑选表(无需 --board)

# 全局看板注册表(~/.cache/curryflows/boards.jsonl,env CURRYFLOWS_REGISTRY 可覆盖;
# board.py 的每个成功写子命令自动登记,只是索引不是真相):
python3 scripts/board.py boards                    # 只读 JSONL dump,每条补 "exists" 字段

# 看板 jsonl 的唯一写入者(原子 + 枚举/必填 fail-closed,绝不手编):注册/更新线程、post 决策、封定校验。
python3 scripts/board.py upsert-thread --board <项目>/.curryflows/board --id t1 --state running --codex-session <uuid>
python3 scripts/board.py validate-contract --file <项目>/.curryflows/contracts/t1.md   # validate-contract 不带 --board

# 任务补给队列(tick 常设补货步的落点;dedup_key 唯一 + rejected 必带理由,fail-closed):
python3 scripts/board.py upsert-backlog --board <项目>/.curryflows/board --id b1 --summary "..." --dedup-key <稳定键>
python3 scripts/board.py list-ticks --board <项目>/.curryflows/board --last 5   # 有界读 tick 历史,绝不整读

# 审核派发:从 threads.jsonl 生成 review-panel.js 的 args JSON(单一事实源,勿手拼 threads[]):
python3 scripts/board.py panel-args --board <项目>/.curryflows/board --threads <id1,id2>

# 回收用完的资源(协调器每 tick 的硬职责):终态一并、逐资源调用(CANON [B] 修订,见 operator-spec.md),带护栏。
bash scripts/reap.sh --session <tmux-session> --project <项目repo>          # merged/rolled-back:会话、worktree、分支一并回收
bash scripts/reap.sh --worktree <path>        --project <项目repo>
bash scripts/reap.sh --branch <name>          --project <项目repo>          # (拒删 main/当前分支)
```

## 三层控制流

1. **协调器(cron 心跳 + 持久上下文)= 外层调度**:薄,做推理、决策、内联机械操作、写看板;
   上下文可被 auto-compact 有损重写,每 tick 以看板读回对账开始(CANON [Q]);无事可做即 no-op 收 tick。
2. **内层有界动作**:每 tick 先调官方 Workflow 跑 `workflows/review-panel.js` 这个 review 面板(opus,
   只读)审产物 + 对账资源,协调器决策后**内联执行**起/驭/commit/合 main/回收;唯一仍外派的执行体是
   改码的 fixer subagent(合并冲突先驭回活 worker,见 `references/operator-spec.md`)。
3. **codex `/goal` = 自驱 worker**:真正干活的长跑线程,在 detached tmux 里跑,由强目标契约
   (budget + blocked-stop)+ 只读审计 + Esc 急停兜住。

## 目录

- `SKILL.md` — 主文档 + 双 frontmatter。
- `references/` — 中文设计文档(架构 / 协调器 tick runbook + tick prompt 模板 / reviewer 契约 /
  操作规程 / 综合看板 + 摘要 schema / codex 接入 / 强目标契约 / 决策面 / goal-cookbook)。
- `scripts/` — 英文代码,被 agent 调用:`discover-threads.py`(统一资源发现)、
  `board.py`(看板 jsonl 唯一写入者:upsert-thread / post-decision / resolve-decision /
  upsert-backlog / record-tick / list-* / summary / boards,原子 + 枚举/必填 fail-closed;
  成功写操作自动登记全局看板注册表)、
  `board-tui.py`(终端看板 TUI:四视图纯只读渲染、零写路径,`cfx-board` 无参启动 +
  cwd 上溯 / 注册表挑选;决策在主 session 回复,CANON [R])、
  `reap.sh`(资源回收,终态一并)、
  `inject-steer.sh` / `interrupt-target.sh` / `locate-codex.sh`(codex 的 tmux 驱动器)、
  `codex-review.sh`(codex 第二意见腿:worker=codex 时可选、worker=Claude 时必需)、
  `precondition-dryrun.sh`(seal 前 environment-precondition dry-run:在 throwaway worktree 上真跑契约
  声明的 `preconditions` 检查,fail-closed,CANON [O])。
- `workflows/` — 官方 Workflow 参考脚本:`review-panel.js`(内层 review 面板的官方 Workflow 参考脚本)。
- `task-contracts/` — `task.md` 通用任务契约骨架(给项目 copy 填写,经 seal-contract 人封)。

## 每个项目的运行态(不进本仓)

```
<project>/.curryflows/
  board/threads.jsonl       # 线程台账(资源对账的依据)
  board/decisions.jsonl     # 人类决策队列
  board/ticks.jsonl         # 每 tick 完整裁决(durable 历史)
  board/backlog.jsonl       # 任务补给队列(CANON [M];dedup + 拒绝记忆)
  contracts/<thread-id>.md  # 已封的每线程契约(threads.jsonl 的 contract 字段指向它)
  temp/                     # 监督日志 / 证据
```
worktree 默认落 `~/.cache/curryflows/worktrees/<project>/<thread-id>`(可配)。
