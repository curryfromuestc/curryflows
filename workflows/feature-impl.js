// curryflows -- feature implementation template (self-contained Workflow script).
//
// Implements a feature on an isolated branch+worktree, fitting an INDEPENDENT
// black-box test suite (tests are NOT self-generated here -- they come from the
// test-gen template). Every produced change passes the standard gate stack:
//   precheck(fail-closed) -> produce{core,docs} -> validate(real run+evidence)
//   -> cross-review(codex leg || claude leg -> arbiter, ground-truth not vote)
//   -> verdict-laundering -> bounded loop -> pre-archive guard + minimal-diff
//   -> archive gate(validation pass + real diff + rollback).
// The script only ORCHESTRATES; all editing/bash/codex-driving happens inside
// the agents it spawns. Unresolved cross-model divergence is returned as an
// `escalations` list for the coordinator to post as human decision items.
//
// args = {
//   contract: { summary, validation_command, boundaries, acceptance?,
//               minimal_diff?: {max_files,max_lines} },   // see task-contracts/feature.md
//   config:  { skillDir, projectDir, worktree, branch, maxRounds=3, codexEffort='medium' }
// }

export const meta = {
  name: 'curryflows-feature-impl',
  description: 'curryflows feature implementation: branch+worktree, fit independent black-box suite, codex+Claude cross-review + anti-fabrication gates, speculative output',
  phases: [
    { title: 'precheck' },
    { title: 'produce' },
    { title: 'validate' },
    { title: 'cross-review' },
    { title: 'archive' },
  ],
}

const PRODUCE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    filesChanged: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['filesChanged', 'summary'],
}
const VALIDATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    exitCode: { type: 'integer' },
    passed: { type: 'boolean' },
    evidencePath: { type: 'string' },
    tail: { type: 'string' },
  },
  required: ['exitCode', 'passed', 'evidencePath', 'tail'],
}
const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    reviewer: { type: 'string' },
    failed: { type: 'boolean' },
    summary: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          title: { type: 'string' }, file: { type: 'string' },
          line: { type: 'string' }, severity: { type: 'string' },
          reproducer: { type: 'string' },
        },
        required: ['title', 'severity'],
      },
    },
  },
  required: ['reviewer', 'findings', 'summary'],
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    accept: { type: 'boolean' },
    fixes: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'string' }, why: { type: 'string' } },
        required: ['title', 'why'],
      },
    },
    escalate: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { title: { type: 'string' }, divergence: { type: 'string' }, evidence: { type: 'string' }, recommendation: { type: 'string' } },
        required: ['title', 'divergence', 'recommendation'],
      },
    },
  },
  required: ['accept', 'fixes', 'escalate'],
}
const GUARD_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    fabricationSignals: { type: 'array', items: { type: 'string' } },
    filesChanged: { type: 'integer' },
    linesChanged: { type: 'integer' },
    withinMinimalDiff: { type: 'boolean' },
    rollbackCovered: { type: 'boolean' },
  },
  required: ['fabricationSignals', 'filesChanged', 'linesChanged', 'withinMinimalDiff', 'rollbackCovered'],
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
for (const f of ['summary', 'validation_command', 'boundaries']) {
  if (!K[f]) throw new Error(`precheck failed (fail-closed): contract missing required field "${f}"`)
}
if (!C.worktree || !C.branch || !C.skillDir) {
  throw new Error('precheck failed (fail-closed): config requires worktree, branch, skillDir')
}
log(`feature-impl on branch ${C.branch} (worktree ${C.worktree})`)

// ---- produce: parallel core + docs (NO self-written tests; black-box only) -
phase('produce')
const produce = await parallel([
  () => agent(
    `CORE implementation lane. Work ONLY in the worktree ${C.worktree}. Implement: ${K.summary}\n` +
    `Make the INDEPENDENT black-box suite pass: \`${K.validation_command}\`. You did NOT write that suite; treat it as the spec.\n` +
    `Boundaries (do not exceed): ${K.boundaries}. Edit source code only; do not modify the test suite. mkdir -p ${EV} for any evidence.\n` +
    `Return {filesChanged, summary}.`,
    { label: 'produce:core', phase: 'produce', agentType: 'general-purpose', schema: PRODUCE_SCHEMA }),
  () => agent(
    `DOCS lane. Work ONLY in the worktree ${C.worktree}. Update/author docs for: ${K.summary}. Boundaries: ${K.boundaries}. ` +
    `Do not modify code or tests. Return {filesChanged, summary}.`,
    { label: 'produce:docs', phase: 'produce', agentType: 'general-purpose', schema: PRODUCE_SCHEMA }),
])

