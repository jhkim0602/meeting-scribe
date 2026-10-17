import { FileAudio, FileUp, Loader2, Mic, Moon, PanelLeftClose, Plus, Search, Settings, Sun, Monitor, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createAndRecord } from '../hooks/recorder'
import { api, type MeetingSummary, type SearchHit } from '../lib/api'
import { useStore, type Theme } from '../lib/store'
import { dateGroup, fmtDuration, fmtTime } from '../lib/time'

export async function uploadFiles(files: FileList | File[]) {
  const st = useStore.getState()
  for (const f of Array.from(files)) {
    st.toast(`'${f.name}' 올리는 중…`)
    try {
      const m = await api.upload(f)
      await st.loadMeetings()
      location.hash = `#/m/${m.id}`
      st.toast('올렸어요. 원본을 만들기 시작했어요.', 'success')
    } catch (e) {
      st.toast(`업로드 실패: ${(e as Error).message}`, 'error')
    }
  }
}

function Item({ m, active }: { m: MeetingSummary; active: boolean }) {
  const rec = useStore((s) => s.rec)
  const recording = rec.status !== 'idle' && rec.meetingId === m.id
  return (
    <a className={`side-item ${active ? 'active' : ''}`} href={`#/m/${m.id}`}>
      {m.source === 'upload' ? <FileAudio size={15} className="side-icon" /> : <Mic size={15} className="side-icon" />}
      <span className="side-title">{m.title || '제목 없는 회의'}</span>
      <span className="side-meta">
        {recording ? (
          <span className={`rec-dot ${rec.status === 'paused' ? 'paused' : ''}`} title={rec.status === 'paused' ? '일시정지' : '녹음 중'} />
        ) : m.status === 'processing' ? (
          <span className="side-progress"><Loader2 size={11} className="spin" />{m.progress != null ? `${Math.round(m.progress * 100)}%` : ''}</span>
        ) : m.duration ? (
          fmtDuration(m.duration)
        ) : null}
      </span>
    </a>
  )
}

const THEMES: { key: Theme; icon: typeof Sun; label: string }[] = [
  { key: 'light', icon: Sun, label: '밝게' },
  { key: 'dark', icon: Moon, label: '어둡게' },
  { key: 'system', icon: Monitor, label: '시스템' },
]

export function Sidebar({ onSettings }: { onSettings: () => void }) {
  const allMeetings = useStore((s) => s.meetings)
  const hidden = useStore((s) => s.hiddenMeetings)
  // 지우는 중(되돌리기 대기)인 회의는 숨긴다
  const meetings = useMemo(() => (hidden.length ? allMeetings.filter((m) => !hidden.includes(m.id)) : allMeetings), [allMeetings, hidden])
  const selectedId = useStore((s) => s.selectedId)
  const health = useStore((s) => s.health)
  const theme = useStore((s) => s.theme)
  const set = useStore((s) => s.set)
  const toast = useStore((s) => s.toast)
  const fileRef = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)

  useEffect(() => {
    const query = q.trim()
    if (!query) {
      setHits(null)
      return
    }
    const t = setTimeout(() => api.search(query).then(setHits).catch(() => setHits([])), 250)
    return () => clearTimeout(t)
  }, [q])

  const groups = useMemo(() => {
    const out: { label: string; items: MeetingSummary[] }[] = []
    for (const m of meetings) {
      const g = dateGroup(m.created_at)
      const last = out[out.length - 1]
      if (last && last.label === g) last.items.push(m)
      else out.push({ label: g, items: [m] })
    }
    return out
  }, [meetings])

  const setTheme = (t: Theme) => {
    set({ theme: t })
    try {
      localStorage.setItem('theme', t)
    } catch {
      /* 무시 */
    }
  }

  const live = health?.live
  return (
    <aside className="sidebar">
      <div className="side-head">
        <div className="brand">
          <img src="/favicon.svg" alt="" />
          <span>회의 노트</span>
        </div>
        <button className="icon-btn" onClick={() => set({ sidebarOpen: false })} title="사이드바 닫기"><PanelLeftClose size={16} /></button>
      </div>

      <div className="side-search">
        <Search size={14} />
        <input value={q} placeholder="검색" onChange={(e) => setQ(e.target.value)} />
        {q && <button className="icon-btn" onClick={() => setQ('')}><X size={13} /></button>}
      </div>

      <div className="side-actions">
        <button className="side-action" onClick={() => createAndRecord().catch(() => undefined)}>
          <Plus size={15} />새 회의 녹음
        </button>
        <button className="side-action" onClick={() => fileRef.current?.click()}>
          <FileUp size={15} />녹음 파일 올리기
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="audio/*,video/*,.m4a,.mp3,.wav,.mp4,.mov,.webm,.ogg,.flac,.aac"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>

      <nav className="side-list">
        {hits ? (
          <>
            <div className="side-group">검색 결과 {hits.length}개</div>
            {hits.length === 0 && <p className="side-empty">찾는 내용이 없어요</p>}
            {hits.map((h, i) => (
              <a
                key={i}
                className="side-hit"
                href={`#/m/${h.meeting_id}`}
                onClick={() => {
                  if (h.unit) {
                    const st = useStore.getState()
                    setTimeout(() => {
                      st.setTab(h.kind === 'final' ? 'final' : 'live')
                      st.flash(h.unit!)
                    }, 400)
                  }
                }}
              >
                <span className="hit-title">{h.title}{h.t != null && <em>{fmtTime(h.t)}</em>}</span>
                <span className="hit-snippet">{h.snippet}</span>
              </a>
            ))}
          </>
        ) : (
          <>
            {groups.length === 0 && <p className="side-empty">아직 회의가 없어요</p>}
            {groups.map((g) => (
              <div key={g.label}>
                <div className="side-group">{g.label}</div>
                {g.items.map((m) => <Item key={m.id} m={m} active={m.id === selectedId} />)}
              </div>
            ))}
          </>
        )}
      </nav>

      <div className="side-foot">
        <button
          className={`engine-status st-${live?.state || 'idle'}`}
          onClick={onSettings}
          title={live?.error || ''}
        >
          <span className="dot" />
          {live ? (live.kind === 'mock' ? '시연용 엔진' : live.model.split('/').pop()) : '서버 연결 중'}
          <em>{live?.state === 'ready' ? '준비됨' : live?.state === 'loading' ? '준비 중' : live?.state === 'error' ? '오류' : ''}</em>
        </button>
        <div className="foot-row">
          <div className="theme-switch">
            {THEMES.map(({ key, icon: Icon, label }) => (
              <button key={key} className={theme === key ? 'on' : ''} onClick={() => setTheme(key)} title={label}><Icon size={13} /></button>
            ))}
          </div>
          <span className="spacer" />
          <button className="icon-btn" onClick={onSettings} title="설정"><Settings size={15} /></button>
          {!health && <button className="icon-btn" onClick={() => toast('서버가 꺼져 있어요. ./run.sh로 실행해 주세요.', 'error')}>!</button>}
        </div>
      </div>
    </aside>
  )
}
