# 协调器 tick runbook(`/loop` 动态模式)

协调器是 curryflows 三层控制流的外层调度(见 `SKILL.md`、`architecture.md`)。它以 `/loop`
动态模式运行:每个 tick 以"审核优先"推进,无就绪事项时 park 释放上下文,被事件(线程完成、人类
回复、定时唤醒)重新拉起。这一层是 agent 推理,不是确定性脚本。

> 在 `SKILL.md` 的 references 索引中,本文件登记为:`coordinator.md` — coordinator tick
> runbook + `/loop` prompt。

## 唯一硬约束:协调器上下文不被撑爆

协调器自己**不读巨型 transcript / diff、不跑长脚本、不直接操作 tmux**。这些全外派给 subagent,
subagent 的大上下文随它消亡,协调器只收回蒸馏结论。协调器会 park、会被压缩、会跨多个唤醒周期
存活,所以它的真相源是 durable 看板文件,不是上下文。

## 状态落盘:看板是真相源

```
<project>/.curryflows/board/threads.jsonl    # 每行一个线程记录
<project>/.curryflows/board/decisions.jsonl  # 每行一个决策项
<project>/.curryflows/board/ticks.jsonl      # 每行一个 tick 的完整裁决(durable 历史)
<project>/.curryflows/board/dashboard.html     # render-board.py 渲染的综合看板(人类异步视图)
```

每个 tick 开始时,协调器从看板读回状态,而不是依赖上一个 tick 残留在上下文里的记忆;tick 结束时,
把状态变更写回看板。字段格式见 `board.md`。

## tick 五步:审核 → 决策 → 操作 → 写看板 → park

每个 tick 严格按以下顺序执行。

### 1) 审核(先并发派 N 个 reviewer subagent)

tick 第一件事是并发派多个 **reviewer subagent**(opus,只读,各取不同 lens)。它们的职责(完整
契约见 `reviewer-spec.md`):

- **对账资源真值**:跑 `python3 <skillDir>/scripts/discover-threads.py --project . --board
  ./.curryflows/board/threads.jsonl`,把所有在途 codex 会话 + worktree 与看板对账,标出
  `UNREGISTERED` / `RUNAWAY-SUSPECT` / 孤儿 worktree / 可回收(跑完)的资源。
- **审产物**:读在途 codex worker 的 transcript/diff,对照其目标契约审:是否 drift / 捏造 /
  假实现 / 越界 / 破坏不变量;是否撞到 barrier。
- **回一条清晰裁决**(含异议):**巨型 transcript 隔离在 reviewer 自己的上下文里,绝不进协调器**,
  只回裁决结论 + 指向 checked-in 证据的指针。

没有单独的 checker——巡检并入 reviewer。

### 2) 决策(协调器,薄)

协调器收齐多个 reviewer 的裁决后,就地推理决策(不读大文件):

- **收敛**:多裁决一致且依据可判 → 自动处理;真分歧 → 对照 ground truth 裁,**不投票**;裁不动 →
  组装成决策项 post 到 `decisions.jsonl`,对应线程置 `blocked-human`。
- **落地人类已回复的决策项**:对 `decisions.jsonl` 里已裁决项,定下要执行的操作(合 main / 回滚 /
  注入指令)。
- **处置 runaway**:reviewer 报的 `UNREGISTERED` / `RUNAWAY-SUSPECT` / 孤儿 worktree → 决定软停 +
  post 决策项;在 runaway 未被人类裁决前,本 tick 不扩张 codex 占用。
- **决定可回收集**:reviewer 报的跑完 / 孤儿资源 → 标记本 tick 由 operator 回收。
- **决定要不要起新 worker**:对 `state=ready` 的线程,定下分支 `curryflows/<thread-id>` + worktree +
  目标契约,交给 operator 起(尊重并发上限)。

barrier 共 4 个取值(见 `decision-surface.md`):运行期升人类的三类——合 main(串行:先 rebase 最新
main + 重跑验证)、对外不可逆、model-divergence;另有 seal-contract 在开头封定契约。**决策默认不阻断**:
就绪线程照推,人类决策异步进行。

### 3) 操作(后派 1 个 operator subagent)

把第 2 步定下的所有写动作交给一个 **operator subagent**(opus,可改)一次性执行(完整契约见
`operator-spec.md`):

- **起新 worker**:建分支 + worktree,在 detached tmux 里起 codex /goal(用 `inject-steer.sh`
  注入封定的目标契约),回传 rollout session-id;长跑线程归 tmux/看板所有,**不随 operator 退出而死**。
- **驭在途 worker**:`inject-steer.sh` 注入人类裁决后的指令,或 `interrupt-target.sh` 软停。
- **回收资源**:对可回收集跑 `reap.sh`(`tmux kill-session` + `git worktree prune`)。
- 回传:本 tick 起了/驭了/回收了什么 + 新 session-id。

### 4) 写看板 + 回摘要

