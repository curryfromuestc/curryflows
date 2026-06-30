# 在 Codex 中使用 Goal:面向长时运行工作的持久化目标

Goal(目标)是 Codex 中的持久化目标,使一个线程(thread)跨多个回合持续朝着既定结果推进。Goal 为 Codex 提供一个完成条件:什么应当为真、如何校验成功,以及哪些约束必须保持不变。

对于范围明确的编码任务,Codex 已经表现良好:检视仓库、修复 bug、新增测试、解释失败原因,或实现一处集中的改动。Goal 适用于"下一步取决于 Codex 在过程中所获认知"的任务:性能剖析、打补丁、基准测试、复现不稳定测试(flaky test),或把一个研究问题转化为有证据支撑的审计。

这类任务需要的不是更大的 prompt,而是一个持久化的目标。借助 Goal,Codex 能够持续关注该目标、评估工作是否完成,并选择下一个有价值的动作,而无需你在每个中间结果之后重述目标。

Goal 不是没有边界的后台自治,而是一个有范围限定、由用户控制的完成契约。你定义结果,Codex 依据线程中的证据开展工作,而 Goal 可以被暂停、恢复、清除、完成,或因预算耗尽而停止。



## 你将学到什么

读完本指南后,你将能够:
- 判断在什么情况下 Goal 比一次性 prompt 更合适。
- 编写带有可度量结果、验证面(verification surface)与约束的 Goal。
- 使用 `/goal`、`/goal pause`、`/goal resume` 和 `/goal clear` 管理其生命周期。

## 前置条件

- 一个支持 Goal 的 Codex 构建版本。
- 一个具有明确终点线、且带有可检视证据来源的任务,例如测试、基准测试或最终产物(artifact)。
- 足够的仓库或研究上下文,使 Codex 能够验证进展,而不只是复述进展。

## 大纲

1. 启动一个 Goal 并管理其生命周期。
2. 理解 Goal 与一次性 prompt 的区别。
3. 编写带有可审计完成标准的 Goal。
4. 将该模式应用于复杂的研究工作。
5. 判断在什么情况下普通 prompt 仍是更合适的工具。


![p0.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/8da48971-aa86-43f6-bb8f-93a3f25733b5.png)

*图 1。Goal 把单回合交互转化为带证据校验的续行循环。*

## 快速上手:使用 Goal

当任务有明确终点线、但通往终点的路径并不确定时,使用 Goal。
合适的候选场景包括:性能优化、不稳定测试排查、依赖迁移、需要复现的 bug 排查、多步重构、基准驱动的调优,以及需要最终产物的研究任务。对于一次性编辑,普通 prompt 仍是合适的工具。

要使用 Goal,请安装或更新 Codex 并确认版本。Goal 从 Codex 0.128.0 起可用。

使用 npm:

```bash
npm install -g @openai/codex@latest
codex --version
```

使用 Homebrew:

```bash
brew update
brew upgrade --cask codex
codex --version
```

然后用 /goal 加上目标结果来设置一个 Goal:

```text
/goal Reduce p95 latency below 120 ms without regressing correctness tests
```

你可以在同一命令界面管理其生命周期:

```text
/goal      	View the current Goal
/goal pause    Pause an active Goal
/goal resume   Resume a paused Goal
/goal clear    Remove the current Goal
```

一旦 Goal 处于活跃状态,Codex 就可以检视代码、运行相关命令、做出改动、测试结果,并持续推进,直到抵达某个停止条件。停止条件可能是成功、暂停、清除、中断、预算上限,或需要用户输入的阻塞点。

如果你发现自己在每个回合之后都要重复说同样的话,就该使用 Goal:

```text
Keep going.
Try the next likely fix.
Run the benchmark again.
Now check the tests.
Continue until this is actually done.
```

Goal 把这种意图显式化。

## Goal 与 prompt 的对比

普通 prompt 表达的是:接下来做这件事。

