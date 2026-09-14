# CLOSER — hackathon submission

Copy-paste material for the submission form.

**Hackathon:** Agents for Humans · **Track:** Everyday Agents · **Framework:** Strands Agents SDK · **License:** MIT

---

## One-liner

Your life creates open loops. CLOSER closes them — an autonomous agent that finishes
the boring admin between something happening and it actually being resolved, and only
asks you when your judgement is genuinely needed.

## The problem

People don't lose time because they lack reminders. They lose it because loops stay
open: a refund nobody chased, a warranty claim buried across three documents, a bill
that quietly changed, a company waiting on one more document. Task managers store
these and reminder apps nag about them. Neither one closes them.

## What it does

CLOSER runs in the background on a schedule and works each loop end to end:
**discover → understand → gather evidence → decide → authorise → act → verify → escalate.**

In the demo week, CLOSER reads 17 items and recognises that 10 need nothing. It
suppresses 5 duplicates and quarantines a prompt-injection attempt. It opens 8 loops
and handles most of them on its own:

- files a withdrawable reversal for a double broadband charge
- books a parcel redelivery
- closes a price rise that was notified in advance
- closes a laptop warranty that expired

Every action is verified against the provider's own state. It interrupts the user
exactly **twice**: once to approve a warranty claim, once to pick an appointment slot.
A third decision is held back on purpose because its deadline is eight days away.

## How it uses Strands

- A **Strands supervisor agent** owns each loop. It delegates to five bounded specialist
  `Agent`s (intake, evidence, resolution, communication, verification), which are exposed
  to it as tools.
- **29 typed `@tool` functions**, each specialist holding only the tools its job needs.
- Tool selection is reactive. A tool returning `warranty_active: false` sends the run down
  a different branch than `true` does.
- Every tool call is instrumented. A demo run makes 146 of them, all downloadable as JSON.
- **Model:** Claude on Amazon Bedrock when AWS credentials are present. With no
  credentials, a local deterministic planner implements the Strands `Model` interface, so
  judges get a reproducible demo with no keys. The agent loop, tools, policy engine, state
  machine and verifier are identical on both paths. It is disclosed in the UI and the README.

## What makes it safe to leave running

- **The model proposes; deterministic code authorises.** No code path goes from model output
  to a side effect without passing through the policy engine.
- **Hard ceilings no setting can raise:** CLOSER never makes payments, never deletes
  irreversibly, never goes over a value cap, never acts without evidence, and never acts on
  untrusted content.
- **Idempotency first:** an idempotency key is claimed *before* any side effect, so a crash
  cannot cause a double-send. A test proves this.
- **Silence Engine:** a deterministic rule set decides whether a result deserves your attention.
- **Evidence-first decision cards:** what happened, what I found (each fact links to its
  source), my recommendation, what happens if you approve, and why I'm asking.

## AWS architecture

Designed and templated, not deployed:

| Service | Role in CLOSER |
| --- | --- |
| Bedrock | model |
| AgentCore Runtime | agent host |
| AgentCore Memory | preferences only |
| EventBridge | schedules |
| SQS + DLQ | durable work queue |
| DynamoDB | structured truth and idempotency claims |
| S3 | documents |
| Secrets Manager | connector credentials |
| CloudWatch | monitoring |

See `deploy/`.

## How to run it (no keys needed)

Live: **https://closer-beige-ten.vercel.app**


```bash
./run.sh                     # Python 3.11+, opens http://127.0.0.1:8000
# or
docker build -t closer . && docker run --rm -p 8000:8000 closer
```

Press **Let CLOSER work**. Then try these:

- **Under the hood:** the proof panel with every tool call and the evidence JSON.
- **Autonomy:** set *External messages* to *Do it* and run again. The warranty claim stops
  asking for approval.

Tests: `make test` (97 passing).

## Honest limitations

- Demo mode acts on a synthetic household through local demo connectors. Every result is
  labelled simulated.
- Live connectors (Gmail, Calendar and so on) are defined as interfaces but not implemented.
- The AWS/AgentCore deployment is complete as code but was not deployed to a live account
  for this submission.
- All data is fictional. No real person, company or account is involved.

## Demo video

Script: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) (5 minutes).
