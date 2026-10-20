import { create } from 'zustand'
import { api, type Annotation, type AppSettings, type SegmentEdit, type SpeakerChange, type Health, type Job, type LiveView, type Group, type Meeting, type MeetingSummary, type Unit } from './api'

export type Tab = 'live' | 'summary'
export type RecStatus = 'idle' | 'connecting' | 'recording' | 'paused' | 'stopping'
export type Theme = 'light' | 'dark' | 'system'

export interface Toast {
  id: number
  text: string
  tone?: 'info' | 'error' | 'success'
  action?: { label: string; run: () => void }
}

/** 녹음 중 띄우는 알림. 한 번 띄우는 것(paused-speech, silence)은 잠시 뒤 녹음 표시줄로 접히고,
 *  계속 보이는 것(나머지)은 해결될 때까지 남는다. */
export type RecAlert =
  | { kind: 'paused-speech' }
  | { kind: 'silence'; fromT: number; fromWall: number; minutes: number }
  | { kind: 'mic-lost' }
  | { kind: 'mic-silent' }
  | { kind: 'server-lost'; since: number }
  | { kind: 'disk-low'; freeS: number }
  | { kind: 'disk-full' }

const PERSISTENT = new Set<RecAlert['kind']>(['mic-lost', 'mic-silent', 'server-lost', 'disk-low', 'disk-full'])
export const isPersistent = (a: RecAlert | null) => !!a && PERSISTENT.has(a.kind)

/** 녹음 표시줄 아이콘 상태: ok는 숨김, warn은 주황, bad는 빨강 */
export type Signal = 'ok' | 'warn' | 'bad'

export interface RecState {
  status: RecStatus
  meetingId: string | null
  offset: number
  elapsed: number
  level: number
  pausedAt: number | null
  alert: RecAlert | null
  alertCollapsed: boolean
  mic: Signal
  net: Signal
  /** 일시정지 중에 말소리가 들렸음 (표시줄의 점) */
  heard: boolean
}

export const REC_IDLE: RecState = {
  status: 'idle', meetingId: null, offset: 0, elapsed: 0, level: 0, pausedAt: null,
  alert: null, alertCollapsed: false, mic: 'ok', net: 'ok', heard: false,
}

interface State {
  health: Health | null
  settings: AppSettings | null
  meetings: MeetingSummary[]
  /** 왼쪽 목록의 폴더 */
  groups: Group[]
  selectedId: string | null
  meeting: Meeting | null
  live: LiveView | null
  partial: { t0: number; t1: number; text: string } | null
  job: Job | null
  tab: Tab
  showMarks: boolean
  follow: boolean
  shortcutsOpen: boolean
  sidebarOpen: boolean
  theme: Theme
  rec: RecState
  playerTime: number
  playing: boolean
  toasts: Toast[]
  flashUnit: string | null

  loadHealth: () => Promise<void>
  loadSettings: () => Promise<void>
  loadMeetings: () => Promise<void>
  select: (id: string | null) => Promise<void>
  refreshMeeting: () => Promise<void>
  refreshLive: () => Promise<void>
  refreshViews: () => Promise<void>
  addLiveSegment: (u: Unit) => void
  /** 문장을 직접 고친 결과를 반영 (바뀐 문장, 위치가 옮겨진 댓글·강조) */
  applySegmentEdit: (r: SegmentEdit) => void
  /** 실시간 문장의 임시 화자 바꾸기 (서버가 정한 것, 사용자가 고른 것) */
  applySpeakerChanges: (changes: SpeakerChange[]) => void
  /** 서버가 돌려준 항목 하나를 바꿔 끼운다 (전체를 다시 받지 않는다) */
  upsertAnnotation: (a: Annotation) => void
  removeAnnotation: (id: string) => void
  /** 지우기 전 모습을 돌려준다 (되돌리기용) */
  takeAnnotation: (id: string) => AnnSnapshot
  restoreAnnotation: (snap: AnnSnapshot) => void
  /** 지우는 중(되돌리기 대기)이라 목록에서 숨긴 회의 */
  hiddenMeetings: string[]
  /** 방금 올린 회의: 받아 적기 창(모델·화자 고르기)을 바로 연다 */
  askProcess: string | null
  /** 새 회의 준비 창 (제목·참석자·폴더·마이크를 정하고 녹음 시작) */
  newMeetingOpen: boolean
  stampExported: (stamps: Record<string, number | null>) => void
  setPartial: (p: State['partial']) => void
  setTab: (t: Tab) => void
  set: (patch: Partial<State>) => void
  setRec: (patch: Partial<State['rec']>) => void
  toast: (text: string, tone?: Toast['tone'], action?: Toast['action'], ms?: number) => void
  dismissToast: (id: number) => void
  flash: (unitId: string) => void
}

