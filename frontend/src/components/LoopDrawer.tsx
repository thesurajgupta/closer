import { useEffect, useState } from 'react'
import { api, dayOf, money, timeOf } from '../api'
import type { EvidenceItem, LoopDetail } from '../api'
import { CATEGORY_ICON, Pill, statusTone } from './bits'

function EvidenceSource({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  useEffect(() => {
    api.evidence(id).then(setData).catch(() => setData({ error: true }))
  }, [id])

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" style={{ width: 'min(560px, 100vw)' }}>
        <div className="drawer-head">
          <div>
            <p className="subhead" style={{ margin: 0 }}>Source</p>
            <h2>{data?.source?.title ?? 'Loading…'}</h2>
          </div>
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {data?.evidence ? (
            <div className="card card-pad">
              <p className="subhead">The claim</p>
              <div style={{ fontSize: 15 }}>
                <strong>{data.evidence.label}:</strong> {data.evidence.value}
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 12.5, marginTop: 6 }}>
                {data.evidence.source_type} · {data.evidence.locator || 'whole document'}
                {data.evidence.stale ? ' · superseded, not used' : ''}
              </div>
            </div>
          ) : null}
          {data?.source?.fields ? (
            <div>
              <p className="subhead">Fields in this document</p>
              <dl className="kv">
                {Object.entries(data.source.fields as Record<string, string>).map(([k, v]) => (
                  <div key={k} style={{ display: 'contents' }}>
                    <dt>{k.replace(/_/g, ' ')}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
          {data?.source?.text ? (
            <div>
              <p className="subhead">Document</p>
              <pre className="doc">{data.source.text}</pre>
            </div>
          ) : null}
        </div>
      </aside>
    </>
  )
}

function EvidenceRow({ item, onSource }: { item: EvidenceItem; onSource: (id: string) => void }) {
  return (
    <div className="evidence-row">
      <div>
        <div className="lbl">
          {item.label}
          {item.stale ? <span style={{ color: 'var(--alert)' }}> · superseded, not used</span> : null}
        </div>
        <div className="val">{item.value}</div>
      </div>
      <button className="srcbtn" onClick={() => onSource(item.id)}>
        View source
      </button>
    </div>
  )
}

export default function LoopDrawer({
  loopId,
  onClose,
  onChanged,
}: {
  loopId: string
  onClose: () => void
  onChanged: () => void
}) {
  const [loop, setLoop] = useState<LoopDetail | null>(null)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.loop(loopId).then(setLoop)
  useEffect(() => {
    load()
  }, [loopId])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await load()
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  if (!loop) return null
  const plan = loop.plans[loop.plans.length - 1]

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <div className="loop-icon" style={{ fontSize: 20, marginRight: 4 }}>
            {CATEGORY_ICON[loop.category] ?? '•'}
          </div>
          <div style={{ flex: 1 }}>
            <h2>{loop.title}</h2>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <Pill tone={statusTone(loop.status)}>{loop.status_label}</Pill>
              {loop.external_party ? <span className="pill">{loop.external_party}</span> : null}
              {loop.value_at_stake > 0 ? (
                <span className="pill">{money(loop.value_at_stake, loop.currency)}</span>
              ) : null}
              {loop.days_left !== null ? (
                <span className="pill">{loop.days_left.toFixed(0)} days left</span>
              ) : null}
            </div>
          </div>
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="drawer-body">
          <p style={{ margin: 0, color: 'var(--ink-2)' }}>{loop.description}</p>

          {loop.resolution ? (
            <div className="notice calm">
              <span>✓</span>
              <span>{loop.resolution}</span>
            </div>
          ) : null}

          {loop.status === 'FAILED' ? (
            <div className="card card-pad">
              <div className="notice alert" style={{ marginBottom: 12 }}>
                <span>!</span>
                <span>
                  Couldn't complete this. {plan?.last_error ?? ''}
                  {loop.next_check_at ? ` I'll try again on ${dayOf(loop.next_check_at)}.` : ''}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn sm" disabled={busy} onClick={() => act(() => api.retry(loop.id))}>
                  Retry now
                </button>
                <button className="btn sm ghost" disabled={busy} onClick={() => act(() => api.handleMyself(loop.id))}>
                  I'll handle it myself
                </button>
              </div>
            </div>
          ) : null}

          {loop.status === 'WAITING' ? (
            <div className="card card-pad">
              <div className="notice wait" style={{ marginBottom: 12 }}>
                <span>◷</span>
                <span>
                  Waiting on {loop.external_party ?? 'the other side'}.
                  {loop.next_check_at ? ` Next check ${dayOf(loop.next_check_at)}.` : ''} You don't need to do
                  anything.
                </span>
              </div>
              <button className="btn sm ghost" disabled={busy} onClick={() => act(() => api.simulateReply(loop.id))}>
                Simulate their reply (demo)
              </button>
            </div>
          ) : null}

          <div>
            <p className="subhead">What CLOSER did</p>
            <div className="timeline">
              {loop.timeline.map((e) => (
                <div className={`tl ${e.actor === 'user' ? 'user' : e.actor === 'external' ? 'external' : e.kind === 'verified' ? 'done' : ''}`} key={e.id}>
                  <div className="meta">
                    <span>{timeOf(e.at)}</span>
                    <span>{dayOf(e.at)}</span>
                    <span>{e.agent ?? e.actor}</span>
                  </div>
                  <div className="msg">{e.message}</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <p className="subhead">
              Evidence · {loop.evidence.length} sourced facts
              {loop.missing.length ? ` · ${loop.missing.length} missing` : ''}
            </p>
            <div className="card card-pad">
              {loop.evidence.map((item) => (
                <EvidenceRow key={item.id} item={item} onSource={setSourceId} />
              ))}
              {loop.missing.length ? (
                <div className="notice alert" style={{ marginTop: 12 }}>
                  <span>?</span>
                  <span>Still missing: {loop.missing.join(', ').replace(/_/g, ' ')}</span>
                </div>
              ) : null}
            </div>
          </div>

          {plan ? (
            <div>
              <p className="subhead">Plan, policy and outcome</p>
              <div className="card card-pad">
                <dl className="kv">
                  <dt>Action</dt>
                  <dd>
                    <span className="mono-chip">{plan.action_type}</span> {plan.summary}
                  </dd>
                  <dt>Why</dt>
                  <dd>{plan.rationale}</dd>
                  <dt>Risk class</dt>
                  <dd>{plan.risk_level.replace(/_/g, ' ').toLowerCase()}</dd>
                  <dt>Policy</dt>
                  <dd>
                    {plan.policy.decision.replace(/_/g, ' ').toLowerCase()}{' '}
                    <span className="mono-chip">{plan.policy.rule_id}</span>
                    <div style={{ color: 'var(--muted)', marginTop: 4 }}>{plan.policy.reasons.join(' ')}</div>
                  </dd>
                  {plan.approved_by ? (
                    <>
                      <dt>Approved</dt>
                      <dd>
                        by you · {plan.approved_at ? `${dayOf(plan.approved_at)} ${timeOf(plan.approved_at)}` : ''}
                      </dd>
                    </>
                  ) : null}
                  {plan.execution_result ? (
                    <>
                      <dt>Result</dt>
                      <dd>{String(plan.execution_result.detail ?? '')}</dd>
                    </>
                  ) : null}
                  <dt>Idempotency key</dt>
                  <dd>
                    <span className="mono-chip">{plan.idempotency_key}</span>
                  </dd>
                </dl>

                {plan.verification ? (
                  <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
                    <p className="subhead">Verification</p>
                    <div style={{ marginBottom: 8 }}>{plan.verification.detail}</div>
                    <table className="plain">
                      <thead>
                        <tr>
                          <th>Check</th>
                          <th>Expected</th>
                          <th>Found</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {plan.verification.checks.map((c) => (
                          <tr key={c.check}>
                            <td>{c.check.replace(/_/g, ' ')}</td>
                            <td>{String(c.expected)}</td>
                            <td>{String(c.actual)}</td>
                            <td>{c.pass ? '✓' : '✕'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {loop.draft ? (
            <div>
              <p className="subhead">Prepared message</p>
              <div className="draft">
                <div className="subject">{loop.draft.subject}</div>
                <pre>{loop.draft.body}</pre>
              </div>
            </div>
          ) : null}
        </div>
      </aside>
      {sourceId ? <EvidenceSource id={sourceId} onClose={() => setSourceId(null)} /> : null}
    </>
  )
}