// ---- bounded loop: validate -> cross-review -> (accept|escalate|repair) -----
let round = 0, accepted = false, lastValidation = null, lastVerdict = null, lastCodexOk = false
const escalations = []
while (round < MAX_ROUNDS && !accepted) {
  round++

  phase('validate')
  const v = await agent(
    `VALIDATION lane (round ${round}). Run EXACTLY: \`(cd ${C.worktree} && ${K.validation_command})\`. ` +
    `Capture the exit code and output; write the FULL output to ${EV}/validate-r${round}.log (mkdir -p ${EV} first). ` +
    `Do NOT edit code. Return {exitCode, passed (exitCode==0), evidencePath, tail (~30 lines)}.`,
    { label: `validate:r${round}`, phase: 'validate', agentType: 'Explore', schema: VALIDATE_SCHEMA })
  lastValidation = v

  phase('cross-review')
  const [codexF, claudeF] = await parallel([
    () => agent(
      `Cross-model review -- CODEX LEG (round ${round}).\n` +
      `1. Write a review prompt to ${EV}/xreview-codex-prompt-r${round}.md (mkdir -p ${EV}): "Review the change in worktree ${C.worktree} (branch ${C.branch}) against this contract: ${JSON.stringify(K)}. Inspect \`git -C ${C.worktree} diff\`. Find correctness bugs / contract deviations; file:line + concrete reproducer; rank Critical/Major/Minor."\n` +
      `2. Run: bash ${C.skillDir}/scripts/codex-review.sh --cwd ${C.worktree} --prompt-file ${EV}/xreview-codex-prompt-r${round}.md --out ${EV}/xreview-codex-r${round}.md --effort ${EFFORT} --timeout 900\n` +
      `3. The script launches codex in tmux, waits, and returns when the findings file is written. Read ${EV}/xreview-codex-r${round}.md.\n` +
      `Return {reviewer:'codex', findings:[...], summary}. If the script exits nonzero, return {reviewer:'codex', failed:true, findings:[], summary:'codex leg failed: <reason>'} -- do NOT fabricate findings.`,
      { label: `xreview:codex:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
    () => agent(
      `Cross-model review -- CLAUDE LEG (round ${round}). Independently review the change in worktree ${C.worktree} (branch ${C.branch}) against this contract: ${JSON.stringify(K)}. ` +
      `Read \`git -C ${C.worktree} diff\`. Find correctness bugs / contract deviations; file:line + concrete reproducer; rank. Do NOT edit. ` +
      `Return {reviewer:'claude', findings:[...], summary}.`,
      { label: `xreview:claude:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
  ])

  const verdict = await agent(
    `ARBITER (round ${round}). Two INDEPENDENT reviews of the same change:\n` +
    `CODEX: ${JSON.stringify(codexF)}\nCLAUDE: ${JSON.stringify(claudeF)}\nVALIDATION: ${JSON.stringify(v)}\n` +
    `Reconcile against GROUND TRUTH (the contract ${JSON.stringify(K)} and the validation result), NOT by vote:\n` +
    `- A finding both raise, contract-settled -> a real fix.\n` +
    `- A finding only one raises -> settle against ground truth; real -> fix, not-real -> drop.\n` +
    `- A finding that CANNOT be settled against ground truth (genuine divergence / contract gap) -> escalate to the human.\n` +
    `Return {accept, fixes:[{title,file,line,why}], escalate:[{title,divergence,evidence,recommendation}]}. accept=true ONLY if fixes empty AND escalate empty AND validation passed.`,
    { label: `arbiter:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: VERDICT_SCHEMA })
  lastVerdict = verdict

  // cross-model integrity: a failed codex leg means the review degraded to
  // single-model and must NOT be accepted as cross-reviewed.
  const codexOk = codexF && codexF.failed !== true
  lastCodexOk = codexOk
  // verdict laundering: never trust a one-word accept; derive the route.
  if (verdict.escalate && verdict.escalate.length) { escalations.push(...verdict.escalate) }
  const route =
    (verdict.escalate && verdict.escalate.length) ? 'escalate'
    : (verdict.accept === true && v.passed === true && codexOk && (!verdict.fixes || verdict.fixes.length === 0)) ? 'accept'
    : 'repair'
  log(`round ${round}: validation=${v.passed}, codexLeg=${codexOk ? 'ok' : 'FAILED'}, route=${route}`)
  if (route === 'accept') { accepted = true; break }
  if (route === 'escalate') break  // hand to coordinator -> decision item(s)

  phase('produce')
  if (verdict.fixes && verdict.fixes.length) {
    await agent(
      `REPAIR lane (round ${round}). Apply ONLY these arbiter fixes to the code in worktree ${C.worktree}: ${JSON.stringify(verdict.fixes)}. ` +
      `Keep the diff minimal; do not touch unrelated files. Return {filesChanged, summary}.`,
      { label: `repair:r${round}`, phase: 'produce', agentType: 'general-purpose', schema: PRODUCE_SCHEMA })
  } else {
    log(`round ${round}: no code fixes; retrying cross-review (likely a transient codex-leg failure)`)
  }
}

// cross-model integrity: if no codex leg ever succeeded, the cross-model
// guarantee is unmet -> escalate (human decides) rather than silently fail/accept.
if (!accepted && escalations.length === 0 && lastValidation && lastValidation.passed === true && !lastCodexOk) {
  escalations.push({
    title: 'cross-model review degraded: codex leg unavailable',
    divergence: `codex leg failed across ${round} round(s) (tmux/inject infra); only the Claude leg reviewed; validation is green`,
    evidence: `${EV}/`,
    recommendation: 'fix the codex tmux/inject infra and re-run, OR human-approve single-model acceptance',
  })
}

// ---- pre-archive guard + minimal-diff --------------------------------------
phase('archive')
const mdMaxFiles = (K.minimal_diff && K.minimal_diff.max_files) || 999999
const mdMaxLines = (K.minimal_diff && K.minimal_diff.max_lines) || 999999
const guard = await agent(
  `PRE-ARCHIVE GUARD for worktree ${C.worktree} (branch ${C.branch}). Inspect the REAL diff (\`git -C ${C.worktree} diff\` and committed diff vs the base). Report:\n` +
  `- fabricationSignals: any of [no real code change, low-semantic padding, out-of-scope files beyond "${K.boundaries}", premature final artifacts, nondurable evidence refs].\n` +
  `- filesChanged (int), linesChanged (int).\n` +
  `- withinMinimalDiff: filesChanged <= ${mdMaxFiles} AND linesChanged <= ${mdMaxLines}.\n` +
  `- rollbackCovered: true unless changes were made outside the worktree/branch (i.e. dropping the branch fully reverts).\n` +
  `Do NOT edit. Return the object.`,
  { label: 'pre-archive-guard', phase: 'archive', agentType: 'Explore', schema: GUARD_SCHEMA })

// ---- archive gate (fail-closed) --------------------------------------------
const archiveOk =
  accepted &&
  lastValidation && lastValidation.passed === true &&
  guard.filesChanged > 0 &&
  guard.rollbackCovered === true &&
  guard.withinMinimalDiff === true &&
  guard.fabricationSignals.length === 0

const status = archiveOk ? 'ready-to-merge'
  : escalations.length ? 'blocked-human'
  : 'failed'

// commit the speculative change onto the branch so it is a real, droppable
// commit (rollback = delete branch+worktree); the coordinator merges at the barrier.
if (archiveOk) {
  await agent(
    `COMMIT lane. In worktree ${C.worktree} on branch ${C.branch}: run \`git add -A && git commit -m "<concise message>"\` capturing: ${K.summary}. Do NOT push, do NOT merge. Return {filesChanged, summary}.`,
    { label: 'commit', phase: 'archive', agentType: 'general-purpose', schema: PRODUCE_SCHEMA })
}

log(`feature-impl done: status=${status}, rounds=${round}, escalations=${escalations.length}`)

return {
  template: 'feature',
  status,
  branch: C.branch,
  worktree: C.worktree,
  rounds: round,
  produce,
  validation: lastValidation,
  verdict: lastVerdict,
  guard,
  escalations,  // coordinator posts these as human decision items (model-divergence barrier)
}
