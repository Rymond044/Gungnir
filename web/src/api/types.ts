// Shapes of the server's JSON (server/src/gungnir_server/api.py). Times are
// ISO strings in UTC; chart points carry epoch milliseconds.

export interface HostSummary {
  name: string
  created_at: string
  last_seen_at: string | null
  agent_version: string | null
  agent_started_at: string | null
  utc_offset_s: number | null
  dropped_rows: number
}

export interface CollectorState {
  collector: string
  state: 'preferred' | 'fallback' | 'disabled'
  origin: string | null
  reason: string | null
  reported_at: string
}

export interface FactRow {
  name: string
  entity: string
  ts: string
  value: string
}

export interface Latest {
  signal: string
  entity: string
  ts: string
  value: number
}

export interface Overview {
  host: HostSummary
  now: string
  workload_class: string | null
  workload_since: string | null
  power_profile: string | null
  collectors: CollectorState[]
  facts: FactRow[]
  latest: Latest[]
  memory_bytes: number | null
  swap_bytes: number | null
  devices: string[]
  filesystems: string[]
  open: { anomalies: number; impact: number; expected: number }
  /** The open incident, if any (ADR 0060). */
  incident: IncidentSummary | null
}

export interface IncidentSummary {
  id: number
  opened_at: string
  /** The end of the latest harm; the incident closes two quiet minutes after it. */
  last_harm_at: string
  closed_at: string | null
  workload_class: string | null
  records: number
  /** The best harmful hypothesis. */
  cause: string | null
  culprit: string | null
  confidence: number | null
  hypotheses: number
  /** Proposals waiting for a decision. */
  waiting: number
}

export interface IncidentHypothesis {
  id: number
  cause: string
  culprit: string | null
  device: string | null
  filesystem: string | null
  first_seen: string
  last_seen: string
  confidence: number
  peak_confidence: number
  harm: boolean
  expected: boolean
  /** Null once the latest diagnosis no longer gives it. */
  rank: number | null
  tree: Omit<Hypothesis, 'actions'>
}

export type ProposalState = 'proposed' | 'confirmed' | 'rejected' | 'expired'

export interface Proposal {
  id: number
  hypothesis_id: number
  action: string
  target: string
  /** `share` of what the target used of `signal` over the last minute; a weight has only the share. */
  parameter: { signal: string | null; share: number; current: number | null; value: number | null }
  effectiveness: string
  /** The likelihood of its most likely side effect. */
  risk: string | null
  side_effects: { effect: string; likelihood: string }[]
  rollback_after_s: number
  source: string
  rank: number
  state: ProposalState
  created_at: string
  decided_at: string | null
  decided_by: string | null
}

export interface Incident {
  summary: IncidentSummary
  hypotheses: IncidentHypothesis[]
  proposals: Proposal[]
}

/** [ms, mean, min, max] */
export type Point = [number, number, number, number]

export interface ChartSeries {
  signal: string
  entity: string
  points: Point[]
}

export interface Chart {
  since: string
  until: string
  bucket_s: number
  series: ChartSeries[]
}

export interface Span {
  since: string
  until: string
  workload_class: string
  power_profile: string
}

export interface Band {
  workload_class: string
  power_profile: string
  low: number
  high: number
  median: number
  mad: number
  samples: number
}

export interface Limit {
  kind: 'threshold' | 'impact' | 'cold start'
  above: number | null
  below: number | null
  level: string | null
  source: string
}

export interface Norm {
  signal: string
  entity: string
  spans: Span[]
  bands: Band[]
  limits: Limit[]
  /** Hourly minima of a group's memory: what the growth check judges. */
  minima: { hour: string; value: number }[]
}

export interface Basis {
  basis?: string
  median?: number
  mad?: number
  class?: string
  power?: string
  factor?: number
  samples?: number
  share?: number
  capacity?: number
  limit?: number
  of?: string
  above?: number
  below?: number
  rise?: number
  needed?: number
  hourly_minima?: number[]
  /** Growth: each hour must rise by this much, and none bring more than this share. */
  min_step?: number
  max_step_share?: number
  feature?: string
  z?: number
  floor?: string
}

