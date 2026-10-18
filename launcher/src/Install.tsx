import { useEffect, useRef, useState } from 'react'
import { call, on, type DoctorItem, type InstallEvent, type Step } from './api'
import { Bar, StatusIcon } from './ui'

const STEPS: { key: Exclude<Step, 'all'>; label: string; hint: string }[] = [
  { key: 'python', label: '파이썬 준비', hint: '앱 전용으로 따로 설치해요 (맥에 원래 있는 것은 건드리지 않아요)' },
  { key: 'packages', label: '받아쓰기 패키지', hint: '약 1GB' },
  { key: 'models', label: '받아쓰기 모델', hint: '이미 받은 것은 건너뛰어요' },
  { key: 'check', label: '점검', hint: '' },
]

type Row = { status: InstallEvent['status'] | 'todo'; progress: number | null; message: string; detail?: unknown }

/** 설치 진행: 단계마다 상태와 진행률. 끝나면 onDone, 실패하면 다시 시도. */
export function Install({ onDone, title = '설치하는 중', autoStart = true }: { onDone: (checks: DoctorItem[] | null) => void; title?: string; autoStart?: boolean }) {
  const [rows, setRows] = useState<Record<string, Row>>(() => Object.fromEntries(STEPS.map((s) => [s.key, { status: 'todo', progress: null, message: '' }])))
  const [failed, setFailed] = useState<string | null>(null)
  const [log, setLog] = useState<string | null>(null)
  const started = useRef(false)
  const checks = useRef<DoctorItem[] | null>(null)

  useEffect(() => {
    let off: (() => void) | undefined
    let gone = false
    on<InstallEvent>('install', (ev) => {
      if (ev.step === 'all') {
        if (ev.status === 'done') onDone(checks.current)
        else if (ev.status === 'error') setFailed(ev.message)
        return
      }
      if (ev.step === 'check' && ev.detail && typeof ev.detail === 'object' && 'items' in ev.detail) checks.current = (ev.detail as { items: DoctorItem[] }).items
      setRows((r) => ({ ...r, [ev.step]: { status: ev.status, progress: ev.progress, message: ev.message, detail: ev.detail } }))
    }).then((f) => (gone ? f() : (off = f)))
    if (autoStart && !started.current) {
      started.current = true
      void call('install_start')
    }
    return () => {
      gone = true
      off?.()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const retry = () => {
    setFailed(null)
    setLog(null)
    setRows(Object.fromEntries(STEPS.map((s) => [s.key, { status: 'todo', progress: null, message: '' }])))
    void call('install_start')
  }
  const total = STEPS.reduce((n, s) => {
    const r = rows[s.key]
    const w = s.key === 'models' ? 0.55 : s.key === 'packages' ? 0.3 : 0.075
    const p = r.status === 'done' || r.status === 'skip' || r.status === 'warn' ? 1 : r.status === 'running' ? r.progress ?? 0.3 : 0
    return n + w * p
  }, 0)

  return (
    <div className="install">
      <div className="install-head">
        <h2>{failed ? '설치를 마치지 못했어요' : title}</h2>
        {!failed && <span className="pct">{Math.round(total * 100)}%</span>}
      </div>
      {!failed && <Bar value={total} />}
      <ol className="steps">
        {STEPS.map((s) => {
          const r = rows[s.key]
          return (
            <li key={s.key} className={`st-${r.status}`}>
              <StatusIcon status={r.status} />
              <div className="step-body">
                <div className="step-title">
                  {s.label}
                  {r.status === 'skip' && <span className="muted"> · 이미 준비됨</span>}
                </div>
                <div className="step-msg">{r.message || s.hint}</div>
                {r.status === 'running' && r.progress !== null && s.key !== 'check' && <Bar value={r.progress} />}
              </div>
            </li>
          )
        })}
      </ol>
      {failed && (
        <div className="callout err">
          <pre>{failed}</pre>
          <div className="row">
            <button className="btn primary" onClick={retry}>다시 시도</button>
            <button className="btn" onClick={async () => setLog(await call<string>('log_tail', { name: 'install', lines: 60 }))}>설치 기록 보기</button>
          </div>
          {log && <pre className="log">{log}</pre>}
        </div>
      )}
      {!failed && (
        <div className="row end">
          <button className="btn quiet" onClick={() => call('install_cancel')}>멈추기</button>
        </div>
      )}
    </div>
  )
}