Goal 表达的是:持续工作,直到这个结果为真。
这个区别很重要。在普通请求中,Codex 处理当下的指令、报告结果,然后等待。有了 Goal,Codex 就拥有一个附着在线程上的持久目标。一个回合结束后,它可以检视当前证据并判断目标是否已满足。如果答案是否定的,且 Goal 仍处于活跃状态并在预算之内,Codex 就可以从最新状态继续推进。

因此,当正确的下一步动作取决于 Codex 刚刚获得的认知时,Goal 最为有用。例如:

```text
/goal Reduce p95 checkout latency below 120 ms on the checkout benchmark while keeping the correctness suite green
```

这不只是一个改进性能的请求。它给了 Codex 一个可度量的结果、一个验证面和一个约束。Codex 可以运行基准测试、检视热点路径(hot path)、做出有针对性的改动、重跑基准测试、运行正确性测试套件,并在结果仍不够好时继续推进。

实用的心智模型很简单:

Prompt:  提问 -> 工作 -> 结果 -> 等待

Goal:    工作 -> 校验 -> 继续或完成

Goal 给 Codex 一条终点线。但工作仍然必须依据证据来审计。

## 如何编写 Goal

一个好的 Goal 不只是一个更大的 prompt。它是一份紧凑的契约,规定 Codex 应当如何工作、什么算作成功,以及在成功尚不可达时应当发生什么。

最强的 Goal 通常会定义六样东西:
* 结果(Outcome):工作完成时什么应当为真。
* 验证面(Verification surface):用以证明的测试、基准测试、报告、产物、命令输出或源材料。
* 约束(Constraints):在 Codex 工作期间什么不得退化。
* 边界(Boundaries):Codex 可以使用哪些文件、工具、数据、仓库或资源。
* 迭代策略(Iteration policy):Codex 在每次尝试之后应当如何决定下一步尝试什么。
* 阻塞停止条件(Blocked stop condition):在当前限制下已无可辩护的路径时,Codex 何时应当停止并报告。

一个有用的模式是:

```text
/goal <desired end state> verified by <specific evidence> while preserving <constraints>. Use <allowed inputs, tools, or boundaries>. Between iterations, <how Codex should choose the next best action>. If blocked or no valid paths remain, <what Codex should report and what would unlock progress>.
```

例如,下面这个 Goal 可用,但仍然相当单薄:

```text
/goal Reduce p95 checkout latency below 120 ms without regressing correctness tests
```

更强的版本给 Codex 一份更完整的操作契约:

```text
/goal Reduce p95 checkout latency below 120 ms, verified by the checkout benchmark, while keeping the correctness suite green. Use only the checkout service, benchmark fixtures, and related tests. Between iterations, record what changed, what the benchmark showed, and the next best experiment to try. If the benchmark cannot run or no valid paths remain, stop with the attempted paths, the evidence gathered, the blocker, and the next input needed.
```

对于研究和调查,同样的原则适用。在工作开始之前先定义证据标准,尤其是在可能无法获得精确证明的情况下:

```text
/goal Produce the strongest evidence-backed reproduction of the paper using the available materials and local resources. Attempt the headline results where feasible, verify outputs where possible, and end with a report that separates confirmed findings, approximate reconstructions, blocked claims, and remaining uncertainty.
```

这类 Goal 给了 Codex 调查的空间,同时保持最终结果的诚实。它不只是说"继续推进",而是说清楚"完成""阻塞"和"仍不确定"到底意味着什么。

当任务清晰但 Goal 尚不清晰时,Codex 可以帮助编写 Goal 本身。一个简单的两步工作流很有效:第一步,用平实的语言描述工作,并让 Codex 把它转化为一份 Goal 草稿;第二步,审阅该草稿,并在激活之前收紧成功条件、验证面、约束和阻塞停止条件。

例如:

```text
Help me turn this into a strong `/goal`: I want Codex to keep working on this flaky checkout test until we either fix it with evidence or can clearly explain what is blocking progress.
```

