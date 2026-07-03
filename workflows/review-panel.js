// curryflows 内层有界 review 面板 —— 作为 Claude Code 官方 Workflow 运行(不是手搓并行 Agent)。
//
// 设计边界(见 SKILL.md「何时用 / 何时不用」):curryflows 的外层协调器是 /loop 动态模式;
// 每个 tick 的「审核」步骤是一次有界扇出,正是官方 Workflow 工具最擅长的事,故由本脚本承载。
// operator(起/驭/回收 tmux codex)与 durable 看板仍是 curryflows 层,不在本脚本内。
//
// 前提:协调器会话已开启 ultracode / 已显式 opt-in 多-agent 编排(本脚本即官方 Workflow 调用)。
//
// 调用:Workflow({ scriptPath: '<skillDir>/workflows/review-panel.js', args: {
//   board, skillDir, projectDir,
//   threads: [{ thread_id, worktree, branch, codex_session, contract, worker_model, state }]
// }})
// `state` (board thread state, e.g. running/idle/committed) lets the repro lens know
// whether this is a progress review (L1/L2 ok) or a committed->verified pass (must be L3).
//
// 返回:{ reviews: [{thread, branch, worktree, verdict, findings, dissent, escalate, resources}], escalations: [...] }
// 协调器据此做决策步(收敛已在 arbiter 完成、不投票),再派 operator。

export const meta = {
  name: 'review-panel',
  description: 'curryflows 内层有界 review 面板:对一批在途 codex worker 线程,跨模型多-lens 审产物 + 资源对账 + arbiter 收敛(不投票),返回结构化裁决 + escalations。由 /loop 协调器每 tick 调用。',
  phases: [
    { title: 'Review', detail: '每线程并发跨模型多 lens 审产物 + 资源对账' },
    { title: 'Arbiter', detail: '每线程对照契约 ground truth 收敛,不投票' },
  ],
}

// Harness-compat: some Workflow runtimes deliver the `args` global as a JSON STRING
// (not a parsed object), which would make A.threads undefined -> silent "no threads to
// review" / 0 agents. Parse it back if so; a real object passes through unchanged.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const THREADS = A.threads || []
const SKILL = A.skillDir || ''
const BOARD = A.board || ''
const PROJECT = A.projectDir || '.'

if (!THREADS.length) {
  return { reviews: [], escalations: [], note: 'no threads to review' }
}

// reviewer 视角(lens);每个 lens 一个独立、隔离上下文的 reviewer
const LENSES = ['correctness', 'bounds', 'invariant', 'repro']

// 每个 lens reviewer 的回传(契约见 references/reviewer-spec.md)
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    lens: { type: 'string' },
    verdict: { type: 'string', enum: ['pass', 'continue', 'escalate', 'runaway', 'failed'] },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          evidence: { type: 'string' },
          reproducible: { type: 'string' },
        },
        required: ['title', 'severity', 'evidence', 'reproducible'],
      },
    },
    dissent: { type: ['string', 'null'] },
    unverified: { type: 'array', items: { type: 'string' } },
    // Independence tier actually achieved by this lens's replay (CANON [P]).
    // repro lens reports L1/L2/L3; lenses that don't replay report 'n/a'.
    // committed->verified requires L3; report honestly (L1/L2 is fine to admit,
    // but then this lens cannot support a 'verified' conclusion).
    independence_tier: { type: 'string', enum: ['L1', 'L2', 'L3', 'n/a'] },
    resources: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          kind: { type: 'string', enum: ['unregistered', 'runaway-suspect', 'orphan', 'reclaimable'] },
          ref: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['kind', 'ref', 'evidence'],
      },
    },
    failed: { type: 'boolean' },
  },
  required: ['lens', 'verdict', 'findings', 'dissent', 'unverified', 'independence_tier', 'resources', 'failed'],
}

