import { useEffect, useState } from 'react'
import { api, dayOf, money } from '../api'
import { Section } from './bits'

/** The messy input state: what a real week actually looks like before anyone
 *  has touched it. */
export default function Inbox() {
  const [data, setData] = useState<any>(null)
  useEffect(() => {
    api.inbox().then(setData)
  }, [])
  if (!data) return null

  return (
    <>
      <Section
        title="Everything sitting in this account"
        hint="Synthetic data. No real person, company or account is involved."
      >
        <div className="card card-pad">
          <p className="subhead">Messages ({data.messages.length})</p>
          <table className="plain">
            <tbody>
              {data.messages.map((m: any) => (
                <tr key={m.id}>
                  <td style={{ width: '30%' }}>{m.sender}</td>
                  <td>
                    {m.subject}
                    {m.quarantined ? (
                      <span className="pill alert" style={{ marginLeft: 8 }}>
                        quarantined
                      </span>
                    ) : null}
                  </td>
                  <td style={{ width: 90, color: 'var(--muted)' }}>{dayOf(m.received_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="grid-2" style={{ marginTop: 14 }}>
        <div className="card card-pad">
          <p className="subhead">Documents ({data.documents.length})</p>
          <table className="plain">
            <tbody>
              {data.documents.map((d: any) => (
                <tr key={d.id}>
                  <td>
                    {d.title}
                    {d.stale ? (
                      <span className="pill" style={{ marginLeft: 8 }}>
                        superseded
                      </span>
                    ) : null}
                  </td>
                  <td style={{ width: 90, color: 'var(--muted)' }}>{d.doc_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <p className="subhead">Calendar ({data.calendar.length})</p>
            <table className="plain">
              <tbody>
                {data.calendar.map((e: any) => (
                  <tr key={e.id}>
                    <td>{e.title}</td>
                    <td style={{ width: 130, color: 'var(--muted)' }}>
                      {new Date(e.starts_at).toLocaleString([], {
                        weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card card-pad">
            <p className="subhead">Charges ({data.billing.length})</p>
            <table className="plain">
              <tbody>
                {data.billing.map((b: any) => (
                  <tr key={b.id}>
                    <td>
                      {b.provider} · {b.period}
                    </td>
                    <td style={{ width: 120, textAlign: 'right' }}>
                      {money(b.amount)}
                      {b.expected_amount && Math.abs(b.expected_amount - b.amount) > 1 ? (
                        <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                          expected {money(b.expected_amount)}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {data.outbox?.length ? (
        <Section title="What CLOSER produced">
          <div className="card card-pad">
            {data.outbox.map((o: any) => (
              <div className="draft" key={o.id} style={{ marginBottom: 10 }}>
                <div className="subject">
                  {o.subject}{' '}
                  <span className="pill" style={{ marginLeft: 6 }}>
                    {o.status ?? 'sent in demo environment'}
                  </span>
                </div>
                <pre>{o.body}</pre>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </>
  )
}