随后 Codex 可以提出一个更完整的 Goal,询问任何确实必要的缺失细节,并为你留下一个更清晰的 `/goal` 供使用。

## Goal 活跃时会发生哪些变化

当 Goal 处于活跃状态时,有三样东西会改变。

第一,目标保持可见。如果 Codex 运行一个测试且它失败了,线程仍然保留着原始目标。如果基准测试有所改善但未达到阈值,Codex 可以继续推进。如果某条研究路径遇到数据缺失,Codex 可以调整证据计划,而不会偏离研究标准。

第二,续行得以从空闲线程发起。当另一个回合正在进行、有用户输入在排队,或有其他线程工作待处理时,Codex 不会续行。只有当线程空闲、Goal 处于活跃状态且在预算之内时,它才会续行。
第三,完成必须基于证据。不能因为模型认为大概完成了就把 Goal 标记为完成。只有在目标已对照相关文件、测试、日志、基准测试输出、生成的产物或其他具体证据完成校验之后,才应将其标记为完成。

这就是设计核心:Codex 可以持续推进,但由证据决定它是否完成。

## Goal 在 Codex 中的设计方式

Goal 被实现为持久化的线程状态,而不是全局记忆,也不是项目级指令。这个设计选择很重要:目标归属于承载相关上下文的那个线程,包括 Codex 检视过的文件、运行过的命令、产生的 diff、看到的日志,以及它积累起来的推理轨迹。



![p1.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/0f0ac4f7-1cef-4baf-82b5-4f9ce1fdc1b8.png)

*图 2。Goal 为当前线程增加了持久状态、续行、控制项与证据校验。*

在架构层面,Goal 是一个持久的、线程范围(thread-scoped)的状态。它记录了 Codex 随时间评估线程所需的目标、生命周期、预算和进度核算。关键的边界在于范围:Goal 归属于当前线程,而不归属于全局记忆或项目指令。

Codex 把该状态视为用户、模型与线程之间的契约。Goal 可以处于活跃、暂停、完成或预算受限状态。这些状态决定了 Codex 是否可以继续、是否应当等待用户,以及是否应当总结进度而不是开始新的工作。

续行是事件驱动的,而不是一个简单的循环。Codex 仅在安全边界处检查是否续行:在一个回合结束之后、没有其他工作待处理时、没有用户输入排队时,以及线程空闲时。

调度器(dispatcher)的行为是刻意保守的。仅做计划的工作不会触发续行。中断会暂停目标。在适当时,恢复线程可以重新启用目标。如果一个续行回合没有发起任何工具调用,下一次自动续行将被抑制,以免 Codex 空转。



![p2.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/a794a2b2-cf62-4db3-af87-cae2beda7729.png)

*图 3。只有当 Goal 处于活跃状态、线程空闲且没有用户输入排队时,Codex 才会续行。*

提示层(prompting layer)强化了同样的架构。续行提示(continuation prompt)让 Codex 围绕活跃目标展开,但它们也要求在完成之前进行一次审计。Codex 必须将目标与具体证据进行比对:更改的文件、运行的命令、通过的测试、基准测试输出、生成的产物或研究证据。

预算处理是显式的。当达到预算上限时,Codex 应当停止实质性工作,总结进度和阻塞点,并指出下一个有用的步骤。达到预算上限并不等同于完成目标。

工具契约(tool contract)将生命周期权限保持在有界范围内。模型可以启动一个 Goal,并且只有在证据支持完成时,才能把一个已存在的 Goal 标记为完成。暂停、恢复、清除以及预算受限的状态转换,仍由用户或系统控制。

需要记住的架构是这样的:Goal 是一个线程范围的完成契约。它结合了持久的目标状态、生命周期控制、续行策略、预算核算和基于证据的完成。要点不是让 Codex 永远循环,而是让目标持续存在,直到证据表明工作已完成。

## 把弱 Goal 变成强 Goal

弱:

```text
/goal Improve performance
```

强:

```text
/goal Reduce p95 latency below 120 ms on the checkout benchmark while keeping the correctness test suite green
```

