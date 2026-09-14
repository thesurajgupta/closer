import type { AppState } from '../api'
import { money } from '../api'
import { Section } from './bits'

export default function Impact({ state }: { state: AppState }) {
  const m = state.metrics
  const tiles = [
    { n: m.time_saved_label, l: 'admin time avoided', d: 'Minutes attributed per loop by category, summed over loops CLOSER actually moved.' },
    { n: money(m.value_touched, m.currency), l: 'value recovered or protected', d: 'Warranty cover, disputed charges and refunds attached to open loops.' },
    { n: m.closed, l: 'loops closed', d: 'Reached COMPLETED after verification against the provider’s own state.' },
    { n: m.waiting_on_others, l: 'waiting on other people', d: 'Tracked and re-checked on a schedule. No action needed from you.' },
    { n: m.human_decisions, l: 'decisions that needed you', d: 'Everything else was handled without an interruption.' },
    { n: m.autonomous_actions, l: 'actions taken alone', d: 'Each one inside the risk classes your settings permit.' },
    { n: m.verified_actions, l: 'verified against the provider', d: 'Confirmed by checking external state, not by assuming success.' },
    { n: m.failed, l: 'failed or escalated', d: 'Surfaced rather than hidden, with a retry scheduled.' },
  ]

  return (
    <>
      <Section title="What this actually saved" hint={m.disclaimer}>
        <div className="grid-3">
          {tiles.map((t) => (
            <div className="card stat-tile" key={t.l}>
              <div className="n">{t.n}</div>
              <div className="l">{t.l}</div>
              <div className="d">{t.d}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="The number that matters" hint="Attention is the scarce resource.">
        <div className="card card-pad">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
            <div style={{ fontFamily: 'var(--serif)', fontSize: 58, lineHeight: 1 }}>
              {Math.round((1 - m.interruption_rate) * 100)}%
            </div>
            <div style={{ color: 'var(--ink-2)', maxWidth: '42ch' }}>
              of the loops CLOSER is handling required nothing from you at all. It interrupted you{' '}
              {m.needs_you} {m.needs_you === 1 ? 'time' : 'times'} out of {m.total_loops}
              {m.held_until_relevant > 0 ? (
                <>
                  , and deliberately held back {m.held_until_relevant} more decision
                  {m.held_until_relevant === 1 ? '' : 's'} that had time to spare
                </>
              ) : null}
              .
            </div>
          </div>
        </div>
      </Section>

      <Section title="Where the work came from">
        <div className="card card-pad">
          <table className="plain">
            <thead>
              <tr>
                <th>Source</th>
                <th>Items in this account</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(state.inbox_counts).map(([k, v]) => (
                <tr key={k}>
                  <td style={{ textTransform: 'capitalize' }}>{k}</td>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  )
}