let toastSeq = 1

export interface AnnSnapshot {
  live?: { item: Annotation; index: number }
}

function readTheme(): Theme {
  try {
    return (localStorage.getItem('theme') as Theme) || 'system'
  } catch {
    return 'system'
  }
}

export const useStore = create<State>((set, get) => ({
  health: null,
  settings: null,
  meetings: [],
  groups: [],
  selectedId: null,
  meeting: null,
  live: null,
  partial: null,
  job: null,
  tab: 'live',
  showMarks: true,
  follow: true,
  shortcutsOpen: false,
  sidebarOpen: true,
  theme: readTheme(),
  rec: REC_IDLE,
  playerTime: 0,
  playing: false,
  toasts: [],
  flashUnit: null,

  loadHealth: async () => {
    try {
      set({ health: await api.health() })
    } catch {
      /* 서버 재시작 중 */
    }
  },
  loadSettings: async () => set({ settings: await api.settings() }),
  loadMeetings: async () => {
    try {
      const [meetings, g] = await Promise.all([api.meetings(), api.groups().catch(() => null)])
      set(g ? { meetings, groups: g.groups } : { meetings })
    } catch {
      /* 무시 */
    }
  },
  select: async (id) => {
    if (id === get().selectedId && get().meeting) return
    set({ selectedId: id, meeting: null, live: null, job: null, partial: null, playerTime: 0, playing: false })
    if (!id) return
    try {
      const meeting = await api.meeting(id)
      if (get().selectedId !== id) return
      set({ meeting, job: meeting.job, tab: 'live' })
      await get().refreshViews()
    } catch (e) {
      get().toast(`회의를 불러오지 못했어요: ${(e as Error).message}`, 'error')
      set({ selectedId: null })
    }
  },
  refreshMeeting: async () => {
    const id = get().selectedId
    if (!id) return
    const meeting = await api.meeting(id)
    if (get().selectedId === id) set({ meeting, job: meeting.job })
  },
  refreshLive: async () => {
    const id = get().selectedId
    if (!id) return
    const live = await api.live(id)
    if (get().selectedId === id) set({ live })
  },
  refreshViews: async () => {
    await get().refreshLive()
  },
  addLiveSegment: (u) => {
    const live = get().live
    if (!live) return
    if (live.units.some((x) => x.id === u.id)) return
    set({ live: { ...live, units: [...live.units, u] } })
  },
  applySegmentEdit: ({ unit, annotations, deleted }) => {
    const live = get().live
    if (!live) return
    const by = new Map(annotations.map((a) => [a.id, a]))
    const gone = new Set(deleted)
    set({
      live: {
        units: live.units.map((u) => (u.id === unit.id ? { ...u, text: unit.text, text_orig: unit.text_orig } : u)),
        annotations: live.annotations.filter((a) => !gone.has(a.id)).map((a) => by.get(a.id) ?? a),
      },
    })
  },
  applySpeakerChanges: (changes) => {
    const live = get().live
    if (!live || !changes.length) return
    const by = new Map(changes.map((c) => [c.id, c]))
    let hit = false
    const units = live.units.map((u) => {
      const c = by.get(u.id)
      if (!c) return u
      hit = true
      return { ...u, speaker: c.speaker, speaker_auto: c.speaker_auto, speaker_src: c.speaker_src, speaker_conf: c.speaker_conf }
    })
    if (hit) set({ live: { ...live, units } })
  },
  upsertAnnotation: (a) => {
    const { live, selectedId } = get()
    if (a.meeting_id !== selectedId || !live) return
    const i = live.annotations.findIndex((x) => x.id === a.id)
    const annotations = i < 0 ? [...live.annotations, a] : live.annotations.map((x, k) => (k === i ? a : x))
    set({ live: { ...live, annotations } })
  },
  removeAnnotation: (id) => {
    const { live } = get()
    if (live) set({ live: { ...live, annotations: live.annotations.filter((x) => x.id !== id) } })
  },
  takeAnnotation: (id) => {
    const { live } = get()
    const snap: AnnSnapshot = {}
    const li = live?.annotations.findIndex((x) => x.id === id) ?? -1
    if (live && li >= 0) snap.live = { item: live.annotations[li], index: li }
    get().removeAnnotation(id)
    return snap
  },
  restoreAnnotation: (snap) => {
    const { live } = get()
    const s = snap.live
    if (!live || !s || live.annotations.some((x) => x.id === s.item.id)) return
    const next = live.annotations.slice()
    next.splice(Math.min(s.index, next.length), 0, s.item)
    set({ live: { ...live, annotations: next } })
  },
  hiddenMeetings: [],
  askProcess: null,
  newMeetingOpen: false,
  stampExported: (stamps) => {
    const { live } = get()
    if (live) set({ live: { ...live, annotations: live.annotations.map((x) => (x.id in stamps ? { ...x, exported_at: stamps[x.id] } : x)) } })
  },
  setPartial: (partial) => set({ partial: partial && partial.text ? partial : null }),
  setTab: (tab) => set({ tab }),
  set: (patch) => set(patch),
  setRec: (patch) => set({ rec: { ...get().rec, ...patch } }),
  toast: (text, tone = 'info', action, ms) => {
    const id = toastSeq++
    set({ toasts: [...get().toasts, { id, text, tone, action }] })
    setTimeout(() => get().dismissToast(id), ms ?? (action ? 10000 : tone === 'error' ? 7000 : 3500))
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  flash: (unitId) => {
    set({ flashUnit: unitId })
    setTimeout(() => {
      if (get().flashUnit === unitId) set({ flashUnit: null })
    }, 1600)
  },
}))

// ---- 화면 사이 연결고리 (오디오 재생, 노트 삽입) ----

type PlayerBridge = { seek: (t: number, play?: boolean) => void; time: () => number }
type NotesBridge = { insertQuote: (text: string, t: number) => void; insertTimestamp: (t: number) => void; insertBlocks: (blocks: unknown[]) => void; flush: () => Promise<void> }
/** 긴 기록은 보이는 부분만 그리므로, 특정 문장으로 가려면 이 길을 쓴다 */
type TranscriptBridge = {
  scrollToUnit: (unitId: string, opts?: { align?: 'center' | 'start' | 'end'; smooth?: boolean }) => void
  memoAt: (t: number) => void
}
/** 녹음 중 아래 입력창 (C/M 단축키) */
type MemoBarBridge = { focus: (mode: 'comment' | 'memo') => void }

export const bridges: { player: PlayerBridge | null; notes: NotesBridge | null; transcript: TranscriptBridge | null; memoBar: MemoBarBridge | null } = {
  player: null,
  notes: null,
  transcript: null,
  memoBar: null,
}

/** 지금 시각: 녹음 중이면 녹음 경과 시간, 아니면 재생 위치 */
export function currentClock(): number {
  const s = useStore.getState()
  if (s.rec.status !== 'idle' && s.rec.meetingId === s.selectedId) return s.rec.elapsed
  return bridges.player?.time() ?? s.playerTime
}

export function seekTo(t: number) {
  const s = useStore.getState()
  if (s.rec.status !== 'idle' && s.rec.meetingId === s.selectedId) {
    s.toast('녹음 중에는 재생할 수 없어요. 녹음을 마치면 들을 수 있어요.')
    return
  }
  bridges.player?.seek(t, true)
}
