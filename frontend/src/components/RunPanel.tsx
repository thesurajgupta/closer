import { useEffect, useRef } from 'react'
import type { ActivityStep, RunSummary } from '../api'
import { timeOf } from '../api'

const GLYPH: Record<ActivityStep['state'], string> = {
  running: '●',
  done: '✓',
  skipped: '○',
  failed: '✕',
}

/**
 * "What CLOSER is doing." Every line here is emitted by real execution — a tool
 * that ran, a policy that fired, a state that changed. There is no scripted
 * animation: if the agent does nothing, this panel says nothing.
 */
export default function RunPanel({
  run,
  activity,
  busy,
}: {
  run: RunSummary | null
  activity: ActivityStep[]
  busy: boolean
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const count = activity.length

  useEffect(() => {
    if (busy && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [count, busy])

  if (!run && !busy) {
    return (
      <div className="card runpanel">
        <h3>Nothing has run yet</h3>
        <div className="when">Press “Let CLOSER work” and it will go through everything waiting.</div>
      </div>
    )
  }

  const shown = activity.slice(-40)

  return (
    <div className="card runpanel">
      <h3>{busy ? 'CLOSER is working' : 'What CLOSER just did'}</h3>
      <div className="when">
        {run
          ? `${run.trigger.toLowerCase().replace('_', ' ')} run · ${timeOf(run.started_at)} · ${run.model_provider}`
          : 'starting…'}
      </div>

      <div className="steps" ref={listRef}>
        {shown.map((step, i) => (
          <div className={`step ${step.state} ${i >= shown.length - 3 ? 'step-enter' : ''}`} key={`${step.at}-${i}`}>
            <span className="glyph">{busy && i === shown.length - 1 ? '●' : GLYPH[step.state]}</span>
            <span>
              {step.label}
              {step.agent ? <span className="agent"> · {step.agent}</span> : null}
              {step.detail ? <div className="agent" style={{ textTransform: 'none', letterSpacing: 0 }}>{step.detail}</div> : null}
            </span>
          </div>
        ))}
        {busy && shown.length === 0 ? (
          <div className="step running">
            <span className="glyph">●</span>
            <span>Starting up…</span>
          </div>
        ) : null}
      </div>

      {run && !busy ? (
        <div className="runstats">
          <div>
            <span className="n">{run.events_scanned}</span>
            <span className="l">items read</span>
          </div>
          <div>
            <span className="n">{run.events_ignored}</span>
            <span className="l">needed nothing</span>
          </div>
          <div>
            <span className="n">{run.loops_discovered}</span>
            <span className="l">loops found</span>
          </div>
          <div>
            <span className="n">{run.autonomous_actions}</span>
            <span className="l">handled alone</span>
          </div>
          <div>
            <span className="n">{run.decisions_required}</span>
            <span className="l">decisions raised</span>
          </div>
        </div>
      ) : null}
    </div>
  )
}