### 示例:性能调优

![p3.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/6b27acf7-b270-43d9-bb8f-1b21186cbfc0.png)

*图 4。强 Goal 会指明终态、验证面与约束。*

更强的版本给了 Codex 三样东西:一个结果、一种验证方法和一个约束。它还给了 Codex 一种判断何时不应停止的方式。如果 p95 从 180 ms 改善到 135 ms,Goal 尚未完成。如果延迟降到 120 ms 以下但正确性测试失败,Goal 尚未完成。如果基准测试无法运行,Codex 必须把该阻塞点暴露出来,而不是宣布成功。

同样的规则适用于性能工作之外:Goal 应当保持足够具体以便验证,但又足够开放以支持探索发现。

Goal 应当窄到足以审计,又宽到足以让 Codex 选择下一步动作。如果真正的问题出在上游依赖,"修复失败的 checkout 测试"可能过窄。"改进整个系统"则过宽,因为没有审计面。"在不改变公共 API 行为的前提下,让 checkout 测试套件在当前分支上通过",要好得多。

同样的原则适用于生成的产物。一个弱 Goal 是这样说的:

```text
/goal Write docs for this feature
```

一个更强的 Goal 是这样说的:

```text
/goal Produce a docs page for Goals that explains the lifecycle, command surface, and two examples. Verify that the page builds locally and that all referenced commands match the current CLI behavior.
```

第二个 Goal 给了 Codex 一些可以检视的东西:一个页面、一条构建命令,以及命令的行为。

对于研究型 Goal,同样的规范甚至更为重要。在调查开始之前先定义证据标准:什么算作精确复现、什么算作部分重建、什么算作代理证据支撑,以及什么应当被视为阻塞。

一个强的研究型 Goal 应当要求 Codex 建立一份论断清单(claim inventory),把论断映射到证据,实现可行的部分,标注阻塞点,并产出一份审计,把已确认的论断、仅有支撑性证据的论断、被阻塞的论断和剩余的不确定性区分开来。

这使 Goal 既窄到足以审计,又不必规定整条路径。Codex 可以选择下一步动作,但完成标准是固定的。

## 将 Goal 用于复杂研究:复现一篇量化论文

下面是一个运用这些原则的研究型 Goal 的具体示例。

该案例研究是 Buehler、Gonon、Teichmann 和 Wood 的 Deep Hedging。这篇论文探讨的是:在不同的风险偏好、交易成本和更高维度的市场设定下,神经网络交易策略能否复现基于模型的对冲。正确的 Goal 不是抽象地"复现这篇论文",而是尝试论文的核心数值论断,把精确的机制与近似的训练替代物区分开来,并明确说明从现有材料中无法被精确重放(replay)的部分。

一个弱的研究型 Goal 会是:

```text
/goal Reproduce Buehler et al., "Deep Hedging"
```

这定义不足。它没有说明哪一节是关键、什么算作复现、如何处理不可获得的训练状态,或如何区分接近的数值匹配与精确的重放。

一个更好的 Goal 是:

```text
/goal Produce the strongest evidence-backed reproduction of Buehler et al., "Deep Hedging," using the available paper materials and local resources. Attempt every headline result, verify the outputs, and end with a report that separates reproduced mechanics, approximate trained results, blocked exact replay, and remaining uncertainty.
```

更强的版本之所以有效,是因为它指明了证据标准和最终产物。Codex 不只是试图产出一个令人印象深刻的复现,而是在不夸大现有证据所能支撑范围的前提下,尽量减少不确定性。



![p4.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/44b2dc53-6a45-4b08-8650-d4bcfa2670c7.png)

*图 5。研究型 Goal 在宣布状态之前,先把论文分解为多个证据通道。*

在实践中,该 Goal 为这次调查提供了一份具体的操作契约。

Codex 用它来:
* 把核心论断与支撑性论断区分开来,
* 将这些论断映射到可获得的证据,
* 重建可以在本地测试的部分,
* 并标注无法从现有材料中精确复现的论断。

