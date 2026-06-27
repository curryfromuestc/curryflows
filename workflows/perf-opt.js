// curryflows -- performance optimization template (self-contained Workflow).
//
// Search for a measured speedup across competing strategies WITHOUT regressing
// correctness. Gate stack with a perf-specific HARD-STOP (correctness-vs-speed:
// no win is acceptable unless the correctness suite stays green):
//   precheck(fail-closed) -> capture-baseline(real bench)
//   -> parallel{strategy lanes, each in its OWN worktree} -> select-best
//   -> HARD-STOP(correctness green AND beats baseline) -> apply winner
//   -> validate -> cross-review(codex||claude->arbiter) -> verdict-laundering
//   -> bounded loop -> pre-archive guard -> archive gate.
// Strategy lanes mutate the same code, so they run isolated (isolation:'worktree')
// and return a patch + metrics; the winner is applied to the thread worktree.
//
// args = {
//   contract: { summary, benchmark_command, target_metric, validation_command (correctness),
//               strategies: [ "name: how" , ... ], boundaries },   // task-contracts/perf.md
//   config:  { skillDir, projectDir, worktree, branch, maxRounds=3, codexEffort='medium' }
// }

export const meta = {
  name: 'curryflows-perf-opt',
  description: 'curryflows performance optimization: baseline, isolated strategy search, correctness-vs-speed hard-stop, apply winner, codex+Claude cross-review + gates',
  phases: [
    { title: 'precheck' },
    { title: 'baseline' },
    { title: 'search' },
    { title: 'apply' },
    { title: 'validate' },
    { title: 'cross-review' },
    { title: 'archive' },
  ],
}

const METRIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { metric: { type: 'number' }, raw: { type: 'string' }, correctnessPassed: { type: 'boolean' } },
  required: ['metric', 'raw', 'correctnessPassed'],
}
const CAND_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    strategy: { type: 'string' }, metric: { type: 'number' }, correctnessPassed: { type: 'boolean' },
    patch: { type: 'string' }, summary: { type: 'string' }, failed: { type: 'boolean' },
  },
  required: ['strategy', 'metric', 'correctnessPassed', 'patch', 'summary'],
}
const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    reviewer: { type: 'string' }, failed: { type: 'boolean' }, summary: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', additionalProperties: false,
      properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'string' }, severity: { type: 'string' }, reproducer: { type: 'string' } },
      required: ['title', 'severity'] } },
  },
  required: ['reviewer', 'findings', 'summary'],
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    accept: { type: 'boolean' },
    fixes: { type: 'array', items: { type: 'object', additionalProperties: false, properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'string' }, why: { type: 'string' } }, required: ['title', 'why'] } },
    escalate: { type: 'array', items: { type: 'object', additionalProperties: false, properties: { title: { type: 'string' }, divergence: { type: 'string' }, evidence: { type: 'string' }, recommendation: { type: 'string' } }, required: ['title', 'divergence', 'recommendation'] } },
  },
  required: ['accept', 'fixes', 'escalate'],
}
const GUARD_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    fabricationSignals: { type: 'array', items: { type: 'string' } },
    filesChanged: { type: 'integer' }, linesChanged: { type: 'integer' },
    rollbackCovered: { type: 'boolean' },
  },
  required: ['fabricationSignals', 'filesChanged', 'linesChanged', 'rollbackCovered'],
}

let _A = args
if (typeof _A === 'string') { try { _A = JSON.parse(_A) } catch (e) { _A = {} } }
const K = (_A && _A.contract) || {}
const C = (_A && _A.config) || {}
const MAX_ROUNDS = C.maxRounds || 3
const EFFORT = C.codexEffort || 'medium'
const EV = `${C.worktree}/.curryflows`

// ---- precheck (fail-closed) ------------------------------------------------
phase('precheck')
for (const f of ['summary', 'benchmark_command', 'target_metric', 'validation_command', 'strategies']) {
  if (!K[f] || (f === 'strategies' && !K.strategies.length)) {
    throw new Error(`precheck failed (fail-closed): contract missing required field "${f}"`)
  }
}
if (!C.worktree || !C.branch || !C.skillDir) throw new Error('precheck failed: config requires worktree, branch, skillDir')

