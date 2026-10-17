// 백엔드 API 타입과 호출 함수

export type MeetingStatus = 'empty' | 'recording' | 'recorded' | 'processing' | 'ready' | 'error'

export interface Job {
  id: string
  meeting_id: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  phase: string | null
  progress: number
  message: string | null
  error: string | null
  eta: number | null
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface MeetingSummary {
  id: string
  title: string
  created_at: number
  updated_at: number
  status: MeetingStatus
  source: 'live' | 'upload'
  duration: number
  recording: boolean
  progress?: number | null
}

export interface Meeting extends MeetingSummary {
  original_name: string | null
  speakers: Record<string, string>
  num_speakers: number | null
  final_version: number
  summary_md: string | null
  summary_updated_at: number | null
  job: Job | null
  has_audio: boolean
  has_summary: boolean
}

export interface Unit {
  id: string
  t0: number
  t1: number
  text: string
  speaker?: string | null
  words?: [number, number, number, number][]
  idx?: number
}

export interface Pos {
  unit: string
  offset: number
}

export type AnnKind = 'comment' | 'edit' | 'memo' | 'highlight'

export interface Annotation {
  id: string
  meeting_id: string
  kind: AnnKind
  origin: 'live' | 'final'
  origin_version: number
  anchor: { t0?: number; t1?: number; t?: number; quote?: string; after?: string | null }
  body: string
  replacement: string | null
  color: string | null
  status: 'open' | 'resolved' | 'accepted' | 'dismissed'
  replies: { text: string; at: number }[]
  created_at: number
  updated_at: number
  pos: null | { after: string | null } | { start: Pos; end: Pos; approx: boolean; score: number; text: string }
  match?: 'same' | 'differs'
}

export interface LiveView {
  units: Unit[]
  annotations: Annotation[]
}

export interface FinalView {
  version: number
  created_at: number
  engine: string | null
  diarizer: string | null
  units: Unit[]
  speakers: string[]
  duration: number | null
  annotations: Annotation[]
}

export interface Health {
  ok: boolean
  version: string
  live: { kind: string; model: string; state: 'idle' | 'loading' | 'ready' | 'error'; error: string | null }
  final: { kind: string; model: string }
  diarizer: string
  apple_silicon: boolean
  installed: Record<string, boolean>
  ffmpeg: boolean
  data_dir: string
  project_root: string
  language: string
}

export interface AppSettings {
  vocabulary: string
  auto_process: boolean
}

export interface SearchHit {
  meeting_id: string
  title: string
  kind: 'title' | 'final' | 'live' | 'notes'
  t: number | null
  unit?: string
  snippet: string
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = `${res.status}`
    try {
      const data = await res.json()
      msg = data.detail || msg
    } catch {
      /* 본문 없음 */
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  settings: () => req<AppSettings>('GET', '/api/settings'),
  saveSettings: (s: Partial<AppSettings>) => req<AppSettings>('PUT', '/api/settings', s),
  meetings: () => req<MeetingSummary[]>('GET', '/api/meetings'),
  createMeeting: (title = '') => req<Meeting>('POST', '/api/meetings', { title }),
  meeting: (id: string) => req<Meeting>('GET', `/api/meetings/${id}`),
  patchMeeting: (id: string, patch: Partial<Pick<Meeting, 'title' | 'speakers' | 'num_speakers'>>) =>
    req<Meeting>('PATCH', `/api/meetings/${id}`, patch),
  deleteMeeting: (id: string) => req<{ ok: boolean }>('DELETE', `/api/meetings/${id}`),
  live: (id: string) => req<LiveView>('GET', `/api/meetings/${id}/live`),
  final: (id: string) => req<{ final: FinalView | null; job: Job | null }>('GET', `/api/meetings/${id}/final`),
  job: (id: string) => req<{ job: Job | null }>('GET', `/api/meetings/${id}/job`),
  process: (id: string) => req<{ job: Job }>('POST', `/api/meetings/${id}/process`),
  cancel: (id: string) => req<{ ok: boolean }>('POST', `/api/meetings/${id}/cancel`),
  notes: (id: string) => req<{ blocks: unknown[] | null; markdown: string; updated_at: number | null }>('GET', `/api/meetings/${id}/notes`),
  saveNotes: (id: string, blocks: unknown[]) => req<{ ok: boolean; updated_at: number }>('PUT', `/api/meetings/${id}/notes`, { blocks }),
  addAnnotation: (id: string, body: Record<string, unknown>) => req<Annotation>('POST', `/api/meetings/${id}/annotations`, body),
  patchAnnotation: (annId: string, patch: Partial<Pick<Annotation, 'body' | 'replacement' | 'status' | 'color'>>, view = 'live') =>
    req<Annotation>('PATCH', `/api/annotations/${annId}?view=${view}`, patch),
  reply: (annId: string, text: string, view = 'live') => req<Annotation>('POST', `/api/annotations/${annId}/replies?view=${view}`, { text }),
  deleteAnnotation: (annId: string) => req<{ ok: boolean }>('DELETE', `/api/annotations/${annId}`),
  search: (q: string) => req<SearchHit[]>('GET', `/api/search?q=${encodeURIComponent(q)}`),
  exportMd: async (id: string) => (await fetch(`/api/meetings/${id}/export.md`)).text(),
  upload: async (file: File, title = ''): Promise<Meeting> => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('title', title)
    const res = await fetch('/api/upload', { method: 'POST', body: fd })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '업로드 실패')
    return res.json()
  },
}
