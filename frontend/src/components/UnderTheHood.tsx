import { useEffect, useState } from 'react'
import type { AppState, RunSummary } from '../api'
import { api, dayOf, timeOf } from '../api'
import { Section } from './bits'

/**
 * The panel for anyone who wants to check that this is real: the agents, the
 * tools they may call, the risk table, the state machine, and a downloadable
 * evidence bundle for any run.
 */
export default function UnderTheHood({ state }: { state: AppState }) {
  const [arch, setArch] = useState<any>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [tools, setTools] = useState<any[]>([])

  useEffect(() => {
    api.architecture().then(setArch)
    api.runs().then((r) => setRuns(r.runs))
    api.latestRun().then((r) => setTools(r.tools as any[]))
  }, [state.last_run?.run_id])

  const latest = state.last_run

  return (
    <>
      <Section title="Is any of this real?" hint="Everything below is read from the running system.">
        <div className="card card-pad">
          <p style={{ marginTop: 0, color: 'var(--ink-2)', maxWidth: '70ch' }}>
            CLOSER runs on the Strands Agents SDK. A supervisor agent owns each loop and hands off to bounded
            specialists, which call the typed tools listed below. The model chooses tools; it has no authority to
            execute anything. Every side effect passes the policy engine, is claimed under an idempotency key, and
            is checked against the provider's own state afterwards.
          </p>
          <p style={{ color: 'var(--muted)', fontSize: 13.4, maxWidth: '70ch' }}>
            Model provider in this session: <strong>{state.model_provider}</strong>. With AWS credentials present
            the same agents run against Claude on Amazon Bedrock; without them a deterministic local planner
            implements the same Strands model interface so the demo is reproducible offline. The agent loop, tool
            calls, state machine, policy engine and verification are identical either way.
          </p>
        </div>
      </Section>

      {latest ? (
        <Section title={`Run ${latest.run_id}`} hint={`${timeOf(latest.started_at)} · ${latest.status.toLowerCase()}`}>
          <div className="grid-3">
            {[
              ['Input events', latest.events_scanned],
              ['Ignored', latest.events_ignored],
              ['Duplicates suppressed', latest.duplicates_suppressed],
              ['Injection attempts blocked', latest.injection_attempts_blocked],
              ['Loops discovered', latest.loops_discovered],
              ['Loops advanced', latest.loops_advanced],
              ['Completed', latest.loops_completed],
              ['Waiting', latest.loops_waiting],
              ['Human decisions', latest.decisions_required],
              ['Autonomous actions', latest.autonomous_actions],
              ['Failures', latest.failures],
              ['Retries', latest.retries],
            ].map(([label, value]) => (
              <div className="card stat-tile" key={String(label)}>
                <div className="n">{String(value)}</div>
                <div className="l">{String(label)}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <a className="btn" href={`/api/runs/${latest.run_id}/report`} download>
              Download the evidence JSON
            </a>
            <span style={{ color: 'var(--muted)', fontSize: 13, alignSelf: 'center' }}>
              {tools.length} tool calls recorded in this run.
            </span>
          </div>
        </Section>
      ) : null}

      {tools.length ? (
        <Section title="Tool calls, in order">
          <div className="card card-pad" style={{ maxHeight: 380, overflow: 'auto' }}>
            <table className="plain">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Agent</th>
                  <th>Tool</th>
                  <th>Loop</th>
                  <th>ms</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {tools.map((t: any, i: number) => (
                  <tr key={t.id}>
                    <td>{i + 1}</td>
                    <td>{t.agent}</td>
                    <td>
                      <span className="mono-chip">{t.tool}</span>
                    </td>
                    <td style={{ color: 'var(--muted)' }}>{t.loop_id ?? '—'}</td>
                    <td>{t.latency_ms}</td>
                    <td style={{ color: t.status === 'success' ? 'var(--calm)' : 'var(--alert)' }}>{t.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {arch ? (
        <>
          <Section title="Agents">
            <div className="grid-2">
              {arch.agents.map((a: any) => (
                <div className="card card-pad" key={a.name}>
                  <div style={{ fontFamily: 'var(--serif)', fontSize: 17 }}>{a.name}</div>
                  <div style={{ color: 'var(--muted)', fontSize: 13.4, marginTop: 4 }}>{a.role}</div>
                </div>
              ))}
            </div>
          </Section>

          <Section title={`Tools the agents may call (${arch.tools.length})`}>
            <div className="card card-pad" style={{ maxHeight: 340, overflow: 'auto' }}>
              <table className="plain">
                <tbody>
                  {arch.tools.map((t: any) => (
                    <tr key={t.name}>
                      <td style={{ width: '30%' }}>
                        <span className="mono-chip">{t.name}</span>
                      </td>
                      <td>{t.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Every action CLOSER can take, and what it costs to take it">
            <div className="card card-pad">
              <table className="plain">
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Risk class</th>
                    <th>Default</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(arch.action_risk as Record<string, string>).map(([action, risk]) => {
                    const denied = arch.denied_in_demo.includes(action)
                    const never = arch.never_autonomous.includes(action)
                    return (
                      <tr key={action}>
                        <td>
                          <span className="mono-chip">{action}</span>
                        </td>
                        <td>{risk.replace(/_/g, ' ').toLowerCase()}</td>
                        <td style={{ color: denied ? 'var(--alert)' : never ? 'var(--attention)' : 'var(--ink-2)' }}>
                          {denied ? 'refused outright' : never ? 'always asks you' : 'follows your settings'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Loop state machine" hint="Only this table can change a loop's state — never the model.">
            <div className="card card-pad" style={{ maxHeight: 320, overflow: 'auto' }}>
              <table className="plain">
                <tbody>
                  {Object.entries(arch.transitions as Record<string, string[]>).map(([from, to]) => (
                    <tr key={from}>
                      <td style={{ width: '28%' }}>
                        <span className="mono-chip">{from}</span>
                      </td>
                      <td style={{ color: 'var(--muted)' }}>{to.length ? to.join(' · ') : 'terminal'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      ) : null}

      {runs.length > 1 ? (
        <Section title="Run history">
          <div className="card card-pad">
            <table className="plain">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Trigger</th>
                  <th>When</th>
                  <th>Discovered</th>
                  <th>Decisions</th>
                  <th>Failures</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id}>
                    <td>
                      <span className="mono-chip">{r.run_id}</span>
                    </td>
                    <td>{r.trigger.toLowerCase().replace('_', ' ')}</td>
                    <td>
                      {dayOf(r.started_at)} {timeOf(r.started_at)}
                    </td>
                    <td>{r.loops_discovered}</td>
                    <td>{r.decisions_required}</td>
                    <td>{r.failures}</td>
                    <td>
                      <a className="srcbtn" href={`/api/runs/${r.run_id}/report`} download>
                        JSON
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </>
  )
}
