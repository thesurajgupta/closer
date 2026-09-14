# CLOSER — five minute demo

One story, told once. Total: 5:00.

Before you record:

```bash
./run.sh                      # http://127.0.0.1:8000
```

Click **Reset the week** so the run happens live on camera. Have the **Your
account** tab open in a second window.

---

## 0:00 – 0:25 — the problem

*On screen: the **Your account** tab. Fourteen messages, fifteen documents, four
appointments, four charges.*

> "This is a normal week. An invoice that's higher than usual. A company asking
> for a document. An appointment that clashes with something important. A washing
> machine that broke.
>
> None of this is hard. All of it takes an hour of finding receipts, checking
> terms, writing messages, and remembering to chase.
>
> Modern life doesn't fail because we lack reminders. It fails because nobody
> closes the loop."

## 0:25 – 0:50 — introduce CLOSER

*Switch to the home screen.*

> "CLOSER is an autonomous agent built on the Strands Agents SDK. It runs in the
> background, finds the unfinished work, handles the parts it can handle safely,
> and only asks me when my judgement is actually needed.
>
> Let's watch it work."

## 0:50 – 2:30 — the live run

*Click **Let CLOSER work**. Let the panel run. Don't talk over all of it — let a
few lines land.*

> "That's the real agent loop. Each line is a tool that ran or a policy that
> fired — nothing here is an animation.
>
> It read the invoice. It compared it against last month and against the plan
> price. It found the plan rental billed twice. Then it read the provider's own
> terms — and their terms say a duplicate line item reported through the portal
> is reversed in seven working days.
>
> So it filed it. ₹999, below my review threshold, reversible, and I was not
> interrupted."

*Point at the run stats.*

> "Seventeen items read. Eleven needed nothing at all. Five duplicates
> suppressed. One message tried to give the agent instructions — we'll come back
> to that.
>
> Eight loops found. Five acted on without asking me, four already closed.
>
> And two decisions that need me."

## 2:30 – 3:40 — one loop, all the way down

*Scroll to the warranty decision card.*

> "Here's the one that matters. My washing machine broke. CLOSER matched my note
> about the fault to the purchase invoice, then to the warranty certificate,
> pulled out the serial number, worked out there are ten months of cover left,
> and checked that the fault is the kind that's covered.
>
> I don't have to repeat any of that. I just have to decide."

*Click **See the evidence**. Open the drawer. Click **View source** on
"Purchase date".*

> "Every fact traces back to the document it came from. This is the actual
> invoice.
>
> And here's the plan, the risk class, and the policy rule. External
> communication. The model wanted to send this — but the model doesn't get to
> decide that. Deterministic code does."

*Close the drawer. Click **Approve**.*

> "Submitted. Reference back. And then — this is the part that matters — it went
> back to the provider and checked the claim actually registered. Two checks,
> both passed. It doesn't report success because a function returned."

## 3:40 – 4:20 — silence

*Scroll to the closed loops.*

> "Now the part I like most.
>
> The gym charged more this month. CLOSER investigated, found the price-change
> notice they sent 28 days ago, and closed it. No dispute, no notification.
> Knowing when *not* to act is the whole job.
>
> The laptop warranty — expired fifteen months ago. Closed quietly. No pointless
> claim.
>
> And here" — *scroll to **Held back on purpose*** — "is a decision CLOSER is
> ready to make. My bank wants a document. It found the current one and rejected
> the out-of-date one. But the deadline is eight days away, so it's holding it
> until it matters.
>
> Three decisions exist. It interrupted me twice."

*Open **Your account**, point at the quarantined message.*

> "And this one. An email pretending to be my broadband provider, telling the
> agent to ignore its instructions and transfer fifty thousand rupees. External
> content is data, never instruction. It was quarantined, reported, and it can't
> justify any action."

## 4:20 – 4:45 — architecture

*Open **Under the hood**.*

> "Strands supervisor, five bounded specialists, twenty-nine typed tools — every
> call recorded, downloadable as JSON.
>
> On AWS: Bedrock runs Claude, AgentCore hosts the agent, EventBridge and SQS
> make it a background worker rather than a button, DynamoDB holds structured
> truth, and every side effect is claimed under an idempotency key first — so a
> crash can't double-send.
>
> The model reasons. It never gets execution authority. Those are two different
> systems."

## 4:45 – 5:00 — close

*Back to the home screen.*

> "Seventeen items. Two interruptions.
>
> Most assistants wait to be asked.
>
> CLOSER works before you remember to ask."

*Hold on:*

```
CLOSER
Your life creates open loops. CLOSER closes them.
```

---

## If you have thirty seconds spare

- **Autonomy → External messages → Do it → run again.** The warranty claim stops
  asking. The policy engine is live, not decoration.
- **`CLOSER_DEMO_FAIL_ONCE=warranty.submit_claim make demo`.** Watch the retry.
- **Open a waiting loop → Simulate their reply.** Watch verification close it.
