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
  /** 왼쪽 목록의 폴더 (없으면 폴더 밖) */
  group_id?: string | null
}

/** 왼쪽 목록에서 회의를 정리하는 폴더 (앱 안에서만, 디스크의 회의 파일은 옮기지 않는다) */
export interface Group {
  id: string
  name: string
  parent_id: string | null
  created_at: number
  count: number
}

export interface GroupsOut {
  groups: Group[]
  group?: Group
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
  /** 저장 폴더가 iCloud·OneDrive 안이면 경고 문장 */
  storage_warning?: string | null
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
  /** 맥 앱(실행기)으로 설치했다 */
  app_install?: boolean
  /** 앱으로 설치했을 때 Claude가 MCP 서버를 띄우는 방법 (앱의 파이썬) */
  mcp?: { command: string; args: string[]; env: Record<string, string> } | null
}

export interface AppSettings {
  /** 실시간 받아쓰기 모델 (왼쪽 아래 모델 표시에서 바꾼다) */
  live_model?: string | null
  live_choices?: { id: string; label: string; tag: string; detail: string; memory_gb: number }[]
  break_prompt_minutes: number
}

/** 설정 → 고급의 메모리 보기 (바이트, 못 읽으면 null) */
export interface MemoryInfo {
  system: { total: number | null; available: number | null; swap_used: number | null }
  server: number | null
  jobs: { meeting_id: string; bytes: number | null }[]
  live_model: Health['live']
  speaker_model: { loaded: boolean; error: string | null; failures: number; meetings: number }
  recording: { meeting_id: string; backlog_s: number }[]
}

/** 받아 적기·원본 스크립트를 시작하기 전에 고를 것 (예상 시간은 초) */
export interface ProcessOptions {
  duration: number
  default: string
  num_speakers: number | null
  speakers: { available: boolean; reason: string | null }
  choices: {
    id: string; label: string; tag: string; detail: string; memory_gb: number; download_gb: number; downloaded: boolean
    measured: boolean; eta_s: { with_speakers: number; without_speakers: number }
  }[]
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
  memory: () => req<MemoryInfo>('GET', '/api/system/memory'),
  /** 서버가 스스로 한 일 (화자 모델을 CPU로 바꿈, DB를 사본으로 되돌림 …) */
  notices: (since: number) => req<{ id: number; level: 'warn' | 'error' | 'info'; text: string }[]>('GET', `/api/notices?since=${since}`),
  serverLog: (lines = 300) => req<{ path: string | null; available: boolean; lines: string[] }>('GET', `/api/logs/server?lines=${lines}`),
  settings: () => req<AppSettings>('GET', '/api/settings'),
  saveSettings: (s: Partial<AppSettings>) => req<AppSettings>('PUT', '/api/settings', s),
  meetings: () => req<MeetingSummary[]>('GET', '/api/meetings'),
  createMeeting: (title = '') => req<Meeting>('POST', '/api/meetings', { title }),
  meeting: (id: string) => req<Meeting>('GET', `/api/meetings/${id}`),
  patchMeeting: (id: string, patch: Partial<Pick<Meeting, 'title' | 'num_speakers'>>) =>
    req<Meeting>('PATCH', `/api/meetings/${id}`, patch),
  deleteMeeting: (id: string) => req<{ ok: boolean }>('DELETE', `/api/meetings/${id}`),
  groups: () => req<GroupsOut>('GET', '/api/groups'),
  createGroup: (name: string, parent_id: string | null) => req<GroupsOut>('POST', '/api/groups', { name, parent_id }),
  /** parent_id: ''이면 맨 바깥으로 */
  patchGroup: (id: string, patch: { name?: string; parent_id?: string }) => req<GroupsOut>('PATCH', `/api/groups/${id}`, patch),
  deleteGroup: (id: string) => req<GroupsOut>('DELETE', `/api/groups/${id}`),
  moveMeeting: (id: string, group_id: string | null) => req<GroupsOut>('PUT', `/api/meetings/${id}/group`, { group_id }),
  live: (id: string) => req<LiveView>('GET', `/api/meetings/${id}/live`),
  editSegment: (id: string, segmentId: string, text: string) => req<SegmentEdit>('PATCH', `/api/meetings/${id}/segments/${segmentId}`, { text }),
  job: (id: string) => req<{ job: Job | null; record_rev?: number }>('GET', `/api/meetings/${id}/job`),
  revealMeeting: (id: string) => req<{ path: string }>('POST', `/api/meetings/${id}/reveal`),
  processOptions: (id: string) => req<ProcessOptions>('GET', `/api/meetings/${id}/process/options`),
  /** 사용자가 직접 시작한다 (저절로 시작하지 않는다) */
  process: (id: string, opts?: { final?: string; speakers?: boolean; num_speakers?: number }) =>
    req<{ job: Job }>('POST', `/api/meetings/${id}/process`, opts ?? {}),
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
  /** 내보낸 회의(zip, 또는 HTML + 녹음)를 새 회의로 */
  importMeeting: async (files: File[]): Promise<Meeting> => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    const res = await fetch('/api/import', { method: 'POST', body: fd })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '불러오기 실패')
    return res.json()
  },
  upload: async (file: File, title = ''): Promise<Meeting> => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('title', title)
    const res = await fetch('/api/upload', { method: 'POST', body: fd })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '업로드 실패')
    return res.json()
  },
}