有几个部分是可行的。Codex 重建了定价与对冲机制,复现了 Heston 参考价格,为 CVaR 对冲实验训练了策略,重建了主直方图与对冲曲面(hedge-surface)产物,复现了 Black-Scholes 交易成本斜率,并为 Heston 交易成本与高维示例运行了基于训练的检查。

有些论断仍因源材料缺失而被阻塞。论文没有提供确切的随机种子、生成的训练路径、TensorFlow 计算图、优化器状态、检查点(checkpoint)或完整的原始仿真状态。这意味着最强的诚实结果是一个部分且近似的复现,而不是一个精确的神经网络重放。

这正是 Goal 的意义所在。它让工作在出现阻塞之后仍能推进,但同时也让最终表述保持诚实。一个训练出来的替代物可以支撑一个论断,一个接近的数值匹配可以提高置信度,一张重建的图可以验证结果的一部分,但这些都不应被描述为精确还原了原始实验。



![p5.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/591e8054-d28c-4853-bb15-c66cb1146351.png)

*图 6。最终输出应当保留不同层级的认知支撑(epistemic support)。*

最终报告应当保留这些不同层级的支撑,而不是把它们压平成单一的成功论断。
例如,其中一条台账(ledger)条目可能是这样的:

```text
Claim: Deep hedging approximates complete-market Heston hedge without transaction costs.
Route: Rebuilt model mechanics, reference hedge comparison, and trained neural policy.
Evidence surface: Price checks, histograms, and hedge surfaces.
Status: Close approximate reproduction.
Remaining uncertainty: Original training paths, seeds, and checkpoints are unavailable.
```

这就是 Goal 在研究中的演示价值。它们让 Codex 能够在含糊不清中持续工作,同时防止一个看似可信的产物变成一个被夸大的结论。Goal 不只是要求 Codex 完成。它定义了"完成"意味着什么:一次以证据为基础、逐条论断的审计,对近似之处明确说明,并对复现与重放之间的界限保持诚实。

![p6.png](https://developers.openai.com/cookbook/assets/notebook-attachments/examples/codex/using_goals_in_codex/60eac88a-11d6-4649-babe-873d0bfe88b7.png)

## 何时不应使用 Goal

Goal 并不是适用于每项任务的工具。

不要把 Goal 用于一行代码的编辑、简单的解释、简短的代码评审,或那种你只想要一个答案然后就停止的问题。对于这些,普通的 Codex prompt 更合适。

当终点线含糊时,不要使用 Goal。"把这个做得更好"不能给 Codex 任何可靠的完成条件。"重构这段代码"同样很弱,除非你定义了预期的终态、测试和约束。

不要用 Goal 来掩盖不确定性。如果数据可能无法获得,就在 Goal 里说明。如果某个基准测试可能不稳定,就说明该如何处理。如果允许使用代理证据,就定义它应当如何被标注。

当任务具备三个特性时,Goal 最为有力:一个持久的目标、一条基于证据的终点线,以及一条可能需要多个回合调查的路径。

## 结论:让目标持续存在,但由证据决定

Goal 改变了 Codex 的运作模式。它们把一个线程从一连串孤立的 prompt,转变为围绕既定结果的有状态工作循环。

该架构是刻意有界的。Goal 的范围限定在一个线程内,携带生命周期状态和预算核算,并且可以被暂停、恢复、清除、完成,或因预算而停止。Codex 可以持续推进,但只能在用户定义的契约之内。
这使得 Goal 在 Codex 本就最有价值的工作上很有用:调试、优化、迁移、测试和研究。用户提供目标。Codex 跟随证据。Goal 让两者保持连接,直到工作要么完成,要么被诚实地判定为阻塞。

对于复杂研究,这就是"生成一个答案"与"产出一份审计"之间的区别。一个好的 Goal 不只是要求 Codex 完成。它告诉 Codex"完成"意味着什么。
