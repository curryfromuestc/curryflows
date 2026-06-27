// curryflows -- test generation template (self-contained Workflow).
//
// Produces an INDEPENDENT black-box test suite (the shared oracle the feature
// and perf templates validate against). Tests target the public contract, not
// implementation internals. Gate stack with a test-specific HARD-STOP
// (meaningfulness negative control: a planted fault MUST make a new test fail,
// so the suite is not trivially-true):
//   precheck(fail-closed) -> inspect-coverage -> generate-tests(once)
//   -> validate(tests pass on current code) -> meaningfulness(negative control)
//   -> cross-review(codex||claude->arbiter) -> verdict-laundering
//   -> bounded loop(repair-tests, NOT regenerate) -> archive gate.
//
// args = {
//   contract: { summary, test_command, coverage_target, gap_scope, boundaries },  // task-contracts/test.md
//   config:  { skillDir, projectDir, worktree, branch, maxRounds=3, codexEffort='medium' }
// }

export const meta = {
  name: 'curryflows-test-gen',
  description: 'curryflows test generation: independent black-box suite, meaningfulness negative-control hard-stop, codex+Claude cross-review + gates, becomes the oracle for other templates',
  phases: [
    { title: 'precheck' },
    { title: 'inspect' },
    { title: 'generate' },
    { title: 'validate' },
    { title: 'meaningfulness' },
    { title: 'cross-review' },
    { title: 'archive' },
  ],
}

const PRODUCE_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { filesChanged: { type: 'array', items: { type: 'string' } }, summary: { type: 'string' } }, required: ['filesChanged', 'summary'] }
const VALIDATE_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { exitCode: { type: 'integer' }, passed: { type: 'boolean' }, coverage: { type: 'number' }, evidencePath: { type: 'string' }, tail: { type: 'string' } },
  required: ['exitCode', 'passed', 'evidencePath', 'tail'] }
const NEGCTRL_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { faultInjected: { type: 'string' }, aTestFailed: { type: 'boolean' }, whichFailed: { type: 'string' }, summary: { type: 'string' } },
  required: ['faultInjected', 'aTestFailed', 'summary'] }
const FINDINGS_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { reviewer: { type: 'string' }, failed: { type: 'boolean' }, summary: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', additionalProperties: false,
      properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'string' }, severity: { type: 'string' }, reproducer: { type: 'string' } }, required: ['title', 'severity'] } } },
  required: ['reviewer', 'findings', 'summary'] }
const VERDICT_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { accept: { type: 'boolean' },
    fixes: { type: 'array', items: { type: 'object', additionalProperties: false, properties: { title: { type: 'string' }, file: { type: 'string' }, line: { type: 'string' }, why: { type: 'string' } }, required: ['title', 'why'] } },
    escalate: { type: 'array', items: { type: 'object', additionalProperties: false, properties: { title: { type: 'string' }, divergence: { type: 'string' }, evidence: { type: 'string' }, recommendation: { type: 'string' } }, required: ['title', 'divergence', 'recommendation'] } } },
  required: ['accept', 'fixes', 'escalate'] }
const GUARD_SCHEMA = { type: 'object', additionalProperties: false,
  properties: { fabricationSignals: { type: 'array', items: { type: 'string' } }, filesChanged: { type: 'integer' }, rollbackCovered: { type: 'boolean' } },
  required: ['fabricationSignals', 'filesChanged', 'rollbackCovered'] }

let _A = args
if (typeof _A === 'string') { try { _A = JSON.parse(_A) } catch (e) { _A = {} } }
const K = (_A && _A.contract) || {}
const C = (_A && _A.config) || {}
const MAX_ROUNDS = C.maxRounds || 3
const EFFORT = C.codexEffort || 'medium'
const EV = `${C.worktree}/.curryflows`

// ---- precheck (fail-closed) ------------------------------------------------
phase('precheck')
for (const f of ['summary', 'test_command', 'coverage_target', 'gap_scope', 'boundaries']) {
  if (!K[f]) throw new Error(`precheck failed (fail-closed): contract missing required field "${f}"`)
}
if (!C.worktree || !C.branch || !C.skillDir) throw new Error('precheck failed: config requires worktree, branch, skillDir')

