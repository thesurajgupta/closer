export type LoopCard = {
  id: string
  title: string
  category: string
  description: string
  status: string
  status_label: string
  priority: string
  confidence: number
  risk_level: string
  external_party: string | null
  value_at_stake: number
  currency: string
  minutes_saved: number
  deadline: string | null
  days_left: number | null
  next_check_at: string | null
  last_activity: string | null
  notify: boolean
  notify_reason: string
  approval_required: boolean
  resolution: string | null
  evidence_count: number
  missing: string[]
  last_action: string | null
  last_action_status: string | null
  plan_id: string | null
  verification: Verification | null
}

export type Verification = {
  status: string
  detail: string
  checks: { check: string; expected: unknown; actual: unknown; pass: boolean }[]
  verified_at: string | null
}

export type EvidenceItem = {
  id: string
  key: string
  label: string
  value: string
  source_type: string
  source_id: string
  source_title: string
  locator: string
  stale: boolean
  confidence: number
}

export type Decision = {
  plan_id: string
  loop_id: string
  title: string
  what_happened: string
  what_i_found: string[]
  recommendation: string
  what_happens_if_approved: string[]
  why_asking: string
  evidence_ids: string[]
  risk_level: string
  choices: { id: string; label: string }[]
  choice_values?: { id: string; label: string; value: string }[]
  deadline: string | null
  amount: number
  currency: string
  notify_now: boolean
  hold_reason: string
  draft: { subject: string; body: string } | null
}

export type Metrics = {
  handled_this_week: number
  closed: number
  waiting_on_others: number
  needs_you: number
  held_until_relevant: number
  failed: number
  minutes_saved: number
  time_saved_label: string
  value_touched: number
  currency: string
  autonomous_actions: number
  human_decisions: number
  verified_actions: number
  total_loops: number
  interruption_rate: number
  disclaimer: string
}

export type RunSummary = {
  run_id: string
  trigger: string
  started_at: string
  finished_at: string | null
  status: string
  model_provider: string
  events_scanned: number
  events_ignored: number
  loops_discovered: number
  loops_advanced: number
  loops_completed: number
  loops_waiting: number
  decisions_required: number
  autonomous_actions: number
  failures: number
  retries: number
  duplicates_suppressed: number
  injection_attempts_blocked: number
  minutes_saved: number
  notes: string[]
}

export type ActivityStep = {
  at: string
  run_id: string
  label: string
  state: 'running' | 'done' | 'skipped' | 'failed'
  loop_id: string | null
  agent: string | null
  detail: string
}

export type Settings = {
  organize_documents: boolean
  detect_duplicates: boolean
  prepare_drafts: boolean
  schedule_follow_ups: boolean
  monitor_pending: boolean
  auto_reschedule_appointments: boolean
  external_messages: 'auto' | 'ask' | 'never'
  financial_actions: 'auto' | 'ask' | 'never'
  cancellations: 'auto' | 'ask' | 'never'
  sensitive_actions: 'auto' | 'ask' | 'never'
  payments: 'auto' | 'ask' | 'never'
  irreversible_deletion: 'auto' | 'ask' | 'never'
  notify_financial_above: number
  notify_deadline_within_days: number
  quiet_routine_updates: boolean
  focus_areas: string[]
}

export type AppState = {
  mode: string
  demo: boolean
  now: string
  model_provider: string
  metrics: Metrics
  loops: LoopCard[]
  decisions: Decision[]
  notifications: { id: string; title: string; body: string; severity: string }[]
  settings: Settings
  policy_summary: { automatically: string[]; ask_first: string[]; never: string[] }
  last_run: RunSummary | null
  inbox_counts: Record<string, number>
  active_run: { run_id: string | null; state: string; error?: string }
}

export type LoopDetail = LoopCard & {
  source: { type: string; ref: string }
  required_information: string[]
  evidence: EvidenceItem[]
  evidence_complete: boolean
  timeline: {
    id: string; at: string; actor: string; agent: string | null
    kind: string; message: string; data: Record<string, unknown>
  }[]
  plans: {
    plan_id: string; action_type: string; summary: string; rationale: string
    target: string; amount: number; status: string; risk_level: string
    policy: { decision: string; rule_id: string; reasons: string[]; requires_approval: boolean }
    approved_by: string | null; approved_at: string | null; executed_at: string | null
    execution_result: Record<string, unknown> | null
    verification: Verification | null
    attempt_count: number; last_error: string | null; idempotency_key: string
  }[]
  draft: { subject: string; body: string } | null
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  state: () => req<AppState>('/api/state'),
  loop: (id: string) => req<LoopDetail>(`/api/loops/${id}`),
  decisions: () => req<{ decisions: Decision[] }>('/api/decisions'),
  startRun: () => req<{ run_id: string; state: string }>('/api/run', { method: 'POST' }),
  latestRun: () => req<{ run: RunSummary | null; activity: ActivityStep[]; tools: unknown[]; active: AppState['active_run'] }>('/api/runs/latest'),
  run: (id: string) => req<{ run: RunSummary; activity: ActivityStep[]; tools: unknown[]; active: AppState['active_run'] }>(`/api/runs/${id}`),
  runs: () => req<{ runs: RunSummary[] }>('/api/runs'),
  decide: (planId: string, decision: 'approve' | 'reject', choice?: string) =>
    req<{ ok: boolean; status: string; loop_status: string; verification: Verification | null }>(
      `/api/decisions/${planId}`, { method: 'POST', body: JSON.stringify({ decision, choice }) }),
  retry: (loopId: string) => req<{ ok: boolean }>(`/api/loops/${loopId}/retry`, { method: 'POST' }),
  handleMyself: (loopId: string) => req<{ ok: boolean }>(`/api/loops/${loopId}/handle-myself`, { method: 'POST' }),
  simulateReply: (loopId: string) =>
    req<{ ok: boolean }>(`/api/loops/${loopId}/simulate-response`, { method: 'POST', body: JSON.stringify({ outcome: 'resolved' }) }),
  evidence: (id: string) => req<{ evidence: EvidenceItem; source: Record<string, any>; loop_id: string }>(`/api/evidence/${id}`),
  settings: () => req<{ settings: Settings; summary: AppState['policy_summary'] }>('/api/settings'),
  saveSettings: (s: Settings) => req<{ settings: Settings }>('/api/settings', { method: 'PUT', body: JSON.stringify(s) }),
  inbox: () => req<any>('/api/inbox'),
  architecture: () => req<any>('/api/architecture'),
  reset: () => req<{ ok: boolean }>('/api/demo/reset', { method: 'POST' }),
  advanceClock: (days: number) => req<{ now: string }>('/api/demo/advance-clock', { method: 'POST', body: JSON.stringify({ days }) }),
}

export const money = (n: number, ccy = 'INR') =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: ccy, maximumFractionDigits: 0 }).format(n)

export const timeOf = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

export const dayOf = (iso: string) =>
  new Date(iso).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
