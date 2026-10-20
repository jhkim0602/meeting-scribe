import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type Meeting, type ProcessOptions } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtDuration } from '../lib/time'

/** 받아 적기(올린 파일)·원본 스크립트(녹음한 회의)를 시작하기 전에 모델·화자 구분·화자 수를 고르고 예상 시간을 본다.
 *  저절로 시작하지 않는다: 여기서 [시작]을 눌러야 한다. */
export function ProcessDialog({ meeting, onClose }: { meeting: Meeting; onClose: () => void }) {
  const toast = useStore((s) => s.toast)
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const loadMeetings = useStore((s) => s.loadMeetings)
  const [opts, setOpts] = useState<ProcessOptions | null>(null)
  const [model, setModel] = useState('')
  const [speakers, setSpeakers] = useState(true)
  const [count, setCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const upload = meeting.source === 'upload'

  useEffect(() => {
    api.processOptions(meeting.id).then((o) => {
      setOpts(o)
      setModel(o.default)
      setSpeakers(o.speakers.available)
      setCount(o.num_speakers ?? 0)
    }).catch((e) => toast((e as Error).message, 'error'))
  }, [meeting.id, toast])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const cur = opts?.choices.find((c) => c.id === model)
  const eta = cur ? (speakers ? cur.eta_s.with_speakers : cur.eta_s.without_speakers) : 0

  const start = async () => {
    setBusy(true)
    try {
      await api.process(meeting.id, { final: model, speakers, num_speakers: speakers ? count : 0 })
      await Promise.all([refreshMeeting(), loadMeetings()])
      onClose()
    } catch (e) {
      toast((e as Error).message, 'error')
      setBusy(false)
    }
  }

  // 카드 안에서 열어도 그 카드의 글꼴·정렬을 물려받지 않게 문서 맨 위에 그린다
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal process-dialog" role="dialog" aria-label={upload ? '받아 적기' : '원본 스크립트 만들기'}>
        <div className="modal-head">
          <h2>{upload ? '받아 적기' : meeting.final_version ? '원본 스크립트 다시 만들기' : '원본 스크립트 만들기'}</h2>
          <button className="icon-btn" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-body">
          {!opts ? (
            <p className="muted">불러오는 중…</p>
          ) : (
            <>
              <p className="muted">
                녹음 {opts.duration ? fmtDuration(opts.duration) : '길이 모름'}
                {upload ? ' · 받아 적은 결과가 회의 기록이 돼요.' : ' · 회의 기록과 메모·댓글은 그대로 두고, 참고용 스크립트를 따로 만들어요.'}
              </p>
              <section>
                <h3>받아쓰기 모델</h3>
                <div className="choices">
                  {opts.choices.map((c) => (
                    <label key={c.id} className={`choice ${c.id === model ? 'on' : ''}`}>
                      <input type="radio" name="model" checked={c.id === model} onChange={() => setModel(c.id)} />
                      <span className="choice-main">
                        <b>{c.label}</b> <span className="tag">{c.tag}</span>
                        <span className="muted small">{c.detail}</span>
                      </span>
                      <span className="choice-meta">
                        <span>약 {fmtDuration(speakers ? c.eta_s.with_speakers : c.eta_s.without_speakers)}</span>
                        <span className="muted small">{c.downloaded ? '받아 둠' : `처음 ${c.download_gb}GB 받기`} · 메모리 {c.memory_gb}GB</span>
                      </span>
                    </label>
                  ))}
                </div>
              </section>
              <section>
                <h3>화자 구분</h3>
                <label className="check">
                  <input type="checkbox" checked={speakers} disabled={!opts.speakers.available} onChange={(e) => setSpeakers(e.target.checked)} />
                  누가 말했는지 나누기 {opts.speakers.available ? '— 끄면 더 빨리 끝나요' : ''}
                </label>
                {opts.speakers.reason && <p className="muted small">{opts.speakers.reason}</p>}
                {speakers && (
                  <label className="field-inline">
                    <span>참석자 수</span>
                    <select className="input select" value={count} onChange={(e) => setCount(Number(e.target.value))}>
                      <option value={0}>자동으로 찾기</option>
                      {[2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => <option key={n} value={n}>{n}명</option>)}
                    </select>
                  </label>
                )}
              </section>
            </>
          )}
        </div>
        <div className="modal-foot">
          {cur && (
            <span className="muted small process-eta">
              예상 약 {fmtDuration(eta)}{cur.measured ? ' (이 컴퓨터에서 잰 속도)' : ' (어림값)'}{cur.downloaded ? '' : ` + 모델 ${cur.download_gb}GB 받기`} · 녹음 중에는 잠시 멈춰요
            </span>
          )}
          <button className="btn" onClick={onClose}>취소</button>
          <button className="btn btn-primary" onClick={start} disabled={!cur || busy}>시작</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
