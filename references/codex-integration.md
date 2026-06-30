# codex 集成:全走 tmux,两种模式

curryflows 对 codex 的所有操作都走 tmux。**send-keys 的边界按 pane 状态划分**:刚起的
detached pane 是普通 shell,所以用 `tmux send-keys` / `tmux new-session "<cmd>"` 在该 shell
pane 上启动 codex 二进制是允许的(`codex-review.sh` 与 operator 起会话正是这么做);但**一旦
codex TUI 起来,之后所有输入必须走 `scripts/inject-steer.sh`,绝不对 live codex TUI 手搓 raw
`tmux send-keys`**。理由是 live TUI 的渲染时序是 racy 的,一个已观测到的失败模式是大段 paste
之后第一个 Enter 静默不生效;手搓 send-keys 没有「输入落地校验 + Enter 提交校验 + 有界重试」,
会静默丢消息。inject-steer.sh 把这套注入流程做成确定性的(见该脚本头注与下文「驱动器契约」)。
Escape 软停走 `scripts/interrupt-target.sh`,同样不对 live TUI 手搓 raw send-keys。

## CANON [H]:codex 启动纪律(fail-closed,与 /loop 是否在跑解耦)

curryflows 上下文里的**任何** codex 调用——无论协调器是否在 `/loop` 模式、无论是 tick 内的
operator 动作还是协调器临时起一条有界 review——**只能经 tmux 启动,并由一个 subagent 监控到完成**。
这条规则与 `/loop` 是否启动**解耦**:即使在尚未起协调器的 inline 场景,起 codex 也照此办理(补上
"`/loop` 没起 → codex 退回脆弱路径"这个洞)。

**只允许**:`tmux new-session -d` 起普通 shell pane → 在该 pane 上启动 codex 二进制(有界 review 用
`scripts/codex-review.sh`、自驱 worker 用 `codex … /goal`)→ codex TUI 起来后改走
`scripts/inject-steer.sh` 注入(CANON [F])。

**禁用**(fail-closed,不得回退):codex 插件命令(`codex:rescue` / `codex:review` /
`codex:adversarial-review`)、`codex exec`(headless)、以及任何 companion / 远端 CLI 代理路径。理由:
这些把 codex 进程绑死在一个不可重连的连接 / 远端网关上,SSH 断连或网关故障即整段在途进度丢失、最终
什么都不返回——**已观测失败**:companion 路由 `…/responses` 返回 502 Bad Gateway、5 次重试全失败、零
产物。tmux 则相反:tmux server 常驻,断连后 `tmux attach` 即回到原 pane,在途 codex 进程一直存活。

**启动 + 监控同属一个 subagent,绝不刮 TUI**:codex 把成果写到一个声明好的交付文件路径(那是它唯一
允许的写)。两种模式各自的"谁启动、谁监控":

- **有界 review 腿**:由**一个 subagent** 启动 codex(在其内跑 `codex-review.sh`:`tmux new-session`
  → `inject-steer.sh` 注入),**该 subagent 启动完即实时监控到完成**——轮询交付文件,字节数 > 0 且连续
  `STABLE_NEEDED` 次不变才判完成,再把蒸馏结论回传协调器。`codex-review.sh` 已内建这套文件稳定检测
  (见下),subagent 全程在它的大上下文里盯,结束随它消亡。**这正是全局纪律「codex review 用一个
  subagent 启动 + 启动后该 subagent 实时监控」的落地。**
- **自驱 /goal worker**:operator 把它**detach 起在 tmux 里**(长跑、跨天存活,不能让一个 subagent
  阻塞几天),随后**每 tick 由 reviewer**(`review-panel.js`)读其交付文件 + transcript 判进度 / 完成。
  监控仍在 subagent(reviewer)里,不在协调器。

协调器(主 session)绝不亲自轮询 pane 或读巨型 transcript——启动与监控都发生在 subagent 里,大上下文
随它消亡。

## 两种模式

| 维度 | 有界 review(codex 第二意见腿) | 自驱 worker |
|---|---|---|
| 启动方式 | `scripts/codex-review.sh`(单 prompt) | codex `/goal` + 强目标契约 |
| 输入 | 单个 review prompt + 严格输出契约 | `/goal` 七字段强契约(见 `goal-contract.md`) |
| 输出 | 文件交付:codex 写声明的成果文件,我们读文件 | 长程产物 + 证据,基于证据判完成 |
| 监督 | **不挂 overseer**(有界,自然终止) | 挂只读审计 + Esc 急停 |
| 完成判定 | 成果文件出现并稳定 `STABLE_NEEDED` 次 | 契约的 VERIFICATION + BUDGET 硬上限 |
| 适用 | worker=codex 时可选(跨模型已由结构成立,非每 tick 必跑);worker=Claude subagent 时**必需**(否则单模型,见下) | 长程不确定调查(profiling / 复现 / 研究) |

