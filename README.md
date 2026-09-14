<div align="center">

# CLOSER

**Your life creates open loops. CLOSER closes them.**

An autonomous agent, built on the [Strands Agents SDK](https://strandsagents.com),
that finds the unfinished admin in your life, does the parts it can do safely,
and only asks you when your judgement is genuinely needed.

`Agents for Humans Hackathon` · `Everyday Agents` · MIT licensed

</div>

---

## Problem

Modern life doesn't fail because people lack reminders. It fails because nobody
closes the loop.

A refund is owed and nobody follows up. A warranty is still valid but the
receipt is in one folder, the serial number in another, and the terms in a third.
A bill quietly changes. A company asks for one more document. An appointment
clashes with something that matters more. Each of these is an hour of small,
boring coordination — find the evidence, work out the rule, write the message,
wait, check, chase.

Task managers store these. Reminder apps nag you about them. Neither one closes
them. The work still lands on the person.

## Solution

CLOSER is an **open-loop closure agent**. It runs in the background on a
schedule and works the loop end to end:

```
DISCOVER → UNDERSTAND → GATHER → DECIDE → ACT → VERIFY → ESCALATE
```

A reminder app says:

> "Your warranty expires in 7 days."

CLOSER says:

> "Your washing machine is still under warranty for another 10 months. I found
> the invoice, matched it to the warranty certificate, pulled the serial number,
> checked the fault against what's covered, and drafted the service request.
> I need you for one thing: it sends a request to Sterling on your behalf."

Then, if you approve, it submits it, gets a reference back, verifies the claim
really registered, and chases it if they go quiet.

## Why this matters

The scarce resource is not storage or reminders — it is attention.

In the demo run, CLOSER reads 17 items, recognises that 10 of them need nothing,
opens 8 loops, and closes or advances 7 of them without saying a word. It
interrupts the user exactly **twice**. A third decision is deliberately held back
because its deadline is eight days away and raising it now would be noise.

Silence is the feature. Everything else follows from it.

## How CLOSER works

| Stage | What happens | Who decides |
| --- | --- | --- |
| **Discover** | Every inbound item is classified: a real loop, a duplicate, or noise. Most things are noise, and saying so is a useful answer. | agent |
| **Understand** | Documents are searched, facts extracted, superseded versions rejected, gaps named honestly. | agent |
| **Decide** | One action is chosen from the actions available for that loop. "No action, and here is why" is a legitimate answer. | agent |
| **Authorise** | Deterministic policy code decides whether it may run. | **code, never the model** |
| **Act** | The side effect is claimed under an idempotency key, then executed through a connector with bounded retries. | code |
| **Verify** | The provider's own state is checked. Success is confirmed, not assumed. | agent + code |
| **Escalate** | The Silence Engine decides whether any of this is worth your attention. | code |

## Agent architecture

A Strands supervisor owns one loop at a time and delegates to bounded
specialists, each with only the tools its job requires.

```
                          EventBridge  ·  a person  ·  the inbox
                                        │
                                    ┌───▼────┐
                                    │  SQS   │  durable, at-least-once
                                    └───┬────┘
                                        │
                        ┌───────────────▼───────────────┐
                        │   Strands supervisor agent    │   REASON
                        └───┬───────┬───────┬───────┬───┘
                            │       │       │       │
                        Intake  Evidence  Resolution  Communication
                            └───────┴───┬───┴───────┘
                                        │
                                  Proposed action        PLAN
                                        │
                            ┌───────────▼───────────┐
                            │     Policy engine     │   AUTHORIZE
                            │  (no model involved)  │
                            └───────┬───────┬───────┘
                                    │       │
                            run it  │       │  ask the person
                                    └───┬───┘
                                        │
                                   Executor            EXECUTE
                                        │
                              Verification agent       VERIFY
                                        │
                                  Silence engine
                                        │
                              interrupt, or stay quiet
```

Full diagram: [`docs/architecture.svg`](docs/architecture.svg).

| Agent | Responsibility | Cannot |
| --- | --- | --- |
| **Intake** | Classify, deduplicate, open loops | Touch evidence or plans |
| **Evidence** | Establish sourced facts, name what is missing | Execute or authorise anything |
| **Resolution** | Choose one action from what is available | Invent an action type |
| **Communication** | Draft messages, then audit its own draft for unsourced claims | Send anything |
| **Verification** | Check the provider's state, close or reopen the loop | Skip the check |
| **Supervisor** | Delegate, then submit the plan | Bypass the policy engine |

## Strands Agents implementation

The agent loop is real. There is no branch anywhere in this codebase where a
hard-coded response is substituted for agent execution.

- Specialists are `strands.Agent` instances, exposed to the supervisor as tools
  (`delegate_to_evidence_agent` and friends) — the Strands supervisor pattern.
- All 29 tools are `@tool`-decorated functions with typed inputs, typed outputs,
  validation, useful error messages and idempotency where it matters.
- Tool selection is genuinely reactive: `check_warranty_status` returning
  `warranty_active: false` sends the run down a different branch than
  `true` does, and you can watch it happen in the run panel.
- Every tool call is instrumented — agent, tool, latency, status, loop —
  and downloadable as JSON for any run.

**About the model provider.** With AWS credentials present, CLOSER runs on Claude
via Amazon Bedrock (`CLOSER_MODEL_PROVIDER=bedrock`). Without credentials it
falls back to `LocalPlannerModel`, a **real implementation of the Strands `Model`
interface** that receives the conversation, the tool specifications and the
system prompt, and emits Bedrock-shaped streaming events including `toolUse`
blocks. Strands then executes the tools and calls it again, exactly as it would
with a hosted model. What it lacks is neural weights: instead of sampling, it
forward-chains over an explicit rule base (`closer/agents/reasoning.py`) using
the facts earlier tools returned.

This is stated plainly rather than hidden, because it is the honest way to give
a judge a reproducible demo on a laptop with no keys. **The agent loop, tool
selection, tool execution, state machine, policy engine, executor and verifier
are identical on both paths.** Set `CLOSER_MODEL_PROVIDER=bedrock` and not one
line of agent, tool or policy code changes.

## AWS architecture

Every service earns its place; none is here for the logo.

| Service | Why |
| --- | --- |
| **Amazon Bedrock** | Runs Claude for the supervisor and specialists |
| **Bedrock AgentCore Runtime** | Hosts the agent with per-household session isolation |
| **AgentCore Memory** | User *preferences* only — never loop state, never policy |
| **Amazon EventBridge** | The reason CLOSER is a background agent: three schedules drive every run |
| **Amazon SQS** + DLQ | Durable work; at-least-once delivery is what forces the idempotency discipline |
| **Amazon DynamoDB** | Structured truth: loops, plans, approvals, evidence refs, idempotency claims |
| **Amazon S3** | The user's documents |
| **AWS Secrets Manager** | Connector credentials, per session, never in a prompt |
| **Amazon CloudWatch** | Tool latency, failures, verifications — and `Interruptions`, the metric that matters |

Details and the single-table design: [`deploy/aws/README.md`](deploy/aws/README.md).
Infrastructure: [`deploy/aws/template.yaml`](deploy/aws/template.yaml).

## AgentCore deployment

```bash
pip install bedrock-agentcore bedrock-agentcore-starter-toolkit
agentcore configure --entrypoint deploy/agentcore/agentcore_app.py
agentcore launch
agentcore invoke '{"action": "run", "seed": true}'
```

`deploy/agentcore/agentcore_app.py` imports the same orchestrator the local demo
uses. Moving CLOSER to AgentCore is packaging, not a rewrite — see
[`deploy/agentcore/README.md`](deploy/agentcore/README.md).

## Human-in-the-loop design

**The model proposes. Code authorises.** They are separate systems, and the
model has no path into the second one.

Actions are classified by risk, and the default for each class is compiled in:

| Risk class | Examples | Default |
| --- | --- | --- |
| `READ_ONLY` | inspect a document, check status | autonomous |
| `LOW_RISK` | file a document, prepare a draft | autonomous, logged |
| `REVERSIBLE` | schedule a follow-up, move an appointment, file a withdrawable dispute | autonomous within your limits |
| `EXTERNAL_COMMUNICATION` | send an email, submit a claim | **asks you** |
| `FINANCIAL` | cancel a service | **asks you** |
| `SENSITIVE` | share an identity document | **asks you** |

Above those sit ceilings that **no setting and no prompt can raise**: CLOSER
never moves money, never deletes irreversibly, never acts above a hard value
limit, never acts on a claim with no evidence behind it, and never acts on
content that arrived carrying instructions.

When it does need you, it does not ask "shall I continue?". It shows a decision
card carrying the whole investigation:

```
NEEDS YOUR DECISION
Warranty claim for the Sterling WM-8020                    ₹18,500 at stake

WHAT HAPPENED
  Washing machine stopped mid-cycle on Sunday and now shows error E-24.
  Drum does not spin, water drains fine.

WHAT I FOUND
  Product         Sterling WM-8020 Front Load Washing Machine
  Purchase date   2025-07-11
  Invoice number  AA-INV-55120
  Warranty period 24 months
  Warranty status Active
  Serial number   SWM8020-2025-114872
  Where claims go service@sterling-appliances.test

RECOMMENDATION
  Submit a warranty service request for the Sterling WM-8020.

WHAT HAPPENS IF YOU APPROVE
  → A warranty service request goes to Sterling with your invoice and serial.
  → You get a claim reference back, and I'll chase it if they go quiet.

WHY I'M ASKING
  This creates an external request on your behalf.

  [ Approve ]  [ See the evidence ]                       [ Don't do this ]
```

You make the decision. You never repeat the investigation.

Approval is bound to a specific `plan_id`, its current state, its risk level and
its idempotency key. A casual "yes" in some other context is not authorisation;
approving a plan twice is refused; and the policy engine is re-run at execution
time, so a plan approved under one set of settings cannot execute under another.

## Evidence and verification

Every conclusion in the interface has a **View source** link that resolves to
the document, message or calendar entry it came from, with the exact field
highlighted. Superseded documents are shown as *seen and deliberately not used*.

After every side effect, the verification agent goes back to the provider and
checks:

```
claim_registered   expected received     found received          ✓
reference_issued   expected non-empty    found clm_ed788d3cf144  ✓
Confirmed (2/2 checks). Now waiting on Sterling Appliances.
```

Partial success is reported as partial and keeps the loop open. Nothing is
reported as done because a function returned without raising.

## Security

- **External content is data, never instruction.** Emails and documents are
  wrapped in a delimited envelope, scanned for instruction-shaped content, and
  quarantined when hostile. The demo dataset contains a real prompt-injection
  attempt from a lookalike domain — CLOSER blocks it, reports it, and refuses to
  let it justify any action above `LOW_RISK`.
- **Fixed action allowlist.** An action not in `ACTION_RISK` cannot be executed,
  whatever a model emits.
- **Fixed dispatch table.** No model-generated URL, command or destination ever
  reaches a connector.
- **Deterministic state machine.** A model cannot mark a loop complete; only a
  legal transition can.
- **Idempotency before side effects.** The claim is written *before* the action
  is attempted, so a crash between "did it" and "recorded it" cannot double-send.
- **Least privilege.** Each specialist gets only the tools its job needs; IAM
  policies are resource-scoped.
- **No secrets in prompts, code or the client bundle.** Environment variables
  locally; Secrets Manager when deployed.
- **Full audit trail.** Every discovery, evidence collection, decision, policy
  ruling, approval, execution and verification is an audit event on the loop.

## Privacy

Everything in the demo is synthetic. There are no real names, addresses, account
numbers, health records, passwords or API keys anywhere in this repository. All
actions in demo mode run against a local demo environment and are labelled as
such in the interface — **CLOSER never claims a synthetic action happened in the
real world.**

## Architecture diagram

![CLOSER architecture: triggers flow through SQS into the Strands supervisor and specialists (REASON), become a proposal (PLAN), pass a deterministic policy engine (AUTHORIZE), then execute, verify, and reach the Silence Engine, with structured state in DynamoDB, S3, AgentCore Memory, Secrets Manager and CloudWatch.](docs/architecture.svg)

The model reasons and proposes. Deterministic code authorises. Every side effect is verified afterwards.

## Demo

```bash
./run.sh
```

That is the whole thing: it creates a virtualenv, installs dependencies and
starts CLOSER at <http://127.0.0.1:8000>. The interface ships prebuilt, so Node is
not required. **No API keys, no AWS account, no external services.**

Or with Docker:

```bash
docker build -t closer . && docker run --rm -p 8000:8000 closer
```

Press **Let CLOSER work** and watch the run panel. Every line in it is emitted by
real execution — a tool that ran, a policy that fired, a state that changed.

Prefer the terminal?

```bash
make demo        # one full run, printed as it happens
make decisions   # the decision cards
make test        # 97 tests (verified on Python 3.11 and 3.14)
make report      # the run's evidence bundle as JSON
```

### What to look at

1. **Today** — the two decisions that need you, and everything handled without
   asking. Open any loop for its timeline, evidence and verification.
2. **Under the hood** — the agents, all 29 tools, the risk table, the state
   machine, every tool call in order, and a downloadable evidence bundle.
3. **Autonomy** — change `External messages` to *Do it*, run again, and watch the
   warranty claim stop asking for approval. The policy engine is live.
4. **Your account** — the messy starting state, including the quarantined
   injection attempt and the superseded document.

## Local setup

Requirements: Python 3.11+. Node 20.19+ is only needed if you want to rebuild
the interface — a built copy ships in `frontend/dist`.

```bash
make setup      # venv, dependencies, built interface
make serve      # http://127.0.0.1:8000
```

Development with hot reload:

```bash
make dev        # API on :8000, Vite on :5173 with a proxy
```

Background execution:

```bash
make schedule   # the local EventBridge/SQS equivalent, in the foreground
```

## Environment variables

Everything has a working default; the demo needs none of them. See
[`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLOSER_MODE` | `demo` | `demo` or `live` |
| `CLOSER_MODEL_PROVIDER` | `auto` | `auto`, `deterministic`, `bedrock`, `anthropic` |
| `CLOSER_BEDROCK_MODEL_ID` | Claude Sonnet 4.5 | Bedrock model |
| `CLOSER_DEMO_NOW` | `2026-09-10T09:00:00` | Fixed clock, so the demo reproduces on any date |
| `CLOSER_DB` | `./closer.db` | Local structured store |
| `CLOSER_MAX_ACTION_AMOUNT` | `25000` | Hard ceiling nothing can raise |
| `CLOSER_MAX_RETRIES` | `3` | Retry budget per action |
| `CLOSER_SCHEDULER` | `false` | Run schedules inside the API process |
| `CLOSER_DEMO_FAIL_ONCE` | — | Make named connector ops fail once, to see the retry path |

## Testing

```bash
make test
```

97 tests covering the things that decide whether an autonomous agent is safe to
leave running:

| | |
| --- | --- |
| normal event | duplicate event suppressed |
| missing evidence escalates | expired deadline swept |
| ambiguous decision becomes a choice | approval-required action blocked until approved |
| autonomous action inside limits | failing connector surfaced, not hidden |
| retry with backoff | process restart mid-action |
| idempotency across runs | prompt injection quarantined |
| stale evidence rejected | verification failure keeps the loop open |
| successful closure | full end-to-end: event → agent → tools → evidence → decision → policy → action → verification → CLOSED |

Plus the policy engine (17 tests, including "user sets payments to auto" and
"user raises their own threshold above the hard ceiling"), the state machine,
the Silence Engine, the Strands agent layer, and the HTTP API.

```bash
CLOSER_DEMO_FAIL_ONCE=warranty.submit_claim make demo   # watch a retry
```

## Demo scenarios

The synthetic household contains 39 items: 14 messages, 15 documents, 4 calendar
entries, 4 charges and 2 warranty records. Deliberately included: routine cases,
ambiguous cases, an already-closed case, an overdue case, a duplicate, cases with
missing evidence, cases requiring approval, cases that should be ignored, and one
prompt-injection attempt.

| Scenario | What CLOSER does | Outcome |
| --- | --- | --- |
| **Washing machine warranty** | Matches the fault note to the purchase invoice and warranty certificate, confirms 10 months of cover remain, gathers every required field, drafts the claim | **Asks you** — it's an external request |
| **Laptop warranty** | Finds the notebook, checks the terms, sees the cover ended 15 months ago | Closed quietly. No claim filed. |
| **Broadband double charge** | Compares two invoices, finds the plan rental billed twice, reads the provider's own reversal policy, files through their portal, verifies the acknowledgement | Handled alone — ₹999 is below your review threshold |
| **Gym price increase** | Compares charges, finds the 28-day advance notice that explains it | Closed quietly. Nothing to dispute. |
| **Dental appointment** | Finds the clash with a high-importance meeting, offers two free slots on different days | **Asks you** — this is a preference, not a fact |
| **Bank document request** | Works out what was asked for, rejects the superseded statement, picks the current one | Ready, but **held back** — eight days of runway |
| **Insurance renewal** | Extracts both options, raises priority, schedules the follow-up | Watched silently — 14 days out |
| **Missed parcel** | Books a free redelivery slot, verifies with the courier | Handled alone |
| **Resent invoice** | Recognises the same invoice reference | Suppressed |
| **Newsletter, promo, settled refund** | Recognises there is nothing to do | Ignored |
| **"Ignore previous instructions…"** | Detects instruction-shaped content from a lookalike domain | Quarantined, reported, no loop opened |

## Design decisions

**Silence is a feature, not an absence of one.** The Silence Engine is
deterministic code with a real decision procedure — urgency, risk, confidence,
deadline, who is waiting. It will hold back a ready decision for eight days if
raising it now would be noise, and it records *why* it stayed quiet so you can
audit CLOSER's restraint.

**The model never gets execution authority.** Not because the prompt asks
nicely, but because there is no code path from a model output to a side effect
that does not pass through `closer/policy/engine.py`.

**A genuine choice is never made for you.** When two options are both defensible
— Wednesday or Thursday — that is a preference, and CLOSER surfaces it rather
than guessing. `judgement.user_choice` is a first-class policy rule.

**Missing evidence is stated, not papered over.** An agent that quietly fills
gaps is worse than useless. Every requirement is tracked by identity, and a gap
escalates rather than being guessed.

**Verification is not optional.** Calling a tool successfully is not the same as
the thing having happened.

**The demo clock is fixed.** The dataset is authored relative to
`CLOSER_DEMO_NOW`, so a judge running this next year still sees "warranty active,
10 months left" rather than a dataset that rotted.

**No fake loading animations.** Every line in the run panel corresponds to an
execution event. If the agent does nothing, the panel says nothing.

## Limitations

- Demo mode's connectors operate on a local synthetic world. They are honest
  about it — every result carries `simulated: true` and the interface labels it.
  Live connectors are interface implementations away (`closer/connectors/base.py`),
  but they are not written here.
- Without cloud credentials the model is the deterministic local planner
  described above. It is a real Strands model provider and a genuine (if simple)
  reasoner over tool results, but it is not an LLM.
- Document understanding uses the structured fields and text in the synthetic
  corpus. Real PDFs and scans would need Textract or an equivalent behind the
  same `DocumentConnector` interface.
- The AWS template and AgentCore entrypoint are complete and reviewed but have
  not been deployed to a live account as part of this submission.
- Single household, single user. Multi-tenancy is designed for (everything is
  keyed by session) but not exercised.

## Future work

- Live connectors: Gmail, Google Calendar, a document store, merchant APIs
- Textract-backed document understanding for real receipts and letters
- Learned interruption thresholds — CLOSER should notice which decisions you
  always approve and stop asking
- Negotiation loops that span multiple exchanges with a provider
- Shared household loops, with the right person asked for each decision
- An offline evaluation harness scoring runs on interruptions-per-loop-closed

## Repository layout

```
backend/closer/
  agents/         Strands agents, prompts, the local model provider, reasoning rules
  tools/          29 typed tools: intake, evidence, resolution, communication, planning, verification
  policy/         policy engine, silence engine, external-content sanitisation
  execution/      the executor: idempotency, retries, dispatch
  state/          the loop state machine
  connectors/     adapter interfaces + demo implementations
  models/         Pydantic contracts and enums
  store/          SQLite store and repositories (DynamoDB-shaped)
  background/     scheduler, worker, SQS poller, Lambda handler
  demo/           the synthetic household and seeder
  api/            FastAPI
  orchestrator.py the run engine
backend/tests/    97 tests
frontend/         React + TypeScript interface
deploy/agentcore/ AgentCore Runtime entrypoint, Dockerfile
deploy/aws/       EventBridge, SQS, DynamoDB, S3, CloudWatch
docs/             architecture diagram, demo script
```

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

**Most assistants wait to be asked. CLOSER works before you remember to ask.**

</div>
