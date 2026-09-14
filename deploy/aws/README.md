# CLOSER on AWS

Every service here earns its place. Nothing was added for the logo.

| Service | Why it is here | What breaks without it |
| --- | --- | --- |
| **Amazon Bedrock** | Runs Claude for the supervisor and specialists. | No model. |
| **Bedrock AgentCore Runtime** | Hosts the Strands agent with per-household session isolation and managed observability. | You host and isolate the runtime yourself. |
| **AgentCore Memory** | Holds user *preferences* only — "don't tell me about routine updates", "always ask before external email". | CLOSER still works, but stops adapting to how a particular person wants to be interrupted. |
| **Amazon EventBridge** | The reason CLOSER is a background agent rather than a button: three schedules (discovery, follow-up, deadline sweep) drive every run. | CLOSER becomes something you have to remember to open. |
| **Amazon SQS** (+ DLQ) | Makes background work durable. At-least-once delivery is what forces the idempotency discipline. | A crash mid-run silently loses work. |
| **Amazon DynamoDB** | Structured truth: loops, plans, approvals, evidence references, idempotency claims. | State would have to live in a model's context, which is exactly the failure mode CLOSER avoids. |
| **Amazon S3** | The user's documents. | Nothing to gather evidence from. |
| **AWS Secrets Manager** | Live connector credentials, resolved per session, never in a prompt or an image. | Credentials end up somewhere they should not. |
| **Amazon CloudWatch** | Tool latency, action failures, DLQ depth, verification outcomes. | Nobody notices when an autonomous agent starts failing. |

## The one property that matters

```
EventBridge → SQS → agent runtime → tools → policy engine → execution → verification → DynamoDB
                                     ▲                ▲
                                  REASON          AUTHORIZE
                              (the model)     (deterministic code)
```

The model chooses tools. It never gets execution authority. That boundary is
`closer/policy/engine.py`, and it is deterministic Python with no model in the
call path.

## Deploy

```bash
sam build --template deploy/aws/template.yaml
sam deploy --guided --template deploy/aws/template.yaml
```

For the agent itself, prefer AgentCore Runtime (`deploy/agentcore/`) and point
the EventBridge rules at it; the Lambda worker in this template is the
alternative when AgentCore is not available in your account.

## DynamoDB single-table design

The local SQLite store maps one-for-one, so the repository layer is the only
thing that changes:

| Item | pk | sk |
| --- | --- | --- |
| Loop | `SESSION#<session>` | `LOOP#<loop_id>` |
| Plan | `SESSION#<session>` | `PLAN#<plan_id>` |
| Approval | `SESSION#<session>` | `APPROVAL#<plan_id>` |
| Run | `SESSION#<session>` | `RUN#<run_id>` |
| Processed-event marker | `SESSION#<session>` | `SEEN#<key>` |
| **Idempotency claim** | `IDEM#<key>` | `CLAIM` |

The idempotency claim is written with `attribute_not_exists(pk)`, which is the
DynamoDB equivalent of the `INSERT` in `closer/store/db.py::claim_once` — a
conditional write that is the crash barrier between "did the side effect" and
"recorded the side effect".

## Custom metrics

The worker emits these to the `CLOSER` namespace:

- `LoopsDiscovered`, `LoopsClosed`, `LoopsWaiting`
- `AutonomousActions`, `HumanDecisions`, `Interruptions`
- `ActionFailures`, `Retries`, `VerificationFailures`
- `InjectionAttemptsBlocked`
- `ToolLatencyMs` (dimension: tool name)

`Interruptions` is the one to watch. If it climbs, CLOSER is turning into a
notification app, which is the failure mode the product exists to avoid.
