import { useState } from 'react'
import type { Settings } from '../api'
import { api } from '../api'
import { Segmented, Switch } from './bits'

const AREAS = [
  { id: 'bills', label: 'Bills & refunds', icon: '₹' },
  { id: 'warranties', label: 'Warranties & purchases', icon: '🛠' },
  { id: 'appointments', label: 'Appointments', icon: '🗓' },
  { id: 'documents', label: 'Documents', icon: '📄' },
  { id: 'follow_ups', label: 'Follow-ups', icon: '✉' },
]

/**
 * Thirty seconds, two questions, no account. The point is not to collect
 * preferences — it is to make it obvious from the first screen that the person
 * decides what CLOSER is allowed to do.
 */
export default function Onboarding({
  settings,
  onDone,
}: {
  settings: Settings
  onDone: () => void
}) {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<Settings>(settings)
  const [saving, setSaving] = useState(false)

  const toggleArea = (id: string) =>
    setDraft((d) => ({
      ...d,
      focus_areas: d.focus_areas.includes(id)
        ? d.focus_areas.filter((a) => a !== id)
        : [...d.focus_areas, id],
    }))

  const finish = async () => {
    setSaving(true)
    try {
      await api.saveSettings(draft)
      localStorage.setItem('closer.onboarded', '1')
      onDone()
    } finally {
      setSaving(false)
    }
  }

  const skip = () => {
    localStorage.setItem('closer.onboarded', '1')
    onDone()
  }

  return (
    <div className="card card-pad" style={{ marginBottom: 28 }}>
      {step === 0 ? (
        <>
          <p className="subhead">First, one question</p>
          <h2 style={{ fontFamily: 'var(--serif)', fontWeight: 500, fontSize: 24, margin: '0 0 4px' }}>
            What should CLOSER help with?
          </h2>
          <p style={{ color: 'var(--muted)', marginTop: 0, fontSize: 13.5 }}>
            It will leave everything else alone.
          </p>
          <div className="grid-3" style={{ margin: '18px 0 22px' }}>
            {AREAS.map((area) => {
              const on = draft.focus_areas.includes(area.id)
              return (
                <button
                  key={area.id}
                  className="choice"
                  aria-pressed={on}
                  onClick={() => toggleArea(area.id)}
                >
                  <span style={{ fontSize: 16 }}>{area.icon}</span>
                  {area.label}
                </button>
              )
            })}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn primary" onClick={() => setStep(1)}>
              Next
            </button>
            <button className="btn ghost" onClick={skip}>
              Skip — use the defaults
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="subhead">And one more</p>
          <h2 style={{ fontFamily: 'var(--serif)', fontWeight: 500, fontSize: 24, margin: '0 0 4px' }}>
            What may CLOSER do without asking?
          </h2>
          <p style={{ color: 'var(--muted)', marginTop: 0, fontSize: 13.5 }}>
            You can change any of this later, and two things are locked shut for good.
          </p>

          <div style={{ margin: '14px 0 20px' }}>
            <div className="toggle-row">
              <div className="tl-txt">
                <div className="n">Organise, investigate and prepare drafts</div>
                <div className="d">Reading, matching and writing. Nothing leaves your account.</div>
              </div>
              <Switch
                label="Safe organisation"
                on={draft.prepare_drafts && draft.organize_documents}
                onChange={(v) => setDraft((d) => ({ ...d, prepare_drafts: v, organize_documents: v }))}
              />
            </div>
            <div className="toggle-row">
              <div className="tl-txt">
                <div className="n">Schedule and move appointments</div>
                <div className="d">Reversible, and CLOSER verifies the change afterwards.</div>
              </div>
              <Switch
                label="Appointments"
                on={draft.auto_reschedule_appointments}
                onChange={(v) => setDraft((d) => ({ ...d, auto_reschedule_appointments: v }))}
              />
            </div>
            <div className="toggle-row">
              <div className="tl-txt">
                <div className="n">Send messages to companies</div>
                <div className="d">Claims, disputes and follow-ups sent in your name.</div>
              </div>
              <Segmented
                value={draft.external_messages}
                onChange={(v) => setDraft((d) => ({ ...d, external_messages: v as Settings['external_messages'] }))}
                options={[
                  { value: 'auto', label: 'Do it' },
                  { value: 'ask', label: 'Ask first' },
                  { value: 'never', label: 'Never' },
                ]}
              />
            </div>
            <div className="toggle-row">
              <div className="tl-txt">
                <div className="n">Financial changes</div>
                <div className="d">Anything that changes what you are charged.</div>
              </div>
              <Segmented
                value={draft.financial_actions}
                onChange={(v) => setDraft((d) => ({ ...d, financial_actions: v as Settings['financial_actions'] }))}
                options={[
                  { value: 'auto', label: 'Do it' },
                  { value: 'ask', label: 'Ask first' },
                  { value: 'never', label: 'Never' },
                ]}
              />
            </div>
            <div className="toggle-row">
              <div className="tl-txt">
                <div className="n">Payments and permanent deletion</div>
                <div className="d">CLOSER never does these. There is no setting that turns them on.</div>
              </div>
              <span className="pill alert">Never</span>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn primary" onClick={finish} disabled={saving}>
              {saving ? <span className="spinner" /> : null} Start
            </button>
            <button className="btn ghost" onClick={() => setStep(0)}>
              Back
            </button>
          </div>
        </>
      )}
    </div>
  )
}
