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
  /** 녹음을 마쳐 저절로 시작한 작업 (전원이 연결돼 있을 때만 한다) */
  auto?: number
  /** 지금 기다리는 까닭 (녹음 중, 전원 연결 대기). 없으면 null */
  waiting?: string | null
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
  num_speakers: number | null
  final_version: number
  summary_md: string | null
  summary_updated_at: number | null
  job: Job | null
  has_audio: boolean
  has_summary: boolean
  /** 녹음 시간 → 실제 시각 기준점: [녹음 시간(초), 실제 시각(epoch 초)] */
  clock: [number, number][]
  participants: Participant[]
}

export interface Participant {
  id: string
  name: string
  org?: string // 소속
  role?: string // 직무
  note?: string // 메모
}

export interface SpeakerChange {
  id: string
  speaker: string | null
  speaker_auto: string | null
  speaker_src: 'manual' | 'auto' | null
  speaker_conf: number | null
}

export interface StorageInfo {
  data_dir: string
  meetings: number
  audio_bytes: number
  other_bytes: number
  free_bytes: number
  free_s: number
}

export interface Unit {
  id: string
  t0: number
  t1: number
  text: string
  /** 고른 화자 (참여자 id) */
  speaker?: string | null
  /** 목소리 묶음 (A, B …) */
  speaker_auto?: string | null
  /** manual(직접 고름) | auto(목소리로 추정) */
  speaker_src?: 'manual' | 'auto' | null
  speaker_conf?: number | null
  /** 직접 고친 문장: 처음 받아 적은 글자 */
  text_orig?: string
  idx?: number
}

export interface Pos {
  unit: string
  offset: number
}

export type AnnKind = 'comment' | 'memo' | 'highlight' | 'divider'

export interface Annotation {
  id: string
  meeting_id: string
  kind: AnnKind
  anchor: { t0?: number; t1?: number; t?: number; quote?: string; after?: string | null }
  body: string
  color: string | null
  status: 'open' | 'resolved'
  replies: { text: string; at: number }[]
  created_at: number
  updated_at: number
  exported_at: number | null
  meta: { break_start?: number; break_end?: number; auto?: boolean } | null
  pos: AnnPos
}

export type AnnPos = null | { after: string | null } | { start: Pos; end: Pos; text: string }

export interface LiveView {
  units: Unit[]
  annotations: Annotation[]
}

/** 문장을 직접 고친 결과: 바뀐 문장, 위치가 옮겨진 댓글·강조, 지운 강조 */
export interface SegmentEdit {
  unit: Pick<Unit, 'id' | 't0' | 't1' | 'text' | 'text_orig'>
  annotations: Annotation[]
  deleted: string[]
}

export interface Health {
  ok: boolean
  version: string
  live: { kind: string; model: string; state: 'idle' | 'loading' | 'ready' | 'error'; error: string | null; download?: { done: number; total: number } | null }
  precision: string
  power: 'ac' | 'battery' | null
  final: { kind: string; model: string }
  diarizer: string
  apple_silicon: boolean
  installed: Record<string, boolean>
  ffmpeg: boolean
  data_dir: string
  disk_free_bytes: number
  disk_free_s: number
  /** 서버가 녹음 중 잠자기를 막는지 (맥 caffeinate) */
  keep_awake: boolean
  project_root: string
  language: string
}

export interface AppSettings {
  vocabulary: string
  auto_process: boolean
  break_prompt_minutes: number
}

export interface SearchHit {
  meeting_id: string
  title: string
  kind: 'title' | 'live' | 'notes'
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
  patchMeeting: (id: string, patch: Partial<Pick<Meeting, 'title' | 'num_speakers'>>) =>
    req<Meeting>('PATCH', `/api/meetings/${id}`, patch),
  deleteMeeting: (id: string) => req<{ ok: boolean }>('DELETE', `/api/meetings/${id}`),
  live: (id: string) => req<LiveView>('GET', `/api/meetings/${id}/live`),
  editSegment: (id: string, segmentId: string, text: string) => req<SegmentEdit>('PATCH', `/api/meetings/${id}/segments/${segmentId}`, { text }),
  job: (id: string) => req<{ job: Job | null; record_rev?: number }>('GET', `/api/meetings/${id}/job`),
  process: (id: string) => req<{ job: Job }>('POST', `/api/meetings/${id}/process`),
  cancel: (id: string) => req<{ ok: boolean }>('POST', `/api/meetings/${id}/cancel`),
  notes: (id: string) => req<{ blocks: unknown[] | null; markdown: string; updated_at: number | null }>('GET', `/api/meetings/${id}/notes`),
  saveNotes: (id: string, blocks: unknown[]) => req<{ ok: boolean; updated_at: number }>('PUT', `/api/meetings/${id}/notes`, { blocks }),
  addAnnotation: (id: string, body: Record<string, unknown>) => req<Annotation>('POST', `/api/meetings/${id}/annotations`, body),
  patchAnnotation: (annId: string, patch: Partial<Pick<Annotation, 'body' | 'status' | 'color'>>) => req<Annotation>('PATCH', `/api/annotations/${annId}`, patch),
  reply: (annId: string, text: string) => req<Annotation>('POST', `/api/annotations/${annId}/replies`, { text }),
  markExported: (id: string, ids: string[], exported = true) =>
    req<{ updated: number; exported_at: Record<string, number | null> }>('POST', `/api/meetings/${id}/annotations/exported`, { ids, exported }),
  deleteAnnotation: (annId: string) => req<{ ok: boolean }>('DELETE', `/api/annotations/${annId}`),
  storage: () => req<StorageInfo>('GET', '/api/storage'),
  setParticipants: (id: string, participants: (Partial<Participant> & { name: string })[]) =>
    req<{ participants: Participant[] }>('PUT', `/api/meetings/${id}/participants`, { participants }),
  suggestParticipants: () => req<{ name: string; voice: boolean; org?: string; role?: string }[]>('GET', '/api/participants/suggest'),
  assignSpeaker: (id: string, segmentIds: string[], participantId: string | null, scope: 'voice' | 'one') =>
    req<{ changes: SpeakerChange[]; previous_map: Record<string, string> }>('POST', `/api/meetings/${id}/live/speaker`, {
      segment_ids: segmentIds,
      participant_id: participantId,
      scope,
    }),
  restoreSpeakers: (id: string, rows: SpeakerChange[], speakerMap: Record<string, string>) =>
    req<{ changes: SpeakerChange[] }>('POST', `/api/meetings/${id}/live/speaker/restore`, { rows, speaker_map: speakerMap }),
  warmup: () => req<Health['live']>('POST', '/api/warmup'),
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