// ---- inspect coverage gaps -------------------------------------------------
phase('inspect')
const gaps = await agent(
  `COVERAGE INSPECTION lane. In ${C.worktree}: run \`${K.test_command}\` with coverage and identify uncovered behavior within the scope "${K.gap_scope}". ` +
  `Write the gap report to ${EV}/gap-report.md (mkdir -p ${EV}). Do NOT edit. Return {filesChanged:[], summary (the prioritized gaps)}.`,
  { label: 'inspect-coverage', phase: 'inspect', agentType: 'Explore', schema: PRODUCE_SCHEMA })

// ---- generate tests ONCE (black-box: test the public contract) -------------
phase('generate')
const gen = await agent(
  `TEST GENERATION lane (run ONCE). In worktree ${C.worktree}, author BLACK-BOX tests that close the gaps in "${K.gap_scope}" (see ${EV}/gap-report.md). ` +
  `Test the PUBLIC contract / observable behavior, NOT implementation internals -- do not read private internals to over-fit. Tests must be runnable by \`${K.test_command}\`. ` +
  `Boundaries: ${K.boundaries}. Edit ONLY test files; do not modify the code under test. Return {filesChanged, summary}.`,
  { label: 'generate-tests', phase: 'generate', agentType: 'general-purpose', schema: PRODUCE_SCHEMA })

// ---- bounded loop: validate -> negctrl -> cross-review -> (accept|escalate|repair)
let round = 0, accepted = false, lastValidation = null, lastNeg = null, lastVerdict = null, lastCodexOk = false
const escalations = []
while (round < MAX_ROUNDS && !accepted) {
  round++

  phase('validate')
  const v = await agent(
    `VALIDATION lane (round ${round}). In ${C.worktree}: run \`(cd ${C.worktree} && ${K.test_command})\` with coverage. The NEW tests must PASS on the current (assumed-correct) code. ` +
    `Write full output to ${EV}/validate-r${round}.log. Do NOT edit. Return {exitCode, passed (exit==0), coverage (percent in "${K.gap_scope}" if available), evidencePath, tail}.`,
    { label: `validate:r${round}`, phase: 'validate', agentType: 'Explore', schema: VALIDATE_SCHEMA })
  lastValidation = v

  // HARD-STOP: meaningfulness negative control in an isolated worktree.
  phase('meaningfulness')
  const neg = await agent(
    `MEANINGFULNESS NEGATIVE CONTROL (round ${round}). You are in a fresh isolated worktree of the project. ` +
    `Inject ONE plausible fault into the code under test within "${K.gap_scope}" (e.g. flip a comparison, off-by-one, drop a guard). Then run \`${K.test_command}\`. ` +
    `At least one of the newly generated tests MUST fail -- that proves the suite is not trivially-true. ` +
    `Return {faultInjected (what you changed), aTestFailed (bool), whichFailed, summary}. Do NOT report aTestFailed=true unless you actually observed a failure.`,
    { label: `negctrl:r${round}`, phase: 'meaningfulness', agentType: 'general-purpose', isolation: 'worktree', schema: NEGCTRL_SCHEMA })
  lastNeg = neg

  phase('cross-review')
  const [codexF, claudeF] = await parallel([
    () => agent(
      `Cross-model review -- CODEX LEG (round ${round}).\n` +
      `1. Write a prompt to ${EV}/xreview-codex-prompt-r${round}.md: "Review the NEW tests in worktree ${C.worktree} (branch ${C.branch}) vs contract ${JSON.stringify(K)}. Inspect git diff. Check: are they black-box (no over-fitting to internals)? do they close the declared gaps? any trivially-true / tautological assertions? file:line; rank."\n` +
      `2. Run: bash ${C.skillDir}/scripts/codex-review.sh --cwd ${C.worktree} --prompt-file ${EV}/xreview-codex-prompt-r${round}.md --out ${EV}/xreview-codex-r${round}.md --effort ${EFFORT} --timeout 900\n` +
      `3. Read the findings file. Return {reviewer:'codex', findings, summary}. On nonzero exit return {reviewer:'codex', failed:true, findings:[], summary:'codex leg failed: <reason>'} -- no fabrication.`,
      { label: `xreview:codex:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
    () => agent(
      `Cross-model review -- CLAUDE LEG (round ${round}). Independently review the NEW tests in worktree ${C.worktree} vs contract ${JSON.stringify(K)}. Read git diff. ` +
      `Check black-box-ness, gap closure, trivially-true assertions, over-fitting. file:line; rank. No edits. Return {reviewer:'claude', findings, summary}.`,
      { label: `xreview:claude:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: FINDINGS_SCHEMA }),
  ])

  const verdict = await agent(
    `ARBITER (round ${round}). CODEX: ${JSON.stringify(codexF)}\nCLAUDE: ${JSON.stringify(claudeF)}\nVALIDATION: ${JSON.stringify(v)}\nNEGATIVE_CONTROL: ${JSON.stringify(neg)}\n` +
    `Reconcile against GROUND TRUTH (contract ${JSON.stringify(K)}, the validation, and the negative control), NOT by vote. Both-raise+settled -> fix; one-raise -> settle; unsettlable -> escalate. ` +
    `Return {accept, fixes, escalate}. accept=true ONLY if fixes empty AND escalate empty AND validation passed AND the negative control failed a test (aTestFailed==true).`,
    { label: `arbiter:r${round}`, phase: 'cross-review', agentType: 'Explore', schema: VERDICT_SCHEMA })
  lastVerdict = verdict

  const codexOk = codexF && codexF.failed !== true
  lastCodexOk = codexOk
  if (verdict.escalate && verdict.escalate.length) escalations.push(...verdict.escalate)
  const route =
    (verdict.escalate && verdict.escalate.length) ? 'escalate'
    : (verdict.accept === true && v.passed === true && neg.aTestFailed === true && codexOk && (!verdict.fixes || verdict.fixes.length === 0)) ? 'accept'
    : 'repair'
  log(`round ${round}: passed=${v.passed}, negctrl=${neg.aTestFailed}, codexLeg=${codexOk ? 'ok' : 'FAILED'}, route=${route}`)
  if (route === 'accept') { accepted = true; break }
  if (route === 'escalate') break

  phase('generate')
  await agent(
    `REPAIR-TESTS lane (round ${round}). Improve the EXISTING tests in worktree ${C.worktree} per the arbiter fixes: ${JSON.stringify(verdict.fixes)}. ` +
    `Do NOT regenerate from scratch; refine. Keep them black-box. Edit only test files. Return {filesChanged, summary}.`,
    { label: `repair-tests:r${round}`, phase: 'generate', agentType: 'general-purpose', schema: PRODUCE_SCHEMA })
}