- operator 回传后,协调器把状态变更写回 `threads.jsonl` / `decisions.jsonl`,把本 tick 的完整裁决
  追加到 `ticks.jsonl`。看板 HTML 由常驻的 serve-board 实时重渲染,协调器无需每 tick 再刷;需要离线
  快照时才跑 `render-board.py` 落一份 `dashboard.html`。
- 向主 session 回**一条清晰、不糊弄的摘要**(schema 见 `board.md`):每条线程状态/进展/预算余额、
  审核裁决含异议、未验证项/风险/越界、待人类决策项、本 tick 回收的资源。完整内容只给指针。

### 5) park 或 continue

- 若仍有就绪线程或待处理项 → 不 park,继续下一个 tick;
- 若无就绪事项 → arm 一个 Monitor(等线程完成 / 人类回复事件),并 ScheduleWakeup 在 1200–1800s 后
  再唤醒一次,然后停下释放上下文。被事件或定时唤醒后,从第 1 步重新开始。

## 监督拆分:谁读 transcript、谁动手、谁出廉价信号

- **深度审计 + 廉价信号 = reviewer subagent(只读 opus)**。transcript 可达几百 MB,reviewer 把它
  隔离在自己上下文里,只回裁决;`discover-threads.py`(只读首行 + 文件 stat)也在审核阶段顺手跑。
- **决策 = 协调器主 session**(薄,不读大文件)。
- **动手 = operator subagent(可改 opus)**。对目标 codex 的写只有两类:Escape(软停)和人类裁决后
  注入的指令,其余全只读。

## start:起协调器前的一次性步骤

`start` 操作在拉起 `/loop` 之前做两件一次性事:

1. 确保看板目录存在:`mkdir -p ./.curryflows/board`。
2. **后台拉起看板服务**(每次请求实时重渲染,人类浏览器看实时状态):

   ```bash
   nohup python3 <skillDir>/scripts/serve-board.py --board ./.curryflows/board \
     --port 8787 >./.curryflows/temp/serve-board.log 2>&1 &
   # → http://127.0.0.1:8787/(SSH 机器端口转发即可)
   ```

   serve-board 是只读的、独立于协调器生命周期的常驻进程;协调器每 tick 只写 jsonl,serve 端实时
   重渲染,无需协调器再单独刷 HTML。

## `/loop` prompt 文本范例

在目标项目根目录,以动态模式起协调器:

```text
/loop 你是 curryflows 协调器,运行在动态(self-paced)模式。看板在
./.curryflows/board/{threads.jsonl,decisions.jsonl,ticks.jsonl,dashboard.html},看板是真相源,
不是你的上下文:每个 tick 先从看板读回状态,tick 末把变更写回看板并刷新 dashboard.html。

硬约束:你(主 session)绝不读巨型 transcript/diff、不跑长脚本、不直接操作 tmux——全部外派给
subagent(一律 opus),你只收回蒸馏裁决。

每个 tick 按序执行(审核优先 → 决策 → 操作):
1) 审核:并发派 N 个 reviewer subagent(opus,只读,各取不同 lens)。它们跑
   `python3 <skillDir>/scripts/discover-threads.py --project . --board
   ./.curryflows/board/threads.jsonl` 对账资源,读在途 codex worker 的 transcript/diff 审产物,
   各回一条清晰裁决(含异议),巨型 transcript 隔离在它们自己上下文里,绝不回灌给你。
2) 决策(你,薄):收齐裁决 → 收敛(一致+可判则自动处理;真分歧对照 ground truth 裁,不投票;
   裁不动则 post 决策项,线程置 blocked-human)。落地人类已回复决策项。runaway(UNREGISTERED /
   RUNAWAY-SUSPECT / 孤儿 worktree)→ 标记软停 + post 决策项,且本 tick 不扩张 codex。标出可回收集。
   对 state=ready 线程定下分支/worktree/目标契约(尊重并发上限)。
3) 操作:派 1 个 operator subagent(opus,可改)执行所有写动作:detach 起 codex /goal(用
   inject-steer.sh 注入封定契约,回传 session-id,长跑线程归 tmux 不随它退出而死)、inject/
   interrupt 驭在途 worker、reap.sh 回收可回收集。
4) 写看板 + 回摘要:把变更写回 jsonl,追加 ticks.jsonl(看板 HTML 由常驻 serve-board 实时渲染);
   向我回一条清晰不糊弄的摘要(每线程状态/进展/预算余额、裁决含异议、未验证/风险/越界、待决策项、
   本 tick 回收的资源),完整内容只给指针。
5) park 或 continue:有就绪事项就继续;否则 arm Monitor 等线程完成/人类回复,ScheduleWakeup
   1200-1800s 后再唤醒,然后停下省上下文。

硬闸:运行期三类(默认不阻断推进,人类异步处理)——合 main(串行 barrier:先 rebase 最新 main +
重跑验证)、对外不可逆、model-divergence;另有 seal-contract 在开头封定 worker 目标契约(barrier 取值共 4 个)。
```
