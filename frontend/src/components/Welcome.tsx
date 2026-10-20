import { FileUp, Mic, PanelLeft } from 'lucide-react'
import { useRef, useState, useMemo } from 'react'
import { useStore } from '../lib/store'
import { fmtDuration, fmtRelative } from '../lib/time'
import { uploadFiles } from './Sidebar'

export function Welcome() {
  const allMeetings = useStore((s) => s.meetings)
  const hidden = useStore((s) => s.hiddenMeetings)
  // 지우는 중(되돌리기 대기)인 회의는 숨긴다
  const meetings = useMemo(() => (hidden.length ? allMeetings.filter((m) => !hidden.includes(m.id)) : allMeetings), [allMeetings, hidden])
  const health = useStore((s) => s.health)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const set = useStore((s) => s.set)
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const hour = new Date().getHours()
  const greet = hour < 12 ? '좋은 아침이에요' : hour < 18 ? '좋은 오후예요' : '좋은 저녁이에요'

  return (
    <div
      className={`welcome ${drag ? 'drag' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDrag(true)
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files)
      }}
    >
      {!sidebarOpen && (
        <button className="icon-btn welcome-side" onClick={() => set({ sidebarOpen: true })}><PanelLeft size={16} /></button>
      )}
      <div className="welcome-inner">
        <h1>{greet}</h1>
        <p className="muted">회의를 녹음하면 실시간으로 받아 적고, 그 위에 바로 메모하고 고칠 수 있어요. 모든 처리는 이 맥 안에서만 이루어져요.</p>
        <div className="start-cards">
          <button className="start-card primary" onClick={() => useStore.getState().set({ newMeetingOpen: true })}>
            <span className="start-icon rec"><Mic size={20} /></span>
            <span className="start-title">새 회의 녹음</span>
            <span className="start-desc">제목·참석자를 정하고 시작해요. 실시간으로 받아 적고, 틀린 글자는 그 자리에서 고쳐요</span>
          </button>
          <button className="start-card" onClick={() => fileRef.current?.click()}>
            <span className="start-icon"><FileUp size={20} /></span>
            <span className="start-title">녹음 파일 올리기</span>
            <span className="start-desc">m4a · mp3 · wav · mp4, 5시간 길이도 괜찮아요</span>
          </button>
          <input ref={fileRef} type="file" hidden multiple accept="audio/*,video/*" onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        </div>
        {health && health.live.kind === 'mock' && (
          <div className="notice">
            지금은 <b>시연용 엔진</b>으로 동작하고 있어요. 실제 받아쓰기는 맥에서 <code>./setup_mac.sh</code>로 설치하면 Qwen3-ASR로 바뀌어요.
          </div>
        )}
        {health && !health.ffmpeg && <div className="notice warn">ffmpeg가 없어 파일 업로드를 처리할 수 없어요. <code>brew install ffmpeg</code>로 설치해 주세요.</div>}
        {meetings.length > 0 && (
          <div className="recent">
            <h4>최근 회의</h4>
            {meetings.slice(0, 6).map((m) => (
              <a key={m.id} className="recent-item" href={`#/m/${m.id}`}>
                <span>{m.title || '제목 없는 회의'}</span>
                <span className="muted">{m.duration ? fmtDuration(m.duration) + ' · ' : ''}{fmtRelative(m.created_at)}</span>
              </a>
            ))}
          </div>
        )}
        <p className="drop-hint">파일을 이 화면에 끌어다 놓아도 돼요</p>
      </div>
    </div>
  )
}