// arbiter 的收敛回传(不投票;对照契约 ground truth 裁;裁不动 → escalate)
const FINAL_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['pass', 'continue', 'escalate'] },
    findings: { type: 'array', items: { type: 'object', additionalProperties: true } },
    dissent: { type: ['string', 'null'] },
    // Highest independence tier the verification replay actually reached (CANON [P]).
    // A 'verified'-supporting pass verdict on a committed thread REQUIRES L3.
    independence_tier: { type: 'string', enum: ['L1', 'L2', 'L3', 'n/a'] },
    escalate: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          title: { type: 'string' },
          divergence: { type: 'string' },
          evidence: { type: 'string' },
          recommendation: { type: 'string' },
        },
        required: ['title', 'divergence', 'evidence', 'recommendation'],
      },
    },
    resources: { type: 'array', items: { type: 'object', additionalProperties: true } },
  },
  required: ['verdict', 'findings', 'dissent', 'independence_tier', 'escalate', 'resources'],
}

function workerIsCodex(t) {
  const m = String(t.worker_model || 'codex').toLowerCase()
  return m.includes('codex') || m.includes('gpt')
}

function lensPrompt(t, lens) {
  return [
    '你是 curryflows 的只读 reviewer(lens=' + lens + ')。严格按 ' + SKILL + '/references/reviewer-spec.md 行事(先读它)。',
    '不改任何文件、不操作 tmux。把巨型 transcript 隔离在你自己上下文里,只回蒸馏裁决。',
    '',
    '被审线程:thread_id=' + t.thread_id + ' worktree=' + t.worktree + ' branch=' + t.branch +
      ' state=' + (t.state || '?') +
      ' codex_session=' + (t.codex_session || 'null') + ' worker_model=' + (t.worker_model || 'codex'),
    '已封契约(先读,这是判据 ground truth):' + (t.contract || (PROJECT + '/.curryflows/contracts/' + t.thread_id + '.md')),
    '',
    '必做三件事:',
    '1) 资源对账(只读):跑 `python3 ' + SKILL + '/scripts/discover-threads.py --project ' + PROJECT +
      ' --board ' + BOARD + '/threads.jsonl`,把 UNREGISTERED / RUNAWAY-SUSPECT / 孤儿 worktree / 可回收 填入 resources[]。',
    '2) 审产物:读 `git -C ' + t.worktree + ' diff main...HEAD` 与 codex transcript,按本 lens(' + lens +
      ')对照契约审 drift / 捏造 / 越界 / 不变量 / 预算停点 / barrier。',
    '3) 独立复验(不信 worker 自述):对照契约 VERIFICATION 自己重跑;证据须指向 checked-in artifact 路径。',
    '   **独立性三档(CANON [P]),必须在 independence_tier 如实声明本次实际达到哪档**:',
    '   L1=只读复核/re-derive(不重跑);L2=复用 worker 已建的 venv/.so 再跑;L3=抹掉 venv + 删构建产物(.so)+ clean rebuild + 亲自跑。',
    '   - 只有 repro lens 需真跑复验;不做复验的 lens 一律填 independence_tier="n/a"。',
    '   - **若本线程 state=committed(正在判 committed→verified),repro lens 必须做到 L3**——达不到 L3 就不能给支持 verified 的 pass,',
    '     如实报实际档位并回 continue/escalate。诚实报"我只到 L2/没 rebuild"是对的,但不得据此判 verified。',
    '   - **worker 自己或 worker spawn 的 subagent(同谱系)跑的 replay 一律不采信**;独立性来自你另起的执行。',
    '',
    'verdict: pass(本 lens 通过) / continue(需继续) / escalate(真分歧或契约缺口,另给 finding) / runaway(发现 runaway 资源)。',
    'dissent 不许省略(无则填 null);unverified 强制如实列出;independence_tier 必填。禁止绿洗。',
  ].join('\n')
}

