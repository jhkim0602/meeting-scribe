import { AlertCircle, Check, Loader2, PauseCircle, RefreshCw, X } from 'lucide-react'
import { useState } from 'react'
import { api, type Job, type Meeting } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtDuration } from '../lib/time'
import { ProcessDialog } from './ProcessDialog'

const STEPS: { key: string; label: string }[] = [
  { key: 'convert', label: '오디오 준비' },
  { key: 'load', label: '모델 준비' },
  { key: 'transcribe', label: '받아쓰기 (Qwen3-ASR)' },
  { key: 'diarize', label: '화자 구분' },
  { key: 'merge', label: '정리' },
]

function stepState(job: Job, key: string): 'done' | 'active' | 'todo' {
  if (job.status === 'done') return 'done'
  const order = STEPS.map((s) => s.key)
  const cur = order.indexOf(job.phase || '')
  const idx = order.indexOf(key)
  if (cur < 0) return 'todo'
  return idx < cur ? 'done' : idx === cur ? 'active' : 'todo'
}

/** 녹음을 마친 뒤 만드는 원본 스크립트(참고용)와, 올린 파일 받아 적기의 진행 표시.
 *  녹음한 회의는 기록 위에 작은 띠로(기록은 그대로), 올린 파일은 기록이 생기기 전까지 카드로 보여 준다. */
export function ProcessingCard({ meeting, job, compact }: { meeting: Meeting; job: Job | null; compact?: boolean }) {
  const upload = meeting.source === 'upload'
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const loadMeetings = useStore((s) => s.loadMeetings)
  const recording = useStore((s) => s.rec.status !== 'idle' && s.rec.meetingId === meeting.id)
  const running = job && (job.status === 'queued' || job.status === 'running')
  // 시작은 언제나 고르는 창에서 (방금 올린 파일이면 바로 연다)
  const askProcess = useStore((s) => s.askProcess)
  const set = useStore((s) => s.set)
  const [choosing, setChoosing] = useState(false)
  const dialogOpen = choosing || (askProcess === meeting.id && !compact && !running)
  const start = () => setChoosing(true)
  const dialog = dialogOpen && (
    <ProcessDialog meeting={meeting} onClose={() => { setChoosing(false); if (askProcess === meeting.id) set({ askProcess: null }) }} />
  )
  const cancel = async () => {
    await api.cancel(meeting.id)
    await refreshMeeting()
    await loadMeetings()
  }

  if (running && job) {
    const pct = Math.round(job.progress * 100)
    // 녹음 중이거나 전원을 기다리는 동안은 멈춰 있다 (회의를 방해하지 않게)
    const waiting = job.waiting || null
    const icon = (size: number) => (waiting ? <PauseCircle size={size} className="muted" /> : <Loader2 size={size} className="spin" />)
    if (compact) {
      const what = upload ? '받아 적는 중' : '원본 스크립트'
      return (
        <div className={`proc-banner${waiting ? ' waiting' : ''}`}>
          {icon(14)}
          <span>{waiting ? `${what} · ${waiting}` : `${upload ? '받아 적는 중' : '원본 스크립트 만드는 중'} · ${pct}%`}</span>
          {!waiting && <span className="muted">{upload ? '다 받아 적은 부분부터 보여요' : '기록은 그대로예요'}</span>}
          <div className="bar"><div style={{ width: `${pct}%` }} /></div>
          <button className="btn btn-sm btn-ghost" onClick={cancel}>취소</button>
        </div>
      )
    }
    return (
      <div className={`proc-card${waiting ? ' waiting' : ''}`}>
        <div className="proc-head">
          {icon(18)}
          <div>
            <h3>{upload ? '파일을 받아 적고 있어요' : '원본 스크립트를 만들고 있어요'}</h3>
            <p className="muted">
              {waiting || (job.status === 'queued' ? '앞선 작업이 끝나면 시작해요' : job.message || '받아 적고 누가 말했는지 나눠요')}
            </p>
          </div>
          <span className="spacer" />
          <button className="btn btn-sm btn-ghost" onClick={cancel}><X size={14} />취소</button>
        </div>
        <div className="bar big"><div style={{ width: `${pct}%` }} /></div>
        <div className="proc-meta">
          <span>{pct}%</span>
          {!waiting && job.eta != null && job.eta > 5 && <span>약 {fmtDuration(job.eta)} 남음</span>}
        </div>
        <ol className="proc-steps">
          {STEPS.filter((s) => s.key !== 'convert' || meeting.source === 'upload').map((s) => {
            const st = stepState(job, s.key)
            return (
              <li key={s.key} className={st}>
                <span className="step-icon">{st === 'done' ? <Check size={12} /> : st === 'active' ? (waiting ? <PauseCircle size={12} /> : <Loader2 size={12} className="spin" />) : null}</span>
                {s.label}
              </li>
            )
          })}
        </ol>
        <p className="proc-tip">{upload ? '10분씩 받아 적는 대로 여기에 기록이 나와요. 틀린 글자는 바로 고치고, 화자는 다 받아 적은 뒤 칩이나 숫자키로 고르면 돼요.' : '참고용 원본 스크립트예요. 회의 기록과 메모·댓글은 그대로 두고 따로 받을 수 있어요.'}</p>
      </div>
    )
  }

  if (job && job.status === 'error') {
    const what = upload ? '받아 적지' : '원본 스크립트를 만들지'
    if (compact) {
      return (
        <div className="proc-banner error">
          <AlertCircle size={14} />
          <span>{what} 못했어요</span>
          <button className="btn btn-sm" onClick={start}><RefreshCw size={13} />다시 시도</button>
          {dialog}
        </div>
      )
    }
    return (
      <div className="proc-card error">
        <div className="proc-head">
          <AlertCircle size={18} />
          <div>
            <h3>{what} 못했어요</h3>
            <p className="muted">{job.error}</p>
          </div>
        </div>
        <button className="btn btn-primary" onClick={start}>다시 시도</button>
        {dialog}
      </div>
    )
  }

  if (compact || !upload) return null

  return (
    <div className="proc-card empty">
      <RefreshCw size={22} />
      <h3>아직 받아 적지 않았어요</h3>
      {meeting.has_audio && !recording && (
        <button className="btn btn-primary" onClick={start}>받아 적기 시작</button>
      )}
      {dialog}
    </div>
  )
}
