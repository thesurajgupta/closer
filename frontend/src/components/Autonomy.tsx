import { useState } from 'react'
import type { AppState, Settings } from '../api'
import { api, money } from '../api'
import { Section, Segmented, Switch } from './bits'

const AUTO_ROWS: { key: keyof Settings; name: string; desc: string }[] = [
  { key: 'organize_documents', name: 'Organise and file documents', desc: 'Sort what arrives, match it to what it belongs with.' },
  { key: 'detect_duplicates', name: 'Detect duplicates', desc: 'Spot the same charge or request arriving twice.' },
  { key: 'prepare_drafts', name: 'Prepare drafts and claims', desc: 'Write the message. Nothing is sent by this alone.' },
  { key: 'schedule_follow_ups', name: 'Schedule follow-ups', desc: 'Decide when to check back and do it.' },
  { key: 'monitor_pending', name: 'Monitor pending requests', desc: 'Keep watching things waiting on other people.' },
  { key: 'auto_reschedule_appointments', name: 'Reschedule and confirm appointments', desc: 'Move a clashing appointment to a free slot.' },
]

const ASK_ROWS: { key: keyof Settings; name: string; desc: string; allowAuto?: boolean }[] = [
  { key: 'external_messages', name: 'Send external messages', desc: 'Email or submit a request on your behalf.', allowAuto: true },
  { key: 'financial_actions', name: 'Financial changes', desc: 'Anything that changes what you are charged.', allowAuto: true },
  { key: 'cancellations', name: 'Cancellations', desc: 'End a service or a booking.' },
  { key: 'sensitive_actions', name: 'Share sensitive documents', desc: 'Identity, health or legal documents.' },
  { key: 'payments', name: 'Payments', desc: 'CLOSER never moves money. This cannot be switched on.' },
  { key: 'irreversible_deletion', name: 'Irreversible deletion', desc: 'Permanently remove something. This cannot be switched on.' },
]

const LOCKED = new Set<keyof Settings>(['payments', 'irreversible_deletion'])

export default function Autonomy({ state, onSaved }: { state: AppState; onSaved: () => void }) {
  const [draft, setDraft] = useState<Settings>(state.settings)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const patch = (p: Partial<Settings>) => {
    setDraft((d) => ({ ...d, ...p }))
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.saveSettings(draft)
      setSaved(true)
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Section title="What CLOSER may do on its own" hint="Changes take effect on the next run.">
        <div className="card card-pad">
          {AUTO_ROWS.map((row) => (
            <div className="toggle-row" key={row.key}>
              <div className="tl-txt">
                <div className="n">{row.name}</div>
                <div className="d">{row.desc}</div>
              </div>
              <Switch
                label={row.name}
                on={Boolean(draft[row.key])}
                onChange={(v) => patch({ [row.key]: v } as Partial<Settings>)}
              />
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="What CLOSER must ask you about"
        hint="Two of these are locked open — no setting can turn them on."
      >
        <div className="card card-pad">
          {ASK_ROWS.map((row) => (
            <div className="toggle-row" key={row.key}>
              <div className="tl-txt">
                <div className="n">{row.name}</div>
                <div className="d">{row.desc}</div>
              </div>
              {LOCKED.has(row.key) ? (
                <span className="pill alert">Never</span>
              ) : (
                <Segmented
                  value={String(draft[row.key])}
                  onChange={(v) => patch({ [row.key]: v } as unknown as Partial<Settings>)}
                  options={[
                    ...(row.allowAuto ? [{ value: 'auto', label: 'Do it' }] : []),
                    { value: 'ask', label: 'Ask first' },
                    { value: 'never', label: 'Never' },
                  ]}
                />
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Thresholds">
        <div className="card card-pad">
          <div className="toggle-row">
            <div className="tl-txt">
              <div className="n">Bring financial matters to me above</div>
              <div className="d">
                Below {money(draft.notify_financial_above)}, reversible money matters are handled without
                interrupting you.
              </div>
            </div>
            <input
              type="number"
              value={draft.notify_financial_above}
              min={0}
              step={100}
              onChange={(e) => patch({ notify_financial_above: Number(e.target.value) })}
              style={{
                width: 110, padding: '8px 11px', borderRadius: 9,
                border: '1px solid var(--line-2)', background: 'var(--panel)', color: 'var(--ink)',
              }}
            />
          </div>
          <div className="toggle-row">
            <div className="tl-txt">
              <div className="n">Raise deadlines within</div>
              <div className="d">A decision with more runway than this waits until it is closer.</div>
            </div>
            <input
              type="number"
              value={draft.notify_deadline_within_days}
              min={1}
              max={60}
              onChange={(e) => patch({ notify_deadline_within_days: Number(e.target.value) })}
              style={{
                width: 110, padding: '8px 11px', borderRadius: 9,
                border: '1px solid var(--line-2)', background: 'var(--panel)', color: 'var(--ink)',
              }}
            />
          </div>
        </div>
      </Section>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 20 }}>
        <button className="btn primary" onClick={save} disabled={saving}>
          {saving ? <span className="spinner" /> : null} Save
        </button>
        {saved ? <span style={{ color: 'var(--calm)', fontSize: 13.5 }}>Saved.</span> : null}
      </div>

      <Section title="How this reads to the policy engine">
        <div className="grid-3">
          {(['automatically', 'ask_first', 'never'] as const).map((bucket) => (
            <div className="card card-pad" key={bucket}>
              <p className="subhead">{bucket.replace('_', ' ')}</p>
              <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--ink-2)', fontSize: 13.6 }}>
                {state.policy_summary[bucket].map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 13, marginTop: 14, maxWidth: '62ch' }}>
          These settings can only narrow what CLOSER is allowed to do. The ceilings above them — no payments, no
          irreversible deletion, no action without evidence, nothing above a hard value limit — are compiled into
          the policy engine and cannot be changed from this screen or by anything the model says.
        </p>
      </Section>
    </>
  )
}
