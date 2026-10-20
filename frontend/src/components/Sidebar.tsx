import { FileAudio, FileUp, FolderInput, Loader2, Mic, Moon, PanelLeftClose, Plus, Search, Settings, Sun, Monitor, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createAndRecord } from '../hooks/recorder'
import { api, type MeetingSummary, type SearchHit } from '../lib/api'
import { useStore, type Theme } from '../lib/store'
import { Floating } from './Floating'
import { FolderTree, type ItemExtra } from './FolderTree'
import { dateGroup, fmtDuration, fmtTime, fmtDownload } from '../lib/time'

const stem = (name: string) => name.replace(/\.[^.]+$/, '')

/** 내보낸 회의 파일은 불러오고(zip은 하나씩, HTML은 같은 이름의 녹음과 함께), 나머지는 녹음 파일로 올린다 */
export async function uploadFiles(files: FileList | File[]) {
  const st = useStore.getState()
  const list = Array.from(files)
  const used = new Set<File>()
  for (const p of list.filter((f) => /\.(zip|html?)$/i.test(f.name))) {
    const audio = /\.html?$/i.test(p.name) ? list.find((f) => /\.(m4a|wav)$/i.test(f.name) && stem(f.name) === stem(p.name)) : undefined
    used.add(p)
    if (audio) used.add(audio)
    st.toast(`'${p.name}' 불러오는 중…`)
    try {
      const m = await api.importMeeting(audio ? [p, audio] : [p])
      await st.loadMeetings()
      location.hash = `#/m/${m.id}`
      st.toast('회의를 불러왔어요. 이어서 고칠 수 있어요.', 'success')
    } catch (e) {
      st.toast(`불러오기 실패: ${(e as Error).message}`, 'error')
    }
  }
  for (const f of list.filter((x) => !used.has(x))) {
    st.toast(`'${f.name}' 올리는 중…`)
    try {
      const m = await api.upload(f)
      await st.loadMeetings()
      st.set({ askProcess: m.id }) // 올린 뒤 바로 모델 고르는 창이 뜬다 (알림 대신)
      location.hash = `#/m/${m.id}`
    } catch (e) {
      st.toast(`업로드 실패: ${(e as Error).message}`, 'error')
    }
  }
}

function Item({ m, active, extra }: { m: MeetingSummary; active: boolean; extra?: ItemExtra }) {
  // 이 회의를 녹음 중일 때의 상태만 본다 (소리 크기·시간이 바뀔 때마다 목록을 다시 그리지 않게)
  const recStatus = useStore((s) => (s.rec.meetingId === m.id ? s.rec.status : 'idle'))
  const recording = recStatus !== 'idle'
  const rec = { status: recStatus }
  return (
    <a className={`side-item ${active ? 'active' : ''}`} href={`#/m/${m.id}`} {...extra?.drag} onContextMenu={extra?.onContextMenu}>
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
  // 왼쪽 아래 모델 표시: 눌러서 실시간 받아쓰기 모델을 바로 바꾼다 (녹음 중에는 못 바꾼다)
  const [modelMenu, setModelMenu] = useState<DOMRect | null>(null)
  const settings = useStore((s) => s.settings)
  const recordingAny = useStore((s) => s.rec.status !== 'idle')
  const pickModel = async (id: string) => {
    setModelMenu(null)
    if (id === settings?.live_model) return
    const st = useStore.getState()
    try {
      await api.saveSettings({ live_model: id })
      await Promise.all([st.loadSettings(), st.loadHealth()])
      st.toast('실시간 받아쓰기 모델을 바꿨어요. 새 모델을 올리는 중이에요 (처음이면 내려받아요).', 'success')
    } catch (e) {
      st.toast((e as Error).message, 'error')
    }
  }
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
  const importRef = useRef<HTMLInputElement>(null)
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

  // 폴더 밖 회의만 날짜별로 (폴더 안 회의는 FolderTree가 그린다)
  const folderIds = useStore((s) => s.groups)
  const groups = useMemo(() => {
    const known = new Set(folderIds.map((g) => g.id))
    const out: { label: string; items: MeetingSummary[] }[] = []
    for (const m of meetings) {
      if (m.group_id && known.has(m.group_id)) continue
      const g = dateGroup(m.created_at)
      const last = out[out.length - 1]
      if (last && last.label === g) last.items.push(m)
      else out.push({ label: g, items: [m] })
    }
    return out
  }, [meetings, folderIds])

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
        <button className="side-action" onClick={() => importRef.current?.click()} title="회의 노트에서 내보낸 zip, 또는 HTML과 녹음 파일">
          <FolderInput size={15} />회의 불러오기
        </button>
        <input
          ref={importRef}
          type="file"
          accept=".zip,.html,.htm,.m4a,.wav"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
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
                      st.setTab('live')
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
          <FolderTree
            meetings={meetings}
            renderItem={(m, extra) => <Item key={m.id} m={m} active={m.id === selectedId} extra={extra} />}
            rest={(extra) => (
              <>
                {meetings.length === 0 && <p className="side-empty">아직 회의가 없어요</p>}
                {groups.map((g) => (
                  <div key={g.label}>
                    <div className="side-group">{g.label}</div>
                    {g.items.map((m) => <Item key={m.id} m={m} active={m.id === selectedId} extra={extra(m)} />)}
                  </div>
                ))}
              </>
            )}
          />
        )}
      </nav>

      <div className="side-foot">
        <button
          className={`engine-status st-${live?.state || 'idle'}`}
          onClick={(e) => setModelMenu(e.currentTarget.getBoundingClientRect())}
          title={live?.error || '실시간 받아쓰기 모델 바꾸기'}
        >
          <span className="dot" />
          {live ? (live.kind === 'mock' ? '시연용 엔진' : live.model.split('/').pop()) : '서버 연결 중'}
          <em>{live?.state === 'ready' ? '준비됨' : live?.state === 'loading' ? (live.download ? `내려받는 중 ${fmtDownload(live.download)}` : '준비 중') : live?.state === 'error' ? '오류' : live?.state === 'idle' ? '쉬는 중' : ''}</em>
        </button>
        {modelMenu && (
          <Floating rect={modelMenu} placement="above" onClose={() => setModelMenu(null)} className="menu model-menu">
            <div className="menu-label">실시간 받아쓰기 모델</div>
            {(settings?.live_choices || []).map((c) => (
              <button
                key={c.id}
                className={`menu-item ${c.id === settings?.live_model ? 'on' : ''}`}
                disabled={recordingAny}
                title={recordingAny ? '녹음을 마친 뒤 바꿀 수 있어요' : c.detail}
                onClick={() => pickModel(c.id)}
              >
                <span className="model-name">{c.label}</span>
                <span className="muted small">{c.id === settings?.live_model ? '쓰는 중' : `${c.tag} · 메모리 ${c.memory_gb}GB`}</span>
              </button>
            ))}
            <div className="menu-sep" />
            <button className="menu-item" onClick={() => { setModelMenu(null); onSettings() }}>설정 열기</button>
          </Floating>
        )}
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
