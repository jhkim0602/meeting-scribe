import { CalendarDays, Clock3, Download, FileAudio, Mic, MoreHorizontal, PanelLeft, RefreshCw, Trash2, Users } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtDate, fmtDuration } from '../lib/time'
import { Floating } from './Floating'

const STATUS: Record<string, { label: string; tone: string }> = {
  empty: { label: '녹음 전', tone: 'gray' },
  recording: { label: '녹음 중', tone: 'red' },
  recorded: { label: '원본 전', tone: 'amber' },
  processing: { label: '원본 만드는 중', tone: 'blue' },
  ready: { label: '원본 완료', tone: 'green' },
  error: { label: '오류', tone: 'red' },
}

export function PageHeader() {
  const meeting = useStore((s) => s.meeting)!
  const final = useStore((s) => s.final)
  const rec = useStore((s) => s.rec)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const set = useStore((s) => s.set)
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const loadMeetings = useStore((s) => s.loadMeetings)
  const toast = useStore((s) => s.toast)
  const [title, setTitle] = useState(meeting.title)
  const [menu, setMenu] = useState<DOMRect | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const titleRef = useRef<HTMLTextAreaElement>(null)
  const recordingHere = rec.status !== 'idle' && rec.meetingId === meeting.id
  const status = recordingHere ? STATUS.recording : STATUS[meeting.status] || STATUS.empty
  const duration = recordingHere ? rec.elapsed : meeting.duration

  useEffect(() => setTitle(meeting.title), [meeting.id, meeting.title])
  useEffect(() => {
    const el = titleRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${el.scrollHeight}px`
    }
  }, [title])

  const saveTitle = async () => {
    const v = title.trim()
    if (v === meeting.title) return
    await api.patchMeeting(meeting.id, { title: v })
    await Promise.all([refreshMeeting(), loadMeetings()])
  }

  const reprocess = async () => {
    setMenu(null)
    try {
      await api.process(meeting.id)
      await refreshMeeting()
      await loadMeetings()
      useStore.getState().setTab('final')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const remove = async () => {
    try {
      await api.deleteMeeting(meeting.id)
      setMenu(null)
      location.hash = ''
      await useStore.getState().select(null)
      await loadMeetings()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <header className="page-header">
      <div className="topbar">
        {!sidebarOpen && (
          <button className="icon-btn" onClick={() => set({ sidebarOpen: true })} title="사이드바 열기"><PanelLeft size={16} /></button>
        )}
        <span className="crumb">{meeting.source === 'upload' ? <FileAudio size={14} /> : <Mic size={14} />}{meeting.title || '제목 없는 회의'}</span>
        <span className="spacer" />
        <span className={`status-pill tone-${status.tone}`}><span className="dot" />{status.label}</span>
        <button className="icon-btn" onClick={(e) => { setConfirmDelete(false); setMenu(e.currentTarget.getBoundingClientRect()) }} title="더 보기"><MoreHorizontal size={16} /></button>
      </div>
      <div className="title-wrap">
        <textarea
          ref={titleRef}
          className="page-title"
          rows={1}
          value={title}
          placeholder="제목 없는 회의"
          onChange={(e) => setTitle(e.target.value.replace(/\n/g, ''))}
          onBlur={saveTitle}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              ;(e.target as HTMLTextAreaElement).blur()
            }
          }}
        />
        <div className="props">
          <span className="prop"><CalendarDays size={14} />{fmtDate(meeting.created_at)}</span>
          <span className="prop"><Clock3 size={14} />{duration ? fmtDuration(duration) : '0분'}</span>
          {final && <span className="prop"><Users size={14} />화자 {final.speakers.length}명</span>}
          {meeting.original_name && <span className="prop"><FileAudio size={14} />{meeting.original_name}</span>}
        </div>
      </div>
      {menu && (
        <Floating rect={menu} onClose={() => setMenu(null)} className="menu">
          <a className="menu-item" href={`/api/meetings/${meeting.id}/export.md?download=1`} onClick={() => setMenu(null)}>
            <Download size={15} />Markdown으로 내보내기
          </a>
          <button className="menu-item" onClick={reprocess} disabled={!meeting.has_audio || recordingHere || meeting.status === 'processing'}>
            <RefreshCw size={15} />원본 다시 만들기
          </button>
          <div className="menu-sep" />
          {confirmDelete ? (
            <button className="menu-item danger" onClick={remove}><Trash2 size={15} />정말 삭제할까요? 녹음·노트가 모두 지워져요</button>
          ) : (
            <button className="menu-item danger" onClick={() => setConfirmDelete(true)} disabled={recordingHere}><Trash2 size={15} />회의 삭제</button>
          )}
        </Floating>
      )}
    </header>
  )
}
