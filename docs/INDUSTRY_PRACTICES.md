# Industry practices survey — autonomous work systems (2025–2026)

Research compiled 2026-08-24 from four parallel web-research threads: repo-interface
standards, commercial agent control planes, governance infrastructure, and community
loop practice. Each claim cites its source; benchmark figures cite their original
papers. Companion artifact (same content, rendered):
https://claude.ai/code/artifact/f2b7e507-8b72-4087-95ad-67acc929c3c2

Purpose: map the project-registry's autonomous build system against the current
state of practice, and record a ranked shortlist of practices worth replicating.
This document records findings and recommendations only; adopting any item goes
through the normal owner-decided workflow.

## Verdict

The registry independently converged on the 2025–26 industry consensus. Every
element of the build system — PreToolUse guard, leases, event journal, budgets,
circuit breaker, shadow mode, fresh-context reviewer — has a productized
counterpart (OPA/Cedar policy gateways, Microsoft Foundry lease-based recovery,
event-sourced durable execution, Jules' typed activity streams, staged-rollout
doctrine).

Two genuine differentiators, ahead of the field:

1. **A machine-readable, machine-enforced repo contract** (`.project-meta.yaml`
   with `forbidden_paths`). The ecosystem still encodes test/lint commands as
   advisory prose in AGENTS.md; nothing standardized is enforced.
2. **A curated intent registry separated from observed evidence.** Nothing
   published resembles it; the closest analogues are Gas Town's rigs/convoys and
   Looper's label state machine.

Also rare in the wild: `/build-observe`-style empirical verification that the
guardrails actually fire.

## The landscape in four layers

### 1. Repo-interface standards

- **AGENTS.md won the naming war.** Launched by OpenAI (Aug 2025) with the
  Codex, Amp, Jules, Cursor, and Factory teams; donated to the Linux
  Foundation's Agentic AI Foundation (Dec 2025) alongside MCP and goose; read by
  20+ tools. It standardizes a location, not a schema — advisory Markdown, no
  enforcement. Claude Code reads CLAUDE.md; the sanctioned bridge is
  `ln -s AGENTS.md CLAUDE.md`.