curryflows 的跨模型来自一条硬规则:**跨模型 review 仅当 worker.model != reviewer.model 才成立**。
默认 worker 是 codex `/goal`、reviewer 是 Claude opus,produce 与 review 天然跨模型(见 `SKILL.md`、
`reviewer-spec.md`);此时 `codex-review.sh` 是可选增强——某条裁决需要 codex 侧独立第二意见时
reviewer 才拉一份有界 codex 审核,不是每 tick 必跑。

但**若某线程的 worker 是 Claude subagent(非 codex),则至少一个 reviewer 必须是 codex 腿
(`codex-review.sh`)**:此时 worker 与 Claude reviewer 同模型,只有把 codex-review.sh 拉进来,
reviewer 模型集合里才会出现与 worker 不同的模型。这种情形下 codex-review.sh 是**必需,不是可选**;
否则审核退化为单模型,跨模型保证作废。协调器必须保证 reviewer 模型集合里存在与 worker 不同的模型。
真正干活的默认 worker 用自驱 `/goal` 并挂监督。

## 为什么不用 codex exec / 插件 / companion(CANON [H] 的理由)

`codex exec` 是 headless 模式:它不在一个可重连的终端会话里跑,进程与发起它的连接绑定。
本机是通过 SSH 操作的,SSH 断连会带走 headless 进程,在途进度(in-flight progress)随之
丢失,无法重连恢复。codex **插件命令**(`codex:rescue` 等)与 **companion CLI** 更糟:它们在
headless 之上再加一跳远端网关,网关 502 / 限流即整段失败、零产物(已观测,见 CANON [H])。
这三条路径都不满足 curryflows 的"断连可重连 + 可被 subagent 监控"要求,故 fail-closed 禁用。

走 tmux 则不同:codex TUI 跑在一个 detached tmux 会话里,tmux server 是常驻进程,SSH
断连不影响它;重连后 `tmux attach` 即可回到原 pane,在途的 codex 进程一直存活。这是
curryflows 全程走 tmux 而不用 codex exec 的根本原因。

代价是 headless 模式自带的结构化输出(stdout 直接拿)没有了。**文件交付模式**把它补回来:
prompt 里给 codex 一条硬契约——把成果写到一个声明好的文件路径,那是它唯一允许的写,写完即停;
我们读这个文件拿结构化输出,而不是去刮(scrape)TUI pane 的可见文本。刮 pane 不可靠(渲染
折叠、滚动、状态行干扰),读文件可靠。

## codex-review.sh:有界 review 腿驱动器

`scripts/codex-review.sh` 是有界模式的完整驱动器,内部依赖 `inject-steer.sh` 注入、
`interrupt-target.sh` 落 idle。

### 用法

```bash
codex-review.sh --cwd <dir> --prompt-file <f> --out <findings-path> \
                [--name <tmux-session>] [--effort low|medium|high|xhigh] \
                [--ready-timeout <s>] [--timeout <s>] [--keep]
```

### 参数

- `--cwd`(必填):codex 运行目录,须为已存在目录。
- `--prompt-file`(必填):review 任务的 prompt 文件;脚本会在其后追加一段严格契约——
  只读 review、不得编辑/新建/删除任何仓库文件、把完整 findings 以 markdown 写到 `--out`
  指定的精确路径(那是唯一允许的写)、用 file:line 证据 + 对任一缺陷给可复现步骤或具体
  测试缺口 + 给 findings 排序、写完即停。
- `--out`(必填):findings 文件路径;脚本启动时先 `rm -f` 该路径,完成后把绝对路径
  echo 到 stdout。
- `--name`:tmux 会话名,默认 `cfx_rev_$$`。
- `--effort`:`model_reasoning_effort`,默认 `medium`。
- `--ready-timeout`:等 codex TUI 起来的秒数,默认 90。
- `--timeout`:等 findings 文件出现并稳定的秒数,默认 1800。
- `--keep`:完成后保留 tmux 会话(默认拆除)。

### 执行流程

1. `tmux new-session -d` 起 detached 会话,在该 pane 里发
   `codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=<effort>`;
2. 轮询 `inject-steer.sh verify-target <pane>` 等 TUI 成为可注入目标(上限
   `--ready-timeout`);
