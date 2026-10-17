import { AlertTriangle, Coffee, Mic, MicOff, Pause, Play, WifiOff, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { pauseRecording, reconnectMic, resumeRecording } from '../hooks/recorder'
import { useStore } from '../lib/store'

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

/** 녹음 중 예외 상황 알림 (일시정지 중 말소리, 긴 무음, 마이크/서버 끊김) */
export function RecAlertBar() {
  const alert = useStore((s) => s.rec.alert)
  const meetingId = useStore((s) => s.rec.meetingId)
  const selectedId = useStore((s) => s.selectedId)
  const setRec = useStore((s) => s.setRec)
  const now = useNow(alert?.kind === 'server-lost')

  // 일시정지 중 말소리 알림은 10초 뒤 저절로 닫는다
  useEffect(() => {
    if (alert?.kind !== 'paused-speech') return
    const id = setTimeout(() => {
      if (useStore.getState().rec.alert?.kind === 'paused-speech') setRec({ alert: null })
    }, 10000)
    return () => clearTimeout(id)
  }, [alert, setRec])

  if (!alert) return null
  const close = () => setRec({ alert: null })
  const elsewhere = meetingId && meetingId !== selectedId

  let tone = 'warn'
  let icon = <AlertTriangle size={16} />
  let text: React.ReactNode = null
  let actions: React.ReactNode = null

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
      break
    case 'mic-lost':
      tone = 'error'
      icon = <MicOff size={16} />
      text = alert.state === 'reconnecting'
        ? <><b>마이크 연결이 끊겼어요.</b> 다시 연결하는 중이에요.</>
        : <><b>마이크를 찾을 수 없어 녹음을 일시정지했어요.</b> 마이크를 연결하고 다시 시도해 주세요.</>
      actions = alert.state === 'failed' ? (
        <button className="btn btn-sm btn-primary" onClick={async () => { if (await reconnectMic()) resumeRecording() }}><Play size={14} />다시 연결하고 이어하기</button>
      ) : null
      break
    case 'mic-silent':
      tone = 'error'
      icon = <MicOff size={16} />
      text = <><b>마이크에서 소리가 들어오지 않아요.</b> 마이크가 꺼져 있거나 다른 앱이 쓰고 있을 수 있어요.</>
      actions = <button className="btn btn-sm btn-primary" onClick={() => reconnectMic()}><Mic size={14} />마이크 다시 연결</button>
      break
    case 'server-lost': {
      const secs = Math.max(0, Math.round((now - alert.since) / 1000))
      tone = 'error'
      icon = <WifiOff size={16} />
      text = <><b>서버 연결이 끊겼어요.</b> 다시 연결하는 중이에요. 그동안의 소리는 브라우저에 모아 두고 있어요 ({secs}초).</>
      break
    }
  }

  return (
    <div className={`rec-alert tone-${tone}`} role="alert">
      <span className="rec-alert-icon">{icon}</span>
      <span className="rec-alert-text">
        {text}
        {elsewhere && <a href={`#/m/${meetingId}`} className="rec-alert-link">녹음 중인 회의로 가기</a>}
      </span>
      <span className="rec-alert-actions">{actions}</span>
      {alert.kind !== 'server-lost' && alert.kind !== 'mic-lost' && (
        <button className="icon-btn" onClick={close} title="닫기"><X size={14} /></button>
      )}
    </div>
  )
}