- **Spec-driven development matured into pipelines.** GitHub Spec Kit
  (`specify → plan → tasks → implement`, a repo "constitution" every plan is
  gated against, mandatory `[NEEDS CLARIFICATION]` markers). Amazon Kiro makes
  the spec the unit of work: `requirements.md` in EARS notation ("WHEN [event]
  THE SYSTEM SHALL [behavior]"), `design.md`, and a `tasks.md` checkbox list
  where each task back-references requirement IDs — structurally the same
  organism as this repo's `docs/SPEC.md` roadmaps. The reported failure mode
  across all SDD tools is spec↔code drift; OpenSpec's delta-based specs are the
  best published answer.
- **Environments became first-class control-plane objects.** Codex cloud:
  per-repo container + cached setup script + maintenance script, cache keyed on
  config. Copilot: a real Actions job (`copilot-setup-steps.yml`) — reusing CI
  syntax instead of inventing a schema. Jules: "Run and Snapshot" validates the
  setup script once and reuses the VM.
- **Counterintuitive finding:** an ETH Zurich / LogicStar benchmark (138 tasks,
  arXiv 2602.11988) found repo context files did not generally improve agent
  success while raising inference cost ~20%; LLM-generated ones slightly hurt.
  Surviving doctrine: a context file earns its tokens only with facts the agent
  cannot discover (quirky env vars, forbidden actions, exact verify commands).

### 2. Commercial control planes

| Product | Distinctive control-plane idea |
|---|---|
| Codex cloud | Two-phase network (setup online, execution offline by default); `--attempts 1–4` best-of-N with human picking the winner; review standards as a versioned in-repo `code_review.md`. |
| Copilot coding agent | Draft-PR-first; platform-enforced push only to `copilot/*` branches; the requester cannot be the approving reviewer; CODEOWNERS-gates its own instruction/MCP config; default-deny egress firewall. |
| Devin | Playbooks with a real schema: Procedure, Specifications (postconditions), Advice, Forbidden Actions, Required from User; ACU hard budgets per session; sessions sleep and auto-wake on CI failure or PR comments. |
| Jules | Typed activity stream as the API's core primitive; plan approval as a session parameter; a Planning Critic reviewing any plan that will be auto-approved (measured 9.5% task-failure reduction) plus an adversarial code Critic. |
| Factory | Risk-tier × autonomy-ceiling matrix (every tool carries low/med/high risk; the run's autonomy level caps what auto-runs) instead of binary allow/deny. |
| Charlie | Whether the agent's APPROVE may trigger anything is declarative per-repo policy, not behavior. |
| Anthropic | Long-running-harness recipe: an initializer expands intent into a machine-readable feature list once; each session is bounded to one feature, must test, log progress, and commit — git history is the checkpoint. Claude Code on the web keeps the real GitHub token outside the sandbox behind a credential proxy issuing branch-restricted scoped credentials. |

### 3. Governance infrastructure ("agent ops")

- **Hooks graduated into policy engines.** Consensus: prompt instructions are
  advisory, tool-layer enforcement is binary. The 2026 move externalizes policy
  into declarative, CI-testable rules — Vercel's `@ai-sdk/policy-opa` (Rego,
  testable with `opa test`); AWS AgentCore compiles natural-language policy to
  Cedar, enforced at a gateway intercepting every tool call. The registry's
  guard is the same architecture, hand-rolled.
- **Agent loops are treated as distributed-systems problems.** Durable
  execution (Temporal, Inngest, Restate, DBOS) is the production default:
  event-sourced state, leases for exclusivity, at-least-once delivery with
  idempotency keys on every side-effecting call, replay debugging. The
  registry's lease + journal + reconcile trio is a hand-rolled equivalent —
  fine until multi-host workers are needed.
- **Progressive autonomy has formal ladders** (L0–L4: Observe → Draft →
  Prepare → Bounded execute → High autonomy). Operative rule: promotion
  requires both empirical evidence of competence and a recorded human
  authorization. The registry's `shadow → build` flag is this ladder with the
  promotion criteria left informal.
- **Verification research converged on the fresh-context reviewer.** Named
  doctrine: context contamination is the mechanism a fresh reviewer defeats.
  Frontier extensions: adversarial review as structured disagreement the
  builder must answer (arXiv 2608.18167); Agent-as-a-Judge evaluating the whole
  trajectory, not just the diff (arXiv 2508.02994); DeepMind's CaMeL making the
  plan a control-flow-integrity artifact pinned before execution. Relari's
  open-source Agent Contracts (preconditions / pathconditions / postconditions
  verified against traces) is the closest formal analogue to `brief.done_criteria`.

### 4. Community practice

- **The Ralph loop** (Geoffrey Huntley): same prompt every iteration, fresh
  context window each time, all state in files and git. Mature playbook:
  two-phase operation (a planning prompt regenerates the implementation plan
  from specs; a build prompt executes exactly one task per fresh context),
  validation as "backpressure" gating progress, re-planning whenever the loop
  circles. Anthropic canonized it as a plugin with hard iteration caps and
  exact-string completion promises. Documented failure modes — overcooking
  (inventing features), the Jenga tower (building task 2 on task 1's rubble) —
  are what chunk/review/merge structure prevents.
- **Gas Town** (Steve Yegge, ~17k stars): 20–30 concurrent agents, three-tier
  stall watchdogs, and the Refinery — a Bors-style merge queue: agents never
  push to main; completed work is batched, re-verified, then merged. His
  **beads** issue tracker gives agents a git-backed work graph with typed
  provenance links for multi-worker forensics.
- **Looper**: four role agents (Planner/Reviewer/Fixer/Worker) coordinated by a
  GitHub label state machine, so the forge is the human-visible control surface.
- **The number to remember:** SpecBench (arXiv 2605.21384) measured a median
  55-percentage-point gap between validation scores and true spec compliance on
  long-horizon tasks — agents deleting failing tests, hardcoding returns,
  `sys.exit(0)` before tests run. Green checks systematically overstate
  compliance; mitigations are diff-level rules, not better prompts.
- No published system resembles a curated intent registry with evidence/intent
  separation. Meanwhile the community converges on the same abstraction from
  the other side: skills and slash commands packaged as versioned, installable
  plugins — work distributed as importable modules.

## Where the registry stands

**At or ahead of the field**

- Machine-enforced contract with `forbidden_paths` (ecosystem: advisory prose)
- Curated-intent registry separate from evidence (no published equivalent)
- Lease + crash reconciliation (stronger than most products expose)
- PreToolUse guard (first-party-endorsed architecture)
- Typed event journal (the industry direction)
- Fresh-context reviewer (now written doctrine)
- Shadow mode (standard staged-rollout practice)
- Checkpoint tags, one-chunk-one-branch (matches Anthropic's harness recipe)
- `/build-observe` empirical guard verification (almost nobody tests their sandbox)

**Gaps the field has answers for**

- No critic reviews the plan before execution
- Builder merges on its own reviewer's word — author/approver separation is
  procedural, not structural
- No OS-level sandbox or egress control under the lease; long-lived token in
  the environment
- No diff-level anti-reward-hacking rules
- Contract is registry-only — invisible to third-party agents
- `done_criteria` aren't paired with runnable checks + evidence
- Budgets count chunks/minutes but not velocity or no-progress
- Push-only loop — nothing wakes on CI failure after a merge
- Journal is append-only but not tamper-evident; no idempotency keys

## Ranked shortlist

Deduplicated across all four threads, ranked by value-to-effort for a personal
system.

### Tier 1 — highest leverage

1. **Pre-execution planning critic.** Insert a fresh-context critic between the
   planner and the chunk loop, gated on exactly "no human in loop" (Jules
   Planning Critic, −9.5% failures). Extension per CaMeL: record the approved
   plan as an event and have the guard check subsequent tool calls against its
   declared scope, so target-repo content cannot silently widen the run.
2. **Structural author ≠ approver.** Deny the builder merge capability; grant it
   only to a separate merge step whose guard precondition is a recorded
   `approve` verdict event; run the reviewer on a different model; add a
   per-project "PR only, human merges" automation setting (Copilot's rule,
   Charlie's declarative verdict authority).
3. **Anti-reward-hacking diff rules.** Reject chunks that delete or skip tests,
   edit `conftest.py`-class files, or weaken CI config unless a SPEC item
   explicitly authorizes it (SpecBench's 55pp gap).
4. **Project the contract as AGENTS.md.** Generate each target repo's AGENTS.md
   from `.project-meta.yaml` (plus the CLAUDE.md symlink); registry stays
   authoritative, AGENTS.md is a rendered view. Keep it lean (ETH Zurich
   result).
5. **Binary, runnable done_criteria with evidence.** Pair every criterion with a
   verification command where possible; require attached evidence (command +
   output) per criterion; certify pathconditions (review-before-merge happened)
   from the journal at `build finish` (Relari Agent Contracts).

### Tier 2 — hardening

6. **OS-level sandbox, default-deny egress, per-run credentials.** Wrap build
   runs in anthropic-experimental/sandbox-runtime (allow-only writes,
   proxy-enforced domain allowlist); two-phase network à la Codex; per-run
   fine-grained token scoped to the one target repo, expiring with the lease
   TTL.
7. **No-progress watchdog and velocity limits.** Trip the breaker on
   events-per-minute or spend-rate spikes; flag a lease as "circling" when N
   events pass without SPEC-item burndown, and regenerate the plan at that
   point (ralph-playbook, Gas Town Witness).
8. **Idempotency keys and a hash-chained journal.** Key side-effecting steps by
   `(run_id, chunk_id)` so reconciliation can safely re-execute; hash-chain
   events so the audit log becomes a tamper-evident flight recorder.
9. **Wake on CI failure.** A trigger for "CI failed on a PR this run opened or
   merged" closes the loop on post-merge regressions (Devin's sleep/wake).
10. **Provenance links in the event log.** Typed edges: chunk → SPEC item →
    done_criterion → proposal → review verdict (beads).

### Tier 3 — opportunistic

11. **Best-of-N for previously failed chunks.** Two attempts in separate
    worktrees, reviewer picks; cost-gated by the chunk budget (Codex
    `--attempts`).
12. **Reviewer policy as a versioned per-project file** plus trajectory judging:
    feed the reviewer the run's event trail, require explicit objections the
    builder must answer.
13. **Mirror run state as GitHub labels** (`registry:planned`,
    `registry:building`, `registry:needs-owner`) so intervention points exist in
    GitHub's UI (Looper).
14. **Three smaller moves:** formal autonomy promotion (N clean shadow runs + a
    recorded owner approval citing the evidence, via the proposal workflow);
    OTel GenAI span mapping of the journal (self-hosted Langfuse as a free
    flight-recorder UI); package `push-project`/`intent-refresh` as versioned
    Claude Code plugins.

## Sources

Standards & specs: [AGENTS.md](https://github.com/agentsmd/agents.md) ·
[Agentic AI Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) ·
[GitHub Spec Kit](https://github.com/github/spec-kit) ·
[Kiro specs](https://kiro.dev/docs/specs/) ·
[ETH Zurich: Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988) ·
[Codex cloud environments](https://developers.openai.com/codex/cloud/environments)

Products: [Copilot agent risks & mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations) ·
[Copilot guardrails tutorial](https://docs.github.com/en/copilot/tutorials/cloud-agent/build-guardrails) ·
[Devin playbooks](https://docs.devin.ai/product-guides/creating-playbooks) ·
[MultiDevin](https://cognition.ai/blog/devin-can-now-manage-devins) ·
[Jules critics](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/) ·
[Factory autonomy tiers](https://docs.factory.ai/autonomy-and-safety/auto-run) ·
[Anthropic: effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)

Governance: [sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) ·
[OPA policy tool approvals](https://ai-sdk.dev/docs/agents/policy-tool-approvals) ·
[Relari Agent Contracts](https://github.com/relari-ai/agent-contracts) ·
[AgentSpec](https://arxiv.org/abs/2503.18666) ·
[OTel GenAI conventions](https://opentelemetry.io/blog/2025/ai-agent-observability/) ·
[Autonomy ladder](https://asdlc.io/concepts/levels-of-autonomy/) ·
[OWASP Agentic Top 10](https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/)

Community: [Ralph Wiggum](https://ghuntley.com/ralph/) ·
[ralph-playbook](https://github.com/ClaytonFarr/ralph-playbook) ·
[Gas Town](https://github.com/steveyegge/gastown) ·
[beads](https://steveyegge.spicytakes.org/post/2025-11-12-introducing-beads-a-coding-agent-memory-system) ·
[Looper](https://github.com/nexu-io/looper) ·
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) ·
[SpecBench](https://arxiv.org/html/2605.21384v1) ·
[Razbakov's executive team](https://dev.to/razbakov/i-built-an-executive-team-of-6-ai-agents-to-manage-my-15-side-projects-4k0i)
