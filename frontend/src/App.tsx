import { useCallback, useEffect, useRef, useState } from 'react'
import { api, dayOf, money } from './api'
import type { ActivityStep, AppState, Decision, LoopCard, RunSummary } from './api'
import { CATEGORY_ICON, Empty, Pill, Section, statusTone } from './components/bits'
import Autonomy from './components/Autonomy'
import DecisionCard from './components/DecisionCard'
import Impact from './components/Impact'
import Inbox from './components/Inbox'
import LoopDrawer from './components/LoopDrawer'
import Onboarding from './components/Onboarding'
import RunPanel from './components/RunPanel'
import UnderTheHood from './components/UnderTheHood'

type Tab = 'home' | 'impact' | 'autonomy' | 'account' | 'hood'

const TABS: { id: Tab; label: string }[] = [
  { id: 'home', label: 'Today' },
  { id: 'impact', label: 'Impact' },
  { id: 'autonomy', label: 'Autonomy' },
  { id: 'account', label: 'Your account' },
  { id: 'hood', label: 'Under the hood' },
]

function LoopRow({ loop, onOpen }: { loop: LoopCard; onOpen: (id: string) => void }) {
  const tone = statusTone(loop.status)
  return (
    <button className="loop" onClick={() => onOpen(loop.id)}>
      <span className="icon">{CATEGORY_ICON[loop.category] ?? '•'}</span>
      <span>
        <span className="t">{loop.title}</span>
        <span className="sub">
          <Pill tone={tone}>{loop.status_label}</Pill>
          <span>
            {loop.resolution ??
              loop.last_action ??
              (loop.notify_reason ||
              `${loop.evidence_count} ${loop.evidence_count === 1 ? 'fact' : 'facts'} gathered`)}
          </span>
        </span>
      </span>
      <span className="right">
        {loop.value_at_stake > 0 ? (
          <span className="value">{money(loop.value_at_stake, loop.currency)}</span>
        ) : null}
        {loop.days_left !== null && loop.status !== 'COMPLETED' ? (
          <span style={{ color: 'var(--muted)', fontSize: 12 }}>
            {loop.days_left <= 0 ? 'overdue' : `${Math.round(loop.days_left)} days left`}
          </span>
        ) : loop.next_check_at && loop.status === 'WAITING' ? (
          <span style={{ color: 'var(--muted)', fontSize: 12 }}>next check {dayOf(loop.next_check_at)}</span>
        ) : null}
      </span>
    </button>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [state, setState] = useState<AppState | null>(null)
  const [run, setRun] = useState<RunSummary | null>(null)
  const [activity, setActivity] = useState<ActivityStep[]>([])
  const [busy, setBusy] = useState(false)
  const [deciding, setDeciding] = useState<string | null>(null)
  const [openLoop, setOpenLoop] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [onboarded, setOnboarded] = useState(() => {
    try {
      return localStorage.getItem('closer.onboarded') === '1'
    } catch {
      return true
    }
  })
  const poll = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    const [s, r] = await Promise.all([api.state(), api.latestRun()])
    setState(s)
    setRun(r.run)
    setActivity(r.activity)
    return r
  }, [])

  useEffect(() => {
    refresh().catch(() => setToast('Could not reach the CLOSER API.'))
  }, [refresh])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3600)
    return () => clearTimeout(t)
  }, [toast])

  const startRun = async () => {
    setBusy(true)
    setActivity([])
    setTab('home')
    try {
      await api.startRun()
      // Poll the real run record; the panel shows execution as it happens
      // rather than a scripted animation.
      const tick = async () => {
        const r = await api.latestRun()
        setRun(r.run)
        setActivity(r.activity)
        if (r.active.state === 'running') {
          poll.current = window.setTimeout(tick, 250)
        } else {
          const s = await api.state()
          setState(s)
          setBusy(false)
          const needed = s.metrics.needs_you
          setToast(
            needed === 0
              ? 'All handled. Nothing needs you.'
              : `${needed} ${needed === 1 ? 'decision needs' : 'decisions need'} you.`,
          )
        }
      }
      await tick()
    } catch {
      setBusy(false)
      setToast('The run could not be started.')
    }
  }

  useEffect(() => () => { if (poll.current) window.clearTimeout(poll.current) }, [])

  const decide = async (planId: string, verdict: 'approve' | 'reject', choice?: string) => {
    setDeciding(planId)
    try {
      const result = await api.decide(planId, verdict, choice)
      await refresh()
      setToast(
        verdict === 'reject'
          ? 'Closed out. I won’t do it.'
          : result.verification?.status === 'CONFIRMED'
            ? `Done and verified — ${result.verification.detail}`
            : 'Done.',
      )
    } catch (e) {
      setToast(String(e instanceof Error ? e.message : e))
    } finally {
      setDeciding(null)
    }
  }

  const reset = async () => {
    try {
      localStorage.removeItem('closer.onboarded')
    } catch {
      /* private browsing — the walkthrough simply does not reappear */
    }
    setOnboarded(false)
    await api.reset()
    setRun(null)
    setActivity([])
    await refresh()
    setToast('Back to a fresh week.')
  }

  if (!state) {
    return (
      <div className="shell">
        <div className="page" style={{ paddingTop: 120, textAlign: 'center', color: 'var(--muted)' }}>
          Connecting to CLOSER…
        </div>
      </div>
    )
  }

  const m = state.metrics
  const decisionsNow = state.decisions.filter((d) => d.notify_now)
  const decisionsLater = state.decisions.filter((d) => !d.notify_now)
  const active = state.loops.filter(
    (l) => !['COMPLETED', 'CANCELLED', 'EXPIRED'].includes(l.status) && l.status !== 'HUMAN_DECISION',
  )
  const decisionLoops = state.loops.filter((l) => l.status === 'HUMAN_DECISION')
  const done = state.loops.filter((l) => ['COMPLETED', 'CANCELLED'].includes(l.status))

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className={`brand-mark ${busy ? 'spin' : ''}`} />
          <span className="brand-name">CLOSER</span>
          <span className="brand-tag">Your life creates open loops. CLOSER closes them.</span>
        </div>
        <nav className="nav">
          {TABS.map((t) => (
            <button key={t.id} aria-current={tab === t.id} onClick={() => setTab(t.id)}>
              {t.label}
              {t.id === 'home' && m.needs_you > 0 ? (
                <span style={{ color: 'var(--attention)' }}> · {m.needs_you}</span>
              ) : null}
            </button>
          ))}
        </nav>
      </header>

      <div className="demo-strip">
        <strong>Demo data</strong>
        <span className="dot" />
        <span className="say">
          Everything here is synthetic — no real inbox, calendar or account is touched.
        </span>
        <span className="right">
          <span className="mono-chip">{state.model_provider}</span>
          <button className="btn ghost sm" onClick={reset}>
            Reset the week
          </button>
        </span>
      </div>

      <main className="page">
        {tab === 'home' ? (
          <>
            {!onboarded && !state.last_run ? (
              <Onboarding
                settings={state.settings}
                onDone={() => {
                  setOnboarded(true)
                  refresh()
                }}
              />
            ) : null}
            <section className="hero">
              <div>
                <h1 className="hero-line">
                  {m.handled_this_week > 0 ? (
                    <>
                      {m.handled_this_week} {m.handled_this_week === 1 ? 'thing' : 'things'} handled
                      <br />
                      while you were away.
                    </>
                  ) : (
                    <>
                      Your life creates open loops.
                      <br />
                      CLOSER closes them.
                    </>
                  )}
                </h1>
                <p className="hero-sub">
                  {m.handled_this_week > 0
                    ? `${m.waiting_on_others} ${m.waiting_on_others === 1 ? 'loop is' : 'loops are'} waiting on other people. ${
                        m.needs_you === 0
                          ? 'Nothing needs you right now.'
                          : `${m.needs_you} ${m.needs_you === 1 ? 'decision needs' : 'decisions need'} your judgement.`
                      }`
                    : 'An AI agent that quietly handles repetitive admin work in the background and only asks you when your judgment is actually needed.'}
                </p>

                <div className="substats">
                  <div className="substat">
                    <div className="n">{m.time_saved_label}</div>
                    <div className="l">admin time avoided</div>
                  </div>
                  <div className="substat">
                    <div className="n">{money(m.value_touched, m.currency)}</div>
                    <div className="l">recovered or protected</div>
                  </div>
                  <div className="substat attention">
                    <div className="n">{m.needs_you}</div>
                    <div className="l">need you</div>
                  </div>
                </div>

                <div className="cta-row">
                  <button className="btn primary" onClick={startRun} disabled={busy}>
                    {busy ? <span className="spinner" /> : null} {busy ? 'Working…' : 'Let CLOSER work'}
                  </button>
                  <button className="btn ghost" onClick={() => setTab('account')}>
                    See what it started with
                  </button>
                </div>
              </div>

              <RunPanel run={run} activity={activity} busy={busy} />
            </section>

            {decisionsNow.length > 0 ? (
              <Section
                title={`${decisionsNow.length} ${decisionsNow.length === 1 ? 'decision needs' : 'decisions need'} you`}
                hint="Everything else was handled."
              >
                {decisionsNow.map((d: Decision) => (
                  <DecisionCard
                    key={d.plan_id}
                    decision={d}
                    onDecide={decide}
                    onOpenLoop={setOpenLoop}
                    busy={deciding === d.plan_id}
                  />
                ))}
              </Section>
            ) : state.loops.length > 0 && state.last_run ? (
              <Section title="Nothing needs you" hint="That is the point.">
                <div className="notice calm">
                  <span>✓</span>
                  <span>
                    {m.closed} {m.closed === 1 ? 'loop is' : 'loops are'} closed, {m.waiting_on_others} waiting on
                    someone else, and {m.held_until_relevant} decision
                    {m.held_until_relevant === 1 ? '' : 's'} held back until they actually matter.
                  </span>
                </div>
              </Section>
            ) : null}

            {decisionsLater.length > 0 ? (
              <Section
                title="Held back on purpose"
                hint="Ready to go, but there is time — CLOSER will raise them nearer the deadline."
              >
                {decisionsLater.map((d) => (
                  <DecisionCard
                    key={d.plan_id}
                    decision={d}
                    onDecide={decide}
                    onOpenLoop={setOpenLoop}
                    busy={deciding === d.plan_id}
                  />
                ))}
              </Section>
            ) : null}

            {active.length > 0 ? (
              <Section title="In progress" hint="No action needed from you.">
                <div className="loop-grid">
                  {active.map((l) => (
                    <LoopRow key={l.id} loop={l} onOpen={setOpenLoop} />
                  ))}
                </div>
              </Section>
            ) : null}

            {decisionLoops.length > 0 && decisionsNow.length === 0 && decisionsLater.length === 0 ? (
              <Section title="Waiting on your decision">
                <div className="loop-grid">
                  {decisionLoops.map((l) => (
                    <LoopRow key={l.id} loop={l} onOpen={setOpenLoop} />
                  ))}
                </div>
              </Section>
            ) : null}

            {done.length > 0 ? (
              <Section title="Closed" hint="You were not interrupted for any of these.">
                <div className="loop-grid">
                  {done.map((l) => (
                    <LoopRow key={l.id} loop={l} onOpen={setOpenLoop} />
                  ))}
                </div>
              </Section>
            ) : null}

            {state.loops.length === 0 ? (
              <Empty
                title="Nothing here yet"
                body="Press “Let CLOSER work” and it will read everything in the account and decide what to do."
              />
            ) : null}
          </>
        ) : null}

        {tab === 'impact' ? <Impact state={state} /> : null}
        {tab === 'autonomy' ? <Autonomy state={state} onSaved={refresh} /> : null}
        {tab === 'account' ? <Inbox /> : null}
        {tab === 'hood' ? <UnderTheHood state={state} /> : null}
      </main>

      {openLoop ? (
        <LoopDrawer loopId={openLoop} onClose={() => setOpenLoop(null)} onChanged={refresh} />
      ) : null}
      {toast ? <div className="toast">{toast}</div> : null}
    </div>
  )
}