export interface RecordRow {
  id: number
  kind: 'anomaly' | 'impact'
  detector: string
  level: 'noticeable' | 'severe' | null
  signal: string
  entity: string
  started_at: string
  ended_at: string | null
  direction: 'up' | 'down'
  peak_score: number
  value: number | null
  workload_class: string | null
  power_profile: string | null
  baseline: Basis
  episode: number | null
  context: Record<string, number> | null
  expected: boolean | null
  /** The incident it is evidence of. */
  incident_id?: number | null
}

export interface EventRow {
  ts: string
  source: string
  priority: number
  unit: string | null
  message_id: string | null
  message: string
}

export interface ProcessRow {
  ts: string
  pid: number
  ppid: number
  /** The process group; null from agents that do not report it. */
  pgid: number | null
  comm: string
  state: string
  threads: number
  cpu_cores: number
  rss_bytes: number
  cgroup: string | null
  unit: string | null
}

export interface Phase {
  phase: string
  since: string
  until: string
  normal: boolean
  expected: string[]
  scope: string | null
}

export interface Run {
  run: string
  kind: string
  phases: Phase[]
}

// Diagnosis (inference.as_dict). Times inside are epoch seconds.

export type EvidenceItem =
  | {
      type: 'record'
      id: string
      signal: string
      entity: string
      kind: string
      detector: string
      direction: string
      start: number
      end?: number
      level?: string
      score?: number
      workload_class?: string
    }
  | { type: 'value'; signal: string; entity: string; value: number; at: number; record: string }
  | { type: 'event'; id: string; event_type: string; at: number; message: string }
  | { type: 'process'; symptom: string; comm: string; pid: number; state: string; group?: string; at: number }

export type SymptomStatus = 'matched' | 'missing' | 'pending' | 'unobservable' | 'absent'

export interface FoundSymptom {
  kind: 'anomaly' | 'value' | 'event' | 'process'
  names: string[]
  role: string
  strength: Strength
  window: [number, number] | null
  leading: boolean
  impact: boolean
  source: string
  status: SymptomStatus
  evidence: (EvidenceItem & Record<string, unknown>) | null
}

export interface PolicyAction {
  action: string
  when: string | null
  signal: string | null
  share: number
  effectiveness: string
  source: string
}

export interface Hypothesis {
  cause: string
  culprit: string | null
  device: string | null
  filesystem: string | null
  confidence: number
  support: number
  expected: boolean
  /** It hurt the others, not only ran (ADR 0056). */
  harm?: boolean
  leading_at: number
  rule: { source: string; grade: string }
  symptoms: FoundSymptom[]
  actions: PolicyAction[]
}

export interface Advisory {
  cause: string | null
  resource: string | null
  text: string
  source: string
}

export interface Diagnosis {
  label: string
  since: string
  until: string
  hypotheses: Hypothesis[]
  unexplained: (EvidenceItem & Record<string, unknown>)[]
  expected: string[]
  advisories: Advisory[]
  /** Causes whose leading symptom was seen but that give no hypothesis, with the reason. */
  near: (Hypothesis & { reason: string })[]
  /** What the inference engine was given inside the interval. */
  evidence: { records: number; events: number; processes: number }
}

// Knowledge base (catalog.document).

export type Strength = 'characteristic' | 'typical' | 'possible' | 'contradicting'

export interface Described {
  name: string
  label: string | null
  comment: string | null
  source: string | null
  grade: string | null
}

export interface SignalInfo extends Described {
  resource: string
  entity: string
  unit: string
  kind: string
  impact: boolean
  reflects: string[]
}

export interface CauseInfo extends Described {
  resources: string[]
  culprit: string | null
  domain: string | null
}

export interface RuleSymptom {
  kind: 'anomaly' | 'value' | 'event' | 'process'
  names: string[]
  role: string
  strength: Strength
  leading: boolean
  impact: boolean
  window: [number, number] | null
  direction: string | null
  record: string
  level: string | null
  detectors: string[]
  at_most: number | null
  above: number | null
  source: string
  grade: string
}

