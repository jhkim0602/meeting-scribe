import { CalendarDays, Clock3, Download, FileAudio, FileText, FolderDown, FolderOpen, Mic, MoreHorizontal, PanelLeft, RefreshCw, ScrollText, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { ProcessDialog } from './ProcessDialog'
import { deleteLater } from '../lib/annActions'
import { bridges, useStore } from '../lib/store'
import { fmtDate, fmtDuration } from '../lib/time'
import { Floating } from './Floating'
import { ParticipantDetails, ParticipantsProp } from './Participants'

const STATUS: Record<string, { label: string; tone: string }> = {
  empty: { label: '녹음 전', tone: 'gray' },
  recording: { label: '녹음 중', tone: 'red' },
  paused: { label: '일시정지', tone: 'amber' },
  recorded: { label: '녹음 완료', tone: 'gray' },
  processing: { label: '원본 스크립트 만드는 중', tone: 'blue' },
  ready: { label: '완료', tone: 'green' },
  error: { label: '오류', tone: 'red' },
}

export function PageHeader() {
  const meeting = useStore((s) => s.meeting)!
  const recStatus = useStore((s) => s.rec.status)
  const recMeeting = useStore((s) => s.rec.meetingId)
  // 길이는 분 단위로 보이므로 그때만 다시 그린다
  const recElapsed = useStore((s) => (s.rec.elapsed < 60 ? Math.floor(s.rec.elapsed) : Math.floor(s.rec.elapsed / 60) * 60))
  const rec = { status: recStatus, meetingId: recMeeting, elapsed: recElapsed }
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const set = useStore((s) => s.set)
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const loadMeetings = useStore((s) => s.loadMeetings)
  const toast = useStore((s) => s.toast)
  const [title, setTitle] = useState(meeting.title)
  const [menu, setMenu] = useState<DOMRect | null>(null)
  const titleRef = useRef<HTMLTextAreaElement>(null)
  const recordingHere = rec.status !== 'idle' && rec.meetingId === meeting.id
  const status = recordingHere
    ? (rec.status === 'paused' ? STATUS.paused : STATUS.recording)
    : meeting.status === 'processing' && meeting.source === 'upload' ? { label: '받아 적는 중', tone: 'blue' } : STATUS[meeting.status] || STATUS.empty
  const duration = recordingHere ? rec.elapsed : meeting.duration
  const reprocessLabel = meeting.source === 'upload' && !meeting.final_version
    ? '다시 받아 적기'
    : meeting.final_version ? '원본 스크립트 다시 만들기' : '원본 스크립트 만들기'
  const [choosing, setChoosing] = useState(false)

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

  // 이 회의 폴더를 Finder·파일 탐색기로 연다 (전사 기록·회의록 HTML을 새로 만들어 넣은 뒤)
  const reveal = async () => {
    await bridges.notes?.flush().catch(() => undefined)
    try {
      await api.revealMeeting(meeting.id)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  // 모델·화자 구분을 고르고 예상 시간을 본 뒤 시작한다
  const reprocess = () => {
    setMenu(null)
    setChoosing(true)
  }

  // 방금 쓴 노트·메모까지 담기도록 저장을 마친 뒤 연다 (새 탭은 먼저 열어 두어야 팝업 차단에 안 걸린다).
  // 인쇄와 파일로 받기는 열린 문서 위쪽에 있다.
  const openDoc = async (kind: 'transcript' | 'minutes') => {
    setMenu(null)
    const url = `/api/meetings/${meeting.id}/${kind}.html`
    const tab = window.open('', '_blank')
    await bridges.notes?.flush().catch(() => undefined)
    if (tab) tab.location.href = url
    else window.open(url, '_blank')
  }

  // 확인창 대신 바로 숨기고 10초 동안 되돌릴 수 있게 한다
  const remove = () => {
    setMenu(null)
    const id = meeting.id
    const st = useStore.getState()
    const unhide = () => useStore.setState({ hiddenMeetings: useStore.getState().hiddenMeetings.filter((x) => x !== id) })
    st.set({ hiddenMeetings: [...st.hiddenMeetings, id] })
    location.hash = ''
    deleteLater(
      `meeting:${id}`,
      `/api/meetings/${id}`,
      `"${meeting.title || '제목 없는 회의'}" 회의를`,
      () => {
        unhide()
        location.hash = `#/m/${id}`
      },
      (e) => {
        unhide()
        toast(`회의를 지우지 못했어요: ${e.message}`, 'error')
      },
      10000,
      () => {
        unhide()
        loadMeetings()
      },
    )
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
        <button className="icon-btn" onClick={reveal} title="이 회의 폴더 열기 (녹음 · 전사 기록 · 회의록)"><FolderOpen size={16} /></button>
        <button className="icon-btn" onClick={(e) => setMenu(e.currentTarget.getBoundingClientRect())} title="더 보기"><MoreHorizontal size={16} /></button>
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
          <ParticipantsProp />
          {meeting.original_name && <span className="prop"><FileAudio size={14} />{meeting.original_name}</span>}
        </div>
        <ParticipantDetails />
      </div>
      {menu && (
        <Floating rect={menu} onClose={() => setMenu(null)} className="menu">
          <button className="menu-item" onClick={() => openDoc('transcript')}>
            <ScrollText size={15} />전사 기록 보기
          </button>
          <button className="menu-item" onClick={() => openDoc('minutes')}>
            <FileText size={15} />회의록 보기
          </button>
          <a className="menu-item" href={`/api/meetings/${meeting.id}/export.zip`} onClick={() => setMenu(null)} title="날짜_제목 폴더: 전사 기록·회의록 HTML과 녹음. 회의 노트에서 다시 불러와 이어서 고칠 수 있어요">
            <FolderDown size={15} />회의 내보내기 (HTML + 녹음)
          </a>
          <a className="menu-item" href={`/api/meetings/${meeting.id}/export.md?download=1`} onClick={() => setMenu(null)}>
            <Download size={15} />Markdown으로 내보내기
          </a>
          {meeting.final_version > 0 && (
            <a className="menu-item" href={`/api/meetings/${meeting.id}/script.md?download=1`} onClick={() => setMenu(null)} title="녹음을 마친 뒤 큰 모델로 다시 받아 적고 화자를 나눈 참고용 기록 (회의 기록과 섞지 않아요)">
              <FileAudio size={15} />원본 스크립트 받기 (.md)
            </a>
          )}
          <button className="menu-item" onClick={reprocess} disabled={!meeting.has_audio || recordingHere || meeting.status === 'processing'}>
            <RefreshCw size={15} />{reprocessLabel}
          </button>
          <div className="menu-sep" />
          <button className="menu-item danger" onClick={remove} disabled={recordingHere} title={recordingHere ? '녹음을 마친 뒤 지울 수 있어요' : undefined}><Trash2 size={15} />회의 삭제</button>
        </Floating>
      )}
      {choosing && <ProcessDialog meeting={meeting} onClose={() => setChoosing(false)} />}
    </header>
  )
}
