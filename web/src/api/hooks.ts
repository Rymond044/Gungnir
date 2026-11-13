import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post } from './client'
import type {
  Audit,
  Chart,
  Diagnosis,
  EventRow,
  HostSummary,
  Incident,
  IncidentSummary,
  Knowledge,
  Norm,
  Overview,
  Proposal,
  ProcessRow,
  RecordRow,
  Run,
  Span,
} from './types'

// The agent sends a batch every 10 s: polling faster finds nothing new
// (ADR 0051).
export const LIVE_MS = 10_000

export interface Interval {
  since: string
  until: string
}

export function useHosts() {
  return useQuery({
    queryKey: ['hosts'],
    queryFn: () => get<HostSummary[]>('/hosts'),
    refetchInterval: LIVE_MS,
  })
}

export function useOverview(host: string) {
  return useQuery({
    queryKey: ['overview', host],
    queryFn: () => get<Overview>(`/hosts/${host}/overview`),
    refetchInterval: LIVE_MS,
  })
}

export function useChart(host: string, series: string[], interval: Interval, points = 600, live = true) {
  return useQuery({
    queryKey: ['chart', host, series, interval, points],
    queryFn: () => get<Chart>(`/hosts/${host}/chart`, { series, ...interval, points }),
    refetchInterval: live ? LIVE_MS : false,
    placeholderData: keepPreviousData,
    enabled: series.length > 0,
  })
}

export function useNorm(host: string, signal: string, entity: string, interval: Interval, enabled = true) {
  return useQuery({
    queryKey: ['norm', host, signal, entity, interval],
    queryFn: () => get<Norm>(`/hosts/${host}/norm`, { signal, entity, ...interval }),
    // Baselines move once an hour; the class may change any minute.
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
    enabled: enabled && signal !== '',
    retry: false,
  })
}

export function useContext(host: string, interval: Interval) {
  return useQuery({
    queryKey: ['context', host, interval],
    queryFn: () => get<Span[]>(`/hosts/${host}/context`, { ...interval }),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
}

export interface RecordFilter {
  since?: string
  until?: string
  open?: boolean
  kind?: string
  detector?: string
  signal?: string
  entity?: string
  /** Records of one incident, whenever they were. */
  incident?: number
  limit?: number
}

export function useAnomalies(host: string, filter: RecordFilter, enabled = true) {
  return useQuery({
    queryKey: ['anomalies', host, filter],
    queryFn: () => get<RecordRow[]>(`/hosts/${host}/anomalies`, { ...filter }),
    refetchInterval: LIVE_MS,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useAnomaly(host: string, id: string) {
  return useQuery({
    queryKey: ['anomaly', host, id],
    queryFn: () => get<RecordRow>(`/hosts/${host}/anomalies/${id}`),
    refetchInterval: LIVE_MS,
  })
}

export function useEvents(host: string, interval: Interval, limit = 200) {
  return useQuery({
    queryKey: ['events', host, interval, limit],
    queryFn: () => get<EventRow[]>(`/hosts/${host}/events`, { ...interval, limit }),
    refetchInterval: LIVE_MS,
    placeholderData: keepPreviousData,
  })
}

export function useProcesses(host: string) {
  return useQuery({
    queryKey: ['processes', host],
    queryFn: () => get<ProcessRow[]>(`/hosts/${host}/processes`),
    refetchInterval: LIVE_MS,
  })
}

export function useRuns(host: string) {
  return useQuery({ queryKey: ['runs', host], queryFn: () => get<Run[]>(`/hosts/${host}/runs`) })
}

export type DiagnosisRequest = { since: string; until: string } | { run: string }

export function useDiagnosis(host: string, request: DiagnosisRequest | null, live: boolean) {
  return useQuery({
    queryKey: ['diagnosis', host, request],
    queryFn: () => get<Diagnosis[]>(`/hosts/${host}/diagnosis`, { ...request }),
    enabled: request !== null,
    refetchInterval: live ? LIVE_MS : false,
    placeholderData: keepPreviousData,
  })
}

export function useAudit(host: string, interval: Interval, live: boolean) {
  return useQuery({
    queryKey: ['audit', host, interval],
    queryFn: () => get<Audit>(`/hosts/${host}/audit`, { ...interval }),
    refetchInterval: live ? LIVE_MS : false,
    placeholderData: keepPreviousData,
  })
}

export function useKnowledge(host: string) {
  return useQuery({
    queryKey: ['knowledge', host],
    queryFn: () => get<Knowledge>('/knowledge', { host }),
    staleTime: Infinity,
  })
}

export function useIncidents(host: string, interval: Interval, live: boolean) {
  return useQuery({
    queryKey: ['incidents', host, interval],
    queryFn: () => get<IncidentSummary[]>(`/hosts/${host}/incidents`, { ...interval }),
    refetchInterval: live ? LIVE_MS : false,
    placeholderData: keepPreviousData,
  })
}

export function useIncident(host: string, id: string) {
  return useQuery({
    queryKey: ['incident', host, id],
    queryFn: () => get<Incident>(`/hosts/${host}/incidents/${id}`),
    refetchInterval: LIVE_MS,
  })
}

/** The operator confirms or rejects a proposal (ADR 0060). */
export function useDecision(host: string, incident: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ proposal, decision }: { proposal: number; decision: 'confirm' | 'reject' }) =>
      post<Proposal>(`/hosts/${host}/incidents/${incident}/proposals/${proposal}`, { decision }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ['incident', host] })
      void client.invalidateQueries({ queryKey: ['incidents', host] })
      void client.invalidateQueries({ queryKey: ['overview', host] })
    },
  })
}
