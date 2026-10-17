import { AlertTriangle, Coffee, HardDrive, Mic, MicOff, Pause, Play, WifiOff, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { pauseRecording, reconnectMic, resumeRecording } from '../hooks/recorder'
import { isPersistent, useStore } from '../lib/store'
import { fmtDuration } from '../lib/time'

// 한 번 띄우는 알림은 이만큼 뒤 녹음 표시줄로 접힌다 (마우스를 올려 두면 기다린다)
const COLLAPSE_MS = 15000

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

function fmtLeft(sec: number) {
  if (sec >= 3600) return `${Math.floor(sec / 3600)}시간 ${Math.round((sec % 3600) / 60)}분`
  return `${Math.max(1, Math.round(sec / 60))}분`
}

/** 녹음 중 예외 상황 알림. 계속 보이는 알림은 해결될 때까지, 한 번 띄우는 알림은 잠시 뒤 접힌다. */
export function RecAlertBar() {
  const alert = useStore((s) => s.rec.alert)
  const collapsed = useStore((s) => s.rec.alertCollapsed)
  const meetingId = useStore((s) => s.rec.meetingId)
  const selectedId = useStore((s) => s.selectedId)
  const setRec = useStore((s) => s.setRec)
  const now = useNow(alert?.kind === 'server-lost')
  const [hover, setHover] = useState(false)
  const timer = useRef<number>(0)

  const collapse = () => {
    const cur = useStore.getState().rec.alert
    // 일시정지 중 말소리는 표시줄의 점으로, 나머지는 표시줄의 작은 버튼으로 접힌다
    if (cur?.kind === 'paused-speech') setRec({ alert: null, alertCollapsed: false })
    else setRec({ alertCollapsed: true })
  }

  useEffect(() => {
    window.clearTimeout(timer.current)
    if (!alert || collapsed || hover || isPersistent(alert)) return
    timer.current = window.setTimeout(collapse, COLLAPSE_MS)
    return () => window.clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alert, collapsed, hover])

  if (!alert || collapsed) return null
  const close = () => setRec({ alert: null, alertCollapsed: false })
  const elsewhere = meetingId && meetingId !== selectedId

  let tone = 'warn'
  let icon = <AlertTriangle size={16} />
  let text: React.ReactNode = null
  let actions: React.ReactNode = null
  let onClose: (() => void) | null = close

  switch (alert.kind) {
    case 'paused-speech':
      icon = <Pause size={16} />
      text = <><b>녹음이 일시정지된 상태예요.</b> 말소리가 들리는데 기록되지 않고 있어요.</>
      actions = <button className="btn btn-sm btn-primary" onClick={resumeRecording}><Mic size={14} />녹음 이어하기</button>
      break
    case 'silence':
      tone = 'info'
      icon = <Coffee size={16} />
      text = <><b>{alert.minutes >= 1 ? `${alert.minutes}분` : `${Math.round(alert.minutes * 60)}초`}째 조용해요. 쉬는 시간인가요?</b> 일시정지하면 조용해진 시점부터 쉬는 시간으로 표시해요.</>
      actions = (
        <>
          <button className="btn btn-sm btn-primary" onClick={() => pauseRecording({ fromT: alert.fromT, fromWall: alert.fromWall })}><Pause size={14} />네, 일시정지</button>
          <button className="btn btn-sm" onClick={close}>아니요, 계속 녹음</button>
        </>
      )
      onClose = null
      break
    case 'mic-lost':
      tone = 'error'
      icon = <MicOff size={16} />
      text = <><b>마이크를 찾을 수 없어 녹음을 일시정지했어요.</b> 마이크를 연결하고 다시 시도해 주세요.</>
      actions = <button className="btn btn-sm btn-primary" onClick={async () => { if (await reconnectMic({ manual: true })) resumeRecording() }}><Play size={14} />다시 연결하고 이어하기</button>
      onClose = null
      break
    case 'mic-silent':
      tone = 'error'
      icon = <MicOff size={16} />
      text = <><b>1분째 마이크에서 소리가 들어오지 않아요.</b> 마이크가 꺼져 있거나 다른 앱이 쓰고 있을 수 있어요.</>
      actions = <button className="btn btn-sm btn-primary" onClick={() => reconnectMic({ manual: true })}><Mic size={14} />마이크 다시 연결</button>
      onClose = collapse
      break
    case 'server-lost':
      tone = 'error'
      icon = <WifiOff size={16} />
      text = <><b>서버 연결이 끊겼어요.</b> 다시 연결하는 중이에요. 그동안의 소리는 브라우저에 모아 두고 있어요 ({fmtDuration(Math.max(0, (now - alert.since) / 1000))}).</>
      onClose = null
      break
    case 'disk-low':
      icon = <HardDrive size={16} />
      text = <><b>저장 공간이 {fmtLeft(alert.freeS)} 분량 남았어요.</b> 녹음이 길어지면 다른 파일을 지워 공간을 비워 주세요.</>
      onClose = collapse
      break
    case 'disk-full':
      tone = 'error'
      icon = <HardDrive size={16} />
      text = <><b>저장 공간이 가득 차서 녹음을 멈췄어요.</b> 여기까지는 안전하게 저장됐어요. 공간을 비우면 이어서 녹음할 수 있어요.</>
      break
  }

  return (
    <div className={`rec-alert tone-${tone}`} role="alert" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <span className="rec-alert-icon">{icon}</span>
      <span className="rec-alert-text">
        {text}
        {elsewhere && <a href={`#/m/${meetingId}`} className="rec-alert-link">녹음 중인 회의로 가기</a>}
      </span>
      <span className="rec-alert-actions">{actions}</span>
      {onClose && (
        <button className="icon-btn" onClick={onClose} title={isPersistent(alert) && alert.kind !== 'disk-full' ? '접기' : '닫기'}><X size={14} /></button>
      )}
    </div>
  )
}