// ---- capture baseline (real benchmark + correctness) -----------------------
phase('baseline')
const baseline = await agent(
  `BASELINE lane. In ${C.worktree}: run the benchmark \`${K.benchmark_command}\` and the correctness suite \`${K.validation_command}\`. ` +
  `Write raw output to ${EV}/baseline.log (mkdir -p ${EV}). Do NOT edit code. ` +
  `Return {metric (the measured ${K.target_metric}, as a number), raw, correctnessPassed (correctness suite exit==0)}.`,
  { label: 'baseline', phase: 'baseline', agentType: 'Explore', schema: METRIC_SCHEMA })
if (baseline.correctnessPassed !== true) {
  throw new Error('precheck failed (fail-closed): correctness suite is not green at baseline; fix correctness before optimizing')
}
log(`baseline ${K.target_metric}=${baseline.metric}`)

// ---- strategy search: each strategy in its OWN worktree (no clobber) --------
phase('search')
const candidates = (await parallel(K.strategies.map((s, i) => () =>
  agent(
    `STRATEGY lane ${i} ("${s}"). You are in a fresh isolated worktree of the project. Apply ONLY this optimization strategy to the code: ${s}\n` +
    `Then run the benchmark \`${K.benchmark_command}\` and the correctness suite \`${K.validation_command}\`. ` +
    `Boundaries: ${K.boundaries}. ` +
    `Return {strategy:"${s}", metric (measured ${K.target_metric} number), correctnessPassed (correctness exit==0), patch (\`git diff\` of your change as text), summary}. ` +
    `If the strategy is infeasible, return metric equal to the baseline and failed:true with an explanation in summary -- do NOT fabricate a speedup.`,
    { label: `strategy:${i}`, phase: 'search', agentType: 'general-purpose', isolation: 'worktree', schema: CAND_SCHEMA })
))).filter(Boolean)

// ---- HARD-STOP: correctness-vs-speed (the perf-specific pre-apply gate) -----
// Eligible ONLY if correctness stayed green AND it actually beat baseline.
// (target_metric semantics: lower-is-better unless contract says otherwise.)
const lowerIsBetter = K.lower_is_better !== false
const eligible = candidates.filter(c =>
  c.correctnessPassed === true && !c.failed &&
  (lowerIsBetter ? c.metric < baseline.metric : c.metric > baseline.metric))
if (eligible.length === 0) {
  log('HARD-STOP: no candidate both kept correctness green and beat baseline')
  return {
    template: 'perf', status: 'failed', branch: C.branch, worktree: C.worktree,
    baseline, candidates, escalations: [],
    reason: 'no correctness-preserving speedup found',
  }
}
eligible.sort((a, b) => lowerIsBetter ? a.metric - b.metric : b.metric - a.metric)
const winner = eligible[0]
log(`winner: "${winner.strategy}" ${K.target_metric}=${winner.metric} (baseline ${baseline.metric})`)

// ---- apply the winning patch to the thread worktree ------------------------
phase('apply')
await agent(
  `APPLY lane. Apply this winning optimization patch to the code in worktree ${C.worktree}, then commit on branch ${C.branch}.\n` +
  `Strategy: ${winner.strategy}\nPatch:\n${winner.patch}\n` +
  `If the patch does not apply cleanly, re-implement the same strategy by hand to match it. Return {filesChanged, summary}.`,
  { label: 'apply-winner', phase: 'apply', agentType: 'general-purpose',
    schema: { type: 'object', additionalProperties: false, properties: { filesChanged: { type: 'array', items: { type: 'string' } }, summary: { type: 'string' } }, required: ['filesChanged', 'summary'] } })

