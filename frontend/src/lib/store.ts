import { create } from 'zustand'
import { api, type AppSettings, type FinalView, type Health, type Job, type LiveView, type Meeting, type MeetingSummary, type Unit } from './api'

export type Tab = 'live' | 'final' | 'summary'
export type RecStatus = 'idle' | 'connecting' | 'recording' | 'paused' | 'stopping'
export type Theme = 'light' | 'dark' | 'system'

export interface Toast {
  id: number
  text: string
  tone?: 'info' | 'error' | 'success'
}

interface State {
  health: Health | null
  settings: AppSettings | null
  meetings: MeetingSummary[]
  selectedId: string | null
  meeting: Meeting | null
  live: LiveView | null
  partial: { t0: number; t1: number; text: string } | null
  final: FinalView | null
  finalLoaded: boolean
  job: Job | null
  tab: Tab
  showMarks: boolean
  follow: boolean
  sidebarOpen: boolean
  theme: Theme
  rec: { status: RecStatus; meetingId: string | null; offset: number; elapsed: number; level: number }
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
  refreshFinal: () => Promise<void>
  refreshViews: () => Promise<void>
  addLiveSegment: (u: Unit) => void
  setPartial: (p: State['partial']) => void
  setTab: (t: Tab) => void
  set: (patch: Partial<State>) => void
  setRec: (patch: Partial<State['rec']>) => void
  toast: (text: string, tone?: Toast['tone']) => void
  dismissToast: (id: number) => void
  flash: (unitId: string) => void
}

let toastSeq = 1

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
  selectedId: null,
  meeting: null,
  live: null,
  partial: null,
  final: null,
  finalLoaded: false,
  job: null,
  tab: 'live',
  showMarks: true,
  follow: true,
  sidebarOpen: true,
  theme: readTheme(),
  rec: { status: 'idle', meetingId: null, offset: 0, elapsed: 0, level: 0 },
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
      set({ meetings: await api.meetings() })
    } catch {
      /* 무시 */
    }
  },
  select: async (id) => {
    if (id === get().selectedId && get().meeting) return
    set({ selectedId: id, meeting: null, live: null, final: null, finalLoaded: false, job: null, partial: null, playerTime: 0, playing: false })
    if (!id) return
    try {
      const meeting = await api.meeting(id)
      if (get().selectedId !== id) return
      const hasFinal = meeting.final_version > 0
      set({ meeting, job: meeting.job, tab: meeting.source === 'upload' || (hasFinal && meeting.status === 'ready') ? 'final' : 'live' })
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
  refreshFinal: async () => {
    const id = get().selectedId
    if (!id) return
    const { final, job } = await api.final(id)
    if (get().selectedId === id) set({ final, job, finalLoaded: true })
  },
  refreshViews: async () => {
    await Promise.all([get().refreshLive(), get().refreshFinal()])
  },
  addLiveSegment: (u) => {
    const live = get().live
    if (!live) return
    if (live.units.some((x) => x.id === u.id)) return
    set({ live: { ...live, units: [...live.units, u] } })
  },
  setPartial: (partial) => set({ partial: partial && partial.text ? partial : null }),
  setTab: (tab) => set({ tab }),
  set: (patch) => set(patch),
  setRec: (patch) => set({ rec: { ...get().rec, ...patch } }),
  toast: (text, tone = 'info') => {
    const id = toastSeq++
    set({ toasts: [...get().toasts, { id, text, tone }] })
    setTimeout(() => get().dismissToast(id), tone === 'error' ? 7000 : 3500)
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
type NotesBridge = { insertQuote: (text: string, t: number) => void; insertTimestamp: (t: number) => void; insertBlocks: (blocks: unknown[]) => void }

export const bridges: { player: PlayerBridge | null; notes: NotesBridge | null } = { player: null, notes: null }

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
