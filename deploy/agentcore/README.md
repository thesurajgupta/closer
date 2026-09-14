# Deploying CLOSER on Amazon Bedrock AgentCore

CLOSER was written so that this is a packaging step. `agentcore_app.py` imports
the same orchestrator, agents, tools, policy engine and verifier that the local
demo uses. Nothing in `backend/closer` knows whether it is running on a laptop
or in AgentCore Runtime.

## Prerequisites

- An AWS account with Amazon Bedrock model access enabled for the Claude model
  in `CLOSER_BEDROCK_MODEL_ID`
- `pip install bedrock-agentcore bedrock-agentcore-starter-toolkit boto3`
- Docker with `linux/arm64` build support (AgentCore Runtime is arm64 only)

## Deploy

```bash
agentcore configure --entrypoint deploy/agentcore/agentcore_app.py \
  --requirements-file deploy/agentcore/requirements.txt
agentcore launch
```

## Invoke

```bash
# one full pass
agentcore invoke '{"action": "run", "seed": true}'

# a scheduled follow-up, exactly as EventBridge delivers it
agentcore invoke '{"action": "event", "detail-type": "closer.follow_up"}'

# a human decision, bound to one plan
agentcore invoke '{"action": "decide", "plan_id": "plan_...", "decision": "approve"}'

# the evidence bundle for a run
agentcore invoke '{"action": "report"}'
```

## What AgentCore provides

| Capability | How CLOSER uses it |
| --- | --- |
| **Runtime** | Hosts the Strands supervisor and its specialists. Each invocation is one CLOSER pass. |
| **Session isolation** | One session per household. The runtime sandboxes execution; CLOSER additionally scopes every store key by session so shared storage cannot leak between them. |
| **Observability** | Every tool invocation is already emitted as a structured record (`closer.observability.telemetry`); in AgentCore these surface in CloudWatch alongside the runtime's own traces. |
| **Memory** | Used only for user *preferences* and interaction history — "don't bother me about routine updates", "always ask before external email". Structured truth (loop state, evidence references, approvals, idempotency keys) stays in DynamoDB. Memory personalises behaviour; it never overrides the policy engine. |
| **Identity** | Outbound connector credentials are resolved per session rather than baked into the image. |

## Why memory and truth are kept apart

An agent that keeps loop state in free-form memory will eventually "remember"
something that never happened. CLOSER's rule is that anything a decision depends
on — status, dates, amounts, approvals, evidence references, idempotency keys —
lives in a typed record in DynamoDB, and memory holds only preferences that
shape *how* CLOSER behaves within what policy already allows.

## IAM

The runtime role needs:

- `bedrock:InvokeModel` / `bedrock:InvokeModelWithResponseStream` on the chosen model
- `dynamodb:GetItem|PutItem|Query|UpdateItem` on the CLOSER table only
- `s3:GetObject|PutObject` on the documents bucket prefix only
- `sqs:ReceiveMessage|DeleteMessage|GetQueueAttributes` on the CLOSER queue only
- `secretsmanager:GetSecretValue` on `closer/connectors/*` only
- `logs:CreateLogStream|PutLogEvents`

No wildcard resources. CLOSER's own action allowlist is a second, independent
layer: even a role with broader permissions could not make the agent perform an
action that is not in `closer.policy.engine.ACTION_RISK`.
