import { useState } from 'react'
import type { Decision } from '../api'
import { money } from '../api'

/**
 * The decision card carries the whole investigation, so the only thing left for
 * the person to do is decide. It never asks "shall I continue?".
 */
export default function DecisionCard({
  decision,
  onDecide,
  onOpenLoop,
  busy,
}: {
  decision: Decision
  onDecide: (planId: string, verdict: 'approve' | 'reject', choice?: string) => void
  onOpenLoop: (loopId: string) => void
  busy: boolean
}) {
  const options = decision.choice_values ?? []
  const [picked, setPicked] = useState<string>(options[0]?.value ?? '')
  const held = !decision.notify_now

  return (
    <article className={`decision ${held ? 'held' : ''}`}>
      <div className="flag">
        {held ? <>Ready when you are</> : <>Needs your decision</>}
        {decision.deadline ? (
          <span style={{ marginLeft: 'auto', textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>
            by {new Date(decision.deadline).toLocaleDateString([], { day: 'numeric', month: 'short' })}
          </span>
        ) : null}
      </div>

      <h3>{decision.title}</h3>
      {decision.amount > 0 ? (
        <div className="amount">{money(decision.amount, decision.currency)} at stake</div>
      ) : null}

      <div className="decision-body">
        <div className="decision-block">
          <div className="k">What happened</div>
          <p>{decision.what_happened}</p>
        </div>

        <div className="decision-block">
          <div className="k">What I found</div>
          <ul className="findings">
            {decision.what_i_found.map((line) => {
              const [k, ...rest] = line.split(': ')
              const value = rest.join(': ')
              const stale = value.includes('(superseded')
              return (
                <li key={line}>
                  <span>
                    <span className="fk">{value ? `${k}: ` : ''}</span>
                    {value ? value.replace('  (superseded — not used)', '') : k}
                  </span>
                  {stale ? <span className="stale">superseded — not used</span> : <span />}
                </li>
              )
            })}
          </ul>
        </div>

        <div className="decision-block">
          <div className="k">Recommendation</div>
          <p style={{ fontSize: 15.5 }}>{decision.recommendation}</p>
        </div>

        {options.length > 0 ? (
          <div className="decision-block">
            <div className="k">Your options</div>
            <div className="choices">
              {options.map((o) => (
                <button
                  key={o.id}
                  className="choice"
                  aria-pressed={picked === o.value}
                  onClick={() => setPicked(o.value)}
                >
                  <span className="radio" />
                  {o.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <div className="decision-block">
          <div className="k">What happens if you approve</div>
          <ul className="consequences">
            {decision.what_happens_if_approved.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>

        {decision.draft ? (
          <details className="decision-block">
            <summary className="srcbtn" style={{ cursor: 'pointer' }}>
              Read the exact message I'd send
            </summary>
            <div className="draft" style={{ marginTop: 10 }}>
              <div className="subject">{decision.draft.subject}</div>
              <pre>{decision.draft.body}</pre>
            </div>
          </details>
        ) : null}

        <div className="why">
          <strong>Why I'm asking: </strong>
          {decision.why_asking}
          {held && decision.hold_reason ? <> — {decision.hold_reason}</> : null}
        </div>
      </div>

      <div className="decision-actions">
        <button
          className="btn primary"
          disabled={busy || (options.length > 0 && !picked)}
          onClick={() => onDecide(decision.plan_id, 'approve', picked || undefined)}
        >
          {busy ? <span className="spinner" /> : null} Approve
        </button>
        <button className="btn" disabled={busy} onClick={() => onOpenLoop(decision.loop_id)}>
          See the evidence
        </button>
        <button
          className="btn danger"
          disabled={busy}
          onClick={() => onDecide(decision.plan_id, 'reject')}
          style={{ marginLeft: 'auto' }}
        >
          Don't do this
        </button>
      </div>
    </article>
  )
}