function codexLensPrompt(t) {
  return [
    '你是 curryflows 的 codex 第二意见 reviewer 腿(跨模型硬规则 CANON [G]:worker 为 Claude 时 codex 腿必需)。',
    '机制:用文件交付的有界 codex review,严格按 ' + SKILL + '/references/codex-integration.md。',
    '步骤:把 review prompt 写到一个临时文件(要求 codex 对 ' + t.worktree + ' 的 git diff 做只读审核、findings 写到 --out),',
    '再跑 `bash ' + SKILL + '/scripts/codex-review.sh --cwd ' + t.worktree +
      ' --prompt-file <f> --out <findings.md> --effort high --timeout 900`,读回 findings 填入本 schema。',
    '被审线程 thread_id=' + t.thread_id + ',契约=' + (t.contract || (PROJECT + '/.curryflows/contracts/' + t.thread_id + '.md')) + '(先让 codex 读它当判据)。',
    '',
    '硬规则:codex-review.sh 非零退出 → 返回 {lens:"codex", verdict:"failed", failed:true, findings:[]},**严禁捏造 findings**。',
    'lens 填 "codex";dissent 不许省略(无则 null);unverified 如实列;independence_tier 填 "n/a"(codex 腿做 diff 审核、不跑 L3 复验)。',
  ].join('\n')
}

function arbiterPrompt(t, lensVerdicts) {
  return [
    '你是 curryflows 的 arbiter(只读)。对线程 ' + t.thread_id + ' 收敛下列多 lens 裁决,**不投票**:',
    '对照 ground truth(已封契约 ' + (t.contract || (PROJECT + '/.curryflows/contracts/' + t.thread_id + '.md')) +
      ' + 验证结果)裁。',
    '',
    JSON.stringify(lensVerdicts),
    '',
    '收敛规则(见 references/architecture.md「跨模型 review」):',
    '- 多 lens 一致且契约可判 → 给出 verdict(pass/continue)。',
    '- 真分歧 / 契约缺口,对照 ground truth 裁不动 → verdict=escalate,并在 escalate[] 给 {title, divergence, evidence(路径), recommendation(必须引用契约/权威依据)}。',
    '- 把各 lens 的 resources 合并去重到 resources[]。dissent 保留(无则 null)。禁止绿洗。',
    '- independence_tier 填各 lens 复验实际达到的**最高档**(无人复验则 "n/a");**若本线程 state=committed(判 committed→verified),必须 L3 才能给 pass**——repro lens 未达 L3 则回 continue/escalate,不得判 verified(CANON [P])。',
    '注意:若任一 lens failed=true(如 codex 腿脚本失败),如实在 dissent / findings 反映,不得当作通过。',
  ].join('\n')
}

phase('Review')

const results = await pipeline(
  THREADS,
  // stage 1:每线程并发跨模型多 lens 审 + 资源对账
  (t) => {
    const thunks = LENSES.map((lens) => () => agent(
      lensPrompt(t, lens),
      { label: 'review:' + t.thread_id + ':' + lens, phase: 'Review', agentType: 'Explore', effort: 'high', schema: VERDICT_SCHEMA },
    ))
    // 跨模型硬规则:worker 为 Claude 时,加一条 codex 腿(否则单模型、跨模型作废)
    if (!workerIsCodex(t)) {
      thunks.push(() => agent(
        codexLensPrompt(t),
        { label: 'review:' + t.thread_id + ':codex', phase: 'Review', agentType: 'general-purpose', effort: 'high', schema: VERDICT_SCHEMA },
      ))
    }
    return parallel(thunks).then((vs) => ({ lensVerdicts: vs.filter(Boolean) }))
  },
  // stage 2:每线程 arbiter 收敛(不投票)
  (reviewed, t) => agent(
    arbiterPrompt(t, reviewed.lensVerdicts),
    { label: 'arbiter:' + t.thread_id, phase: 'Arbiter', agentType: 'Explore', effort: 'high', schema: FINAL_SCHEMA },
  ).then((f) => ({ thread: t.thread_id, branch: t.branch, worktree: t.worktree, ...f })),
)

const reviews = results.filter(Boolean)
const escalations = []
for (const r of reviews) {
  for (const e of (r.escalate || [])) escalations.push({ thread: r.thread, ...e })
}

return { reviews, escalations }