// ---- bounded loop: validate -> cross-review -> (accept|escalate|repair) -----
let round = 0, accepted = false, lastValidation = null, lastVerdict = null
const escalations = []
while (round < MAX_ROUNDS && !accepted) {
  round++
  phase('validate')
  const v = await agent(
    `VALIDATION lane (round ${round}). In ${C.worktree}: run the correctness suite \`(cd ${C.worktree} && ${K.validation_command})\` AND re-run the benchmark \`${K.benchmark_command}\`. ` +
    `Write full output to ${EV}/validate-r${round}.log. Do NOT edit. Return {exitCode, passed (correctness exit==0 AND metric still beats baseline ${baseline.metric}), evidencePath, tail}.`,
    { label: `validate:r${round}`, phase: 'validate', agentType: 'Explore',
      schema: { type: 'object', additionalProperties: false, properties: { exitCode: { type: 'integer' }, passed: { type: 'boolean' }, evidencePath: { type: 'string' }, tail: { type: 'string' } }, required: ['exitCode', 'passed', 'evidencePath', 'tail'] } })
  lastValidation = v

  phase('cross-review')
  const [codexF, claudeF] = await parallel([
    () => agent(
      `Cross-model review -- CODEX LEG (round ${round}).\n` +
      `1. Write a prompt to ${EV}/xreview-codex-prompt-r${round}.md: "Review the optimization in worktree ${C.worktree} (branch ${C.branch}) vs contract ${JSON.stringify(K)}. Inspect git diff. Look for: correctness-breaking shortcuts, benchmark gaming (measuring the wrong thing), undefined behavior, hidden regressions. file:line + reproducer; rank."\n` +
      `2. Run: bash ${C.skillDir}/scripts/codex-review.sh --cwd ${C.worktree} --prompt-file ${EV}/xreview-codex-prompt-r${round}.md --out ${EV}/xreview-codex-r${round}.md --effort ${EFFORT} --timeout 900\n` +
      `3. Read the findings file. Return {reviewer:'codex', findings, summary}. On nonzero exit return {reviewer:'codex', failed:true, findings:[], summary:'codex leg failed: <reason>'} -- no fabrication.`,
      { label: `xreview:codex:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
    () => agent(
      `Cross-model review -- CLAUDE LEG (round ${round}). Independently review the optimization in worktree ${C.worktree} vs contract ${JSON.stringify(K)}. Read git diff. ` +
      `Look for correctness shortcuts, benchmark gaming, hidden regressions. file:line + reproducer; rank. No edits. Return {reviewer:'claude', findings, summary}.`,
      { label: `xreview:claude:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
  ])

  const verdict = await agent(
    `ARBITER (round ${round}). CODEX: ${JSON.stringify(codexF)}\nCLAUDE: ${JSON.stringify(claudeF)}\nVALIDATION: ${JSON.stringify(v)}\n` +
    `Reconcile against GROUND TRUTH (contract ${JSON.stringify(K)}, the benchmark, and the correctness result), NOT by vote. Both-raise+settled -> fix; one-raise -> settle vs ground truth; unsettlable -> escalate. ` +
    `Return {accept, fixes, escalate}. accept=true ONLY if fixes empty AND escalate empty AND validation passed (correctness green AND still beats baseline).`,
    { label: `arbiter:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: VERDICT_SCHEMA })
  lastVerdict = verdict

  if (verdict.escalate && verdict.escalate.length) escalations.push(...verdict.escalate)
  const route =
    (verdict.escalate && verdict.escalate.length) ? 'escalate'
    : (verdict.accept === true && v.passed === true && (!verdict.fixes || verdict.fixes.length === 0)) ? 'accept'
    : 'repair'
  log(`round ${round}: passed=${v.passed}, route=${route}`)
  if (route === 'accept') { accepted = true; break }
  if (route === 'escalate') break

  phase('apply')
  await agent(
    `REPAIR lane (round ${round}). Apply ONLY these arbiter fixes in worktree ${C.worktree}: ${JSON.stringify(verdict.fixes)}. Keep the diff minimal; preserve the speedup AND correctness. Return {filesChanged, summary}.`,
    { label: `repair:r${round}`, phase: 'apply', agentType: 'general-purpose',
      schema: { type: 'object', additionalProperties: false, properties: { filesChanged: { type: 'array', items: { type: 'string' } }, summary: { type: 'string' } }, required: ['filesChanged', 'summary'] } })
}

// ---- pre-archive guard + archive gate --------------------------------------
phase('archive')
const guard = await agent(
  `PRE-ARCHIVE GUARD for worktree ${C.worktree} (branch ${C.branch}). Inspect the real diff. Report fabricationSignals (e.g. benchmark gaming, correctness suite weakened, out-of-scope files beyond "${K.boundaries}"), filesChanged, linesChanged, rollbackCovered. No edits. Return the object.`,
  { label: 'pre-archive-guard', phase: 'archive', agentType: 'Explore', schema: GUARD_SCHEMA })

const archiveOk = accepted && lastValidation && lastValidation.passed === true &&
  guard.filesChanged > 0 && guard.rollbackCovered === true && guard.fabricationSignals.length === 0
const status = archiveOk ? 'ready-to-merge' : escalations.length ? 'blocked-human' : 'failed'
log(`perf-opt done: status=${status}, rounds=${round}`)

return {
  template: 'perf', status, branch: C.branch, worktree: C.worktree, rounds: round,
  baseline, winner, candidates, validation: lastValidation, verdict: lastVerdict, guard, escalations,
}
