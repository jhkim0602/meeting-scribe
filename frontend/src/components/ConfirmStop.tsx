import { Pause, Square } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useStore } from '../lib/store'
import { fmtTime } from '../lib/time'

interface Props {
  onPause: () => void
  onStop: () => void
  onClose: () => void
}

/** 녹음 종료 확인창. 잠깐 쉬는 경우를 위해 일시정지를 함께 권한다. */
export function ConfirmStop({ onPause, onStop, onClose }: Props) {
  const elapsed = useStore((s) => s.rec.elapsed)
  const paused = useStore((s) => s.rec.status === 'paused')
  const autoProcess = useStore((s) => s.settings?.auto_process ?? true)
  const keepRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    keepRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal confirm" role="alertdialog" aria-labelledby="stop-title">
        <div className="confirm-body">
          <h2 id="stop-title">녹음을 끝낼까요?</h2>
          <p>
            지금까지 <b>{fmtTime(elapsed, elapsed >= 3600)}</b> 녹음했어요.
            {autoProcess ? ' 끝내면 참고용 원본 스크립트를 따로 만들어요 (기록은 그대로).' : ' 끝내면 녹음 파일이 저장돼요.'}
          </p>
          {!paused && <p className="muted">쉬는 시간이라면 일시정지를 눌러 주세요. 다시 시작하면 그 사이가 구분선으로 남아요.</p>}
        </div>
        <div className="confirm-actions">
          {!paused && (
            <button className="btn" onClick={onPause}>
              <Pause size={14} />
              일시정지
            </button>
          )}
          <span className="spacer" />
          <button ref={keepRef} className="btn" onClick={onClose}>
            계속 녹음
          </button>
          <button className="btn btn-danger" onClick={onStop}>
            <Square size={12} fill="currentColor" />
            녹음 종료
          </button>
        </div>
      </div>
    </div>
  )
}