export interface RuleInfo {
  cause: string
  file: string
  source: string
  grade: string
  requires: Record<string, string[]>
  excludes: Record<string, string[]>
  symptoms: RuleSymptom[]
}

export interface PolicyEntry {
  cause: string
  action: string
  target: string
  when: string | null
  signal: string | null
  share: number
  effectiveness: string
  side_effects: { effect: string; likelihood: string }[]
  requires: Record<string, string[]>
  excludes: Record<string, string[]>
  source: string
}

export interface KnowledgeTest {
  name: string
  domain: string
  expect: string | null
  within: number
  /** False: the cause is named as activity that hurt nobody (ADR 0056). */
  harm: boolean
  not: string[]
  expected: boolean
  facts: Record<string, string>
  culprit_class: string | null
  items: { kind: string; name: string; role: string; at: number; direction: string | null }[]
}

export interface Knowledge {
  resources: Described[]
  entity_types: Described[]
  roles: (Described & { entity_types: string[] })[]
  signals: SignalInfo[]
  events: (Described & {
    resource: string | null
    emitted_by: string
    message_id: string | null
    pattern: string | null
  })[]
  processes: (Described & { resource: string; pattern: string; state: string | null })[]
  facts: (Described & { resource: string; entity: string; values: string[] })[]
  classes: Described[]
  causes: CauseInfo[]
  actions: (Described & { target: string; resource: string; proportional: boolean })[]
  protected: (Described & { units: string[]; commands: string[] })[]
  rules: RuleInfo[]
  policy: PolicyEntry[]
  advisories: Advisory[]
  contraindications: { cause: string; actions: string[] }[]
  expected: { class: string; signals: string[]; direction: string; members: boolean; source: string }[]
  workload_rules: {
    class: string
    priority: number
    command: string[]
    unit: string[]
    group: string[]
    source: string
  }[]
  limits: {
    kind: string
    signal: string
    above: number | null
    below: number | null
    level: string | null
    source: string
  }[]
  tests: KnowledgeTest[]
  grades: string[]
}

/** A command behind a record: the processes of one process group (ADR 0057). */
export interface Command {
  pgid: number | null
  /** The group's leader, such as `make`, which names the command. */
  leader: string | null
  /** The busiest names in the group, each with how many processes had it. */
  names: [string, number][]
  pids: number
  /** Average over the snapshots of the record. */
  cores: number
  rss_bytes: number
  unit: string | null
}

export type Lane = 'cpu' | 'memory' | 'disk' | 'other'

interface StretchBase {
  lane: Lane
  /** Seconds since the epoch; `end` is null while it lasts. */
  start: number
  end: number | null
  harm: boolean
  processes: Command[]
}

export type RecordItem = Extract<EvidenceItem, { type: 'record' }>

/** One cause and culprit over one stretch: a build, a copy, a leak. */
export interface ExplainedStretch extends StretchBase {
  kind: 'explained'
  expected: boolean
  cause: string
  culprit: string | null
  device: string | null
  filesystem: string | null
  confidence: number
  bursts: [number, number | null][]
  records: string[]
  impact: EvidenceItem[]
  /** Host and device records no hypothesis used that ran alongside (ADR 0059). */
  also: RecordItem[]
  hypothesis: Omit<Hypothesis, 'actions'>
  actions: PolicyAction[]
}

/**
 * Records no hypothesis uses, by resource and time: the activity of a program
 * whose own use was unusual, or not explained when only the host's signals
 * were (ADR 0059).
 */
export interface OpenStretch extends StretchBase {
  kind: 'unexplained' | 'expected' | 'activity'
  /** The group whose own use was unusual. */
  actor: string | null
  /** Waiting above its usual level but under the impact levels. */
  waiting: boolean
  records: RecordItem[]
  events: EvidenceItem[]
  workload_class: string | null
  groups: { group: string; processes: Command[] }[]
  advice: { resource: string; text: string; source: string }[]
  near: (Omit<Hypothesis, 'actions'> & { reason: string }) | null
}

export type Stretch = ExplainedStretch | OpenStretch

export interface Audit {
  since: string
  until: string
  /** The first snapshot of the host in the interval. */
  data_since: string | null
  stretches: Stretch[]
}
