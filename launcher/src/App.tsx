import { useCallback, useEffect, useState } from 'react'
import { call, on, type AppState } from './api'
import { Home } from './Home'
import { Wizard } from './Wizard'

export function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [confirmQuit, setConfirmQuit] = useState(false)
  const [wizard, setWizard] = useState<boolean | null>(null)

  const refresh = useCallback(async () => {
    const s = await call<AppState>('get_state')
    setState(s)
    setWizard((w) => (w === null ? !s.config.setup_done : w))
  }, [])

  useEffect(() => {
    void refresh()
    let off: (() => void) | undefined
    let gone = false
    on('confirm-quit', () => setConfirmQuit(true)).then((f) => (gone ? f() : (off = f)))
    return () => {
      gone = true
      off?.()
    }
  }, [refresh])

  const quit = async () => {
    const r = await call<{ confirm: boolean }>('quit', { force: false })
    if (r?.confirm) setConfirmQuit(true)
  }

  if (!state || wizard === null) return <div className="loading" />
  return (
    <>
      {state.misplaced && (
        <p className="callout warn banner">
          회의 노트를 <b>응용 프로그램</b> 폴더로 옮긴 뒤 다시 열어 주세요. 디스크 이미지나 다운로드 폴더에서 바로 열면 로그인할 때 켜기가 동작하지 않아요.
        </p>
      )}
      {wizard ? (
        <Wizard state={state} onFinish={async () => { setWizard(false); await refresh() }} />
      ) : (
        <Home state={state} refresh={refresh} onQuit={quit} />
      )}
      {confirmQuit && (
        <div className="modal-bg" role="alertdialog" aria-labelledby="q-title">
          <div className="modal">
            <h3 id="q-title">녹음 중이에요</h3>
            <p>지금 끄면 녹음이 멈춰요. 그때까지 녹음한 것은 저장돼요.</p>
            <div className="row end">
              <button className="btn" autoFocus onClick={() => setConfirmQuit(false)}>계속 녹음</button>
              <button className="btn danger" onClick={() => call('quit', { force: true })}>그래도 끄기</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