// cross-model integrity: no successful codex leg -> escalate, never silently pass/fail.
if (!accepted && escalations.length === 0 && lastValidation && lastValidation.passed === true && !lastCodexOk) {
  escalations.push({
    title: 'cross-model review degraded: codex leg unavailable',
    divergence: `codex leg failed across ${round} round(s); only the Claude leg reviewed; validation is green`,
    evidence: `${EV}/`,
    recommendation: 'fix the codex tmux/inject infra and re-run, OR human-approve single-model acceptance',
  })
}

// ---- pre-archive guard + archive gate --------------------------------------
phase('archive')
const guard = await agent(
  `PRE-ARCHIVE GUARD for worktree ${C.worktree} (branch ${C.branch}). Inspect the real diff. fabricationSignals (e.g. only test files? any code-under-test modified? trivially-true tests? out-of-scope beyond "${K.boundaries}"), filesChanged, rollbackCovered. No edits. Return the object.`,
  { label: 'pre-archive-guard', phase: 'archive', agentType: 'Explore', schema: GUARD_SCHEMA })

const archiveOk = accepted &&
  lastValidation && lastValidation.passed === true &&
  lastNeg && lastNeg.aTestFailed === true &&
  guard.filesChanged > 0 && guard.rollbackCovered === true && guard.fabricationSignals.length === 0
const status = archiveOk ? 'ready-to-merge' : escalations.length ? 'blocked-human' : 'failed'
log(`test-gen done: status=${status}, rounds=${round}`)

return {
  template: 'test', status, branch: C.branch, worktree: C.worktree, rounds: round,
  gaps, gen, validation: lastValidation, negativeControl: lastNeg, verdict: lastVerdict, guard, escalations,
}