3. 把 `--prompt-file` 内容 + 上述严格输出契约拼成完整 prompt;
4. 用 `inject-steer.sh send <pane> <full-prompt>` 注入(硬化:校验 + 重试 + 结构化退出码);
5. 文件稳定检测:每 5s 轮询 `--out`,当其字节数 > 0 且连续 `STABLE_NEEDED=3` 次不变,即
   判完成——这是稳健的完成信号,不刮 TUI;
6. 完成后用 `interrupt-target.sh` 把 pane 落 idle,再拆除 tmux 会话(除非 `--keep`)。

### 退出码

- `0` — findings 已写入 `--out`(路径 echo 到 stdout)。
- `10` — `--ready-timeout` 内 codex TUI 没起来。
- `20` — inject 失败。
- `30` — 等 findings 文件超时(`--timeout`)。
- `64` — 用法 / 输入错误。

### 实测跑通

该脚本已被实测端到端跑通:launch codex → inject-steer 注入 prompt → codex 写出 FINDINGS
文件 → 文件稳定检测命中 → interrupt 落 idle + 拆 tmux → exit 0。

## 驱动器契约(inject-steer.sh / interrupt-target.sh)

两个脚本共用硬化的 target 健康检查(`verify_target`),只对已验证的「live Codex TUI」动手,
拒绝把按键落到普通 shell 或监控自己的 Claude pane——靠进程树识别 codex(Codex TUI 跑在
主屏 `alternate_on=0`,所以不能用 alternate-screen 区分)。

- `inject-steer.sh` 子命令:`verify-target`(只读健康检查)、`type`(粘贴不回车)、
  `submit`(回车提交,有界重试)、`send`(原子 type + submit)。文本一律经文件传入,不走
  argv,所以 CJK / 引号 / 括号 / 换行不会被 shell 二次解析。它处理 Codex 长 paste 折叠成
  `[Pasted Content N chars]` 占位符的情形(按 Unicode 码点数核对,不按字节)。退出码见脚本
  头注(0 成功 / 10 目标无效或死 / 11 非 codex TUI / 20 pane 不稳 / 30 paste 失败 /
  31 落地校验失败 / 40 提交校验失败 / 41 提交后状态歧义 / 64 用法)。
- `interrupt-target.sh <pane>` 发单个 Escape 软停:codex 进程存活、goal 上下文完整,只停
  在途 turn,供人类 review 后再指示。退出码:0 已停 idle / 10 目标无效或死 / 11 非 codex
  TUI / 20 pane 不稳 / 40 Escape 后仍 Working / 64 用法。

对目标 codex 的写只有两类:Escape(软停)和人类裁决后注入的指令,其余全只读
(见 `coordinator.md`「监督拆分」)。

## discover-threads.py:把所有 codex 会话兜出来

`scripts/discover-threads.py` 是统一的只读资源发现,确保协调器对任何在途 codex /goal 或
孤儿 worktree 都不盲。它把两类资源并集后与 per-project board 对账(见 `coordinator.md`
tick 第 1 步)。

codex 会话来自三个来源,关键是它能兜出 VSCode / app-server 起的 headless 会话——只靠
tmux 发现会漏掉它们:

1. `~/.codex/sessions` 下的 rollout transcript(`rollout-*.jsonl`)——**每一个** codex
   会话,无论从哪个入口启动(CLI、VSCode/app-server)。只读首行(`session_meta`)+ 文件
   stat,绝不读几百 MB 的正文。这是发现 headless 会话的关键来源。
2. tmux panes:前台命令看起来像 live Codex TUI 的 pane。
3. `ps`:codex / app-server 进程。

worktree 来自 `git worktree list`,过滤到 `curryflows/*` 分支,未被 board 追踪的标
`[ORPHAN]`。

判读(精确语义见脚本头注):

- rollout `idle_min <= --active-min`(默认 10 分钟)= `active`;
- `active` 且 `size_mb >= --runaway-mb`(默认 50MB)= `RUNAWAY-SUSPECT`;
- 提供了 board 时,active 会话的 `session_id` 不在 board 的 `codex_session` 集合里 =
  `UNREGISTERED`;
- **空 board 仍算「已提供」**:此时一切在途资源都未登记,正是最危险的情形,必须与「没给
  board」区分开;
- 退出码:`0` 干净;`2` 有 active codex 会话或 `curryflows/*` worktree 未登记到所给 board
  (curryflows 存在就是为了永不漏掉这个 in-flight-but-untracked 条件);`64` 用法错误。

输出:stdout 是 JSONL(每资源一行),stderr 是人类可读汇总。要让一个自驱线程不被当
`UNREGISTERED`,启动后必须立即把它注册到 board(见 `goal-contract.md`)。
