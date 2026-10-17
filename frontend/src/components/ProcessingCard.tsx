import { AlertCircle, Check, Loader2, Sparkles, X } from 'lucide-react'
import { api, type Job, type Meeting } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtDuration } from '../lib/time'

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

export function ProcessingCard({ meeting, job, compact }: { meeting: Meeting; job: Job | null; compact?: boolean }) {
  const toast = useStore((s) => s.toast)
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const loadMeetings = useStore((s) => s.loadMeetings)
  const recording = useStore((s) => s.rec.status !== 'idle' && s.rec.meetingId === meeting.id)
  const running = job && (job.status === 'queued' || job.status === 'running')

  const start = async () => {
    try {
      await api.process(meeting.id)
      await refreshMeeting()
      await loadMeetings()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const cancel = async () => {
    await api.cancel(meeting.id)
    await refreshMeeting()
    await loadMeetings()
  }

  if (running && job) {
    const pct = Math.round(job.progress * 100)
    if (compact) {
      return (
        <div className="proc-banner">
          <Loader2 size={14} className="spin" />
          <span>원본을 다시 만드는 중 · {pct}%</span>
          <div className="bar"><div style={{ width: `${pct}%` }} /></div>
          <button className="btn btn-sm btn-ghost" onClick={cancel}>취소</button>
        </div>
      )
    }
    return (
      <div className="proc-card">
        <div className="proc-head">
          <Loader2 size={18} className="spin" />
          <div>
            <h3>원본을 만들고 있어요</h3>
            <p className="muted">
              {job.status === 'queued' ? '앞선 작업이 끝나면 시작해요' : job.message || '더 큰 모델로 다시 받아 적고 화자를 나눠요'}
            </p>
          </div>
          <span className="spacer" />
          <button className="btn btn-sm btn-ghost" onClick={cancel}><X size={14} />취소</button>
        </div>
        <div className="bar big"><div style={{ width: `${pct}%` }} /></div>
        <div className="proc-meta">
          <span>{pct}%</span>
          {job.eta != null && job.eta > 5 && <span>약 {fmtDuration(job.eta)} 남음</span>}
        </div>
        <ol className="proc-steps">
          {STEPS.filter((s) => s.key !== 'convert' || meeting.source === 'upload').map((s) => {
            const st = stepState(job, s.key)
            return (
              <li key={s.key} className={st}>
                <span className="step-icon">{st === 'done' ? <Check size={12} /> : st === 'active' ? <Loader2 size={12} className="spin" /> : null}</span>
                {s.label}
              </li>
            )
          })}
        </ol>
        <p className="proc-tip">처리하는 동안 실시간 기록에 메모·댓글을 계속 달아도 돼요. 끝나면 원본의 같은 자리에 옮겨 보여드려요.</p>
      </div>
    )
  }

  if (job && job.status === 'error') {
    return (
      <div className="proc-card error">
        <div className="proc-head">
          <AlertCircle size={18} />
          <div>
            <h3>원본을 만들지 못했어요</h3>
            <p className="muted">{job.error}</p>
          </div>
        </div>
        <button className="btn btn-primary" onClick={start}>다시 시도</button>
      </div>
    )
  }

  if (compact) return null

  return (
    <div className="proc-card empty">
      <Sparkles size={22} />
      <h3>원본은 녹음을 마친 뒤 만들어져요</h3>
      <p className="muted">
        실시간 기록보다 큰 모델로 처음부터 다시 받아 적고, 누가 말했는지 나눕니다. 실시간 기록에 남긴 메모·댓글·수정은 원본의 같은 자리에 첨삭처럼 표시돼요.
      </p>
      {meeting.has_audio && !recording && (
        <button className="btn btn-primary" onClick={start}><Sparkles size={15} />지금 원본 만들기</button>
      )}
    </div>
  )
}
