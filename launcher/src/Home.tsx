import { useEffect, useState } from 'react'
import { call, catalog, installer, on, recommend, type AppState, type ClaudeStatus, type DoctorItem, type HfCheck, type Machine, type ServerStatus } from './api'
import { Install } from './Install'
import { ExtLink, Logo, ModelSelect, StatusIcon, shortPath } from './ui'

const SERVER_LABEL: Record<ServerStatus['state'], string> = { running: '켜져 있어요', starting: '켜는 중', stopped: '꺼져 있어요', error: '문제가 있어요' }

type Panel = null | 'models' | 'speaker' | 'doctor'

/** 설치 뒤의 창: 회의 노트 열기, 설정 바꾸기, 점검, 끄기 */
export function Home({ state, refresh, onQuit }: { state: AppState; refresh: () => Promise<void>; onQuit: () => void }) {
  const [server, setServer] = useState(state.server)
  const [panel, setPanel] = useState<Panel>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [machine, setMachine] = useState<Machine | null>(null)
  const [live, setLive] = useState(state.config.live ?? '')
  const [final, setFinal] = useState(state.config.final ?? '')
  const [token, setToken] = useState(state.config.hf_token ?? '')
  const [hf, setHf] = useState<HfCheck | null>(null)
  const [claude, setClaude] = useState<ClaudeStatus | null>(null)
  const [doctor, setDoctor] = useState<DoctorItem[] | null>(null)
  const [free, setFree] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const open = (url: string) => void call('open_link', { url })
  const dataDir = state.config.data_dir || state.paths.default_data_dir

  useEffect(() => {
    let off: (() => void) | undefined
    let gone = false
    on<ServerStatus>('server', setServer).then((f) => (gone ? f() : (off = f)))
    const t = setInterval(() => call<AppState>('get_state').then((s) => setServer(s.server)), 4000)
    call<Machine>('machine_info').then(setMachine)
    installer<ClaudeStatus>('claude', 'status').then(setClaude).catch(() => undefined)
    return () => {
      gone = true
      off?.()
      clearInterval(t)
    }
  }, [])
  useEffect(() => setServer(state.server), [state.server])
  useEffect(() => {
    call<number | null>('free_space', { path: dataDir }).then(setFree)
  }, [dataDir])

  /** 서버를 끄는 일(모델·저장 위치 바꾸기, 다시 켜기)은 녹음 중이면 하지 않는다. 녹음이 끊기기 때문 */
  const notRecording = async () => {
    setNotice(null)
    if (!(await call<boolean>('is_recording').catch(() => false))) return true
    setNotice('녹음 중이라 지금은 바꿀 수 없어요. 녹음을 마친 뒤 다시 해 주세요.')
    return false
  }
  const restart = async () => {
    try {
      await call('server_stop')
    } catch (e) {
      setNotice(String(e))
      return
    }
    setServer((s) => ({ ...s, state: 'starting' }))
    await call('server_start')
  }
  const afterInstall = async () => {
    setInstalling(null)
    await refresh()
    await call('server_start')
  }

  if (installing !== null || state.needs_install) {
    if (installing === null)
      return (
        <div className="home">
          <Header server={server} version={state.version} />
          <div className="callout warn">
            앱이 새 버전으로 바뀌어 마무리 설치가 필요해요. 이미 받은 모델은 다시 받지 않아요.
            <div className="row"><button className="btn primary" onClick={() => setInstalling('업데이트를 마무리하는 중')}>지금 설치</button></div>
          </div>
        </div>
      )
    return (
      <div className="home">
        <Install title={installing} onDone={afterInstall} />
      </div>
    )
  }

  const liveLabel = catalog.live.find((c) => c.id === state.config.live)?.label ?? '기본값'
  const finalLabel = catalog.final.find((c) => c.id === state.config.final)?.label ?? '기본값'
  const rec = recommend(machine?.ram_gb ?? null)

  return (
    <div className="home">
      <Header server={server} version={state.version} />

      <div className="hero">
        <button className="btn primary big wide" onClick={() => call('open_app')} disabled={server.state === 'starting'}>
          회의 노트 열기
        </button>
        <p className="muted small">브라우저에서 열려요 · {server.url}</p>
        {server.state === 'error' && server.error && <pre className="callout err">{server.error}</pre>}
        {notice && <p className="callout warn">{notice}</p>}
        {server.external && <p className="muted small">다른 곳에서 켠 서버를 쓰고 있어요 (예: scripts/launchagent.sh). 설정을 바꾸려면 그쪽을 먼저 꺼 주세요.</p>}
      </div>

      <ul className="settings">
        <li>
          <div className="s-main">
            <div className="s-label">받아쓰기 모델</div>
            <div className="s-value">회의 중 {liveLabel} · 원본 스크립트 {finalLabel}</div>
          </div>
          <button className="btn small" onClick={() => setPanel(panel === 'models' ? null : 'models')}>바꾸기</button>
          {panel === 'models' && (
            <div className="s-panel">
              <div className="field-label">회의 중 받아쓰기</div>
              <ModelSelect role="live" value={live} onChange={setLive} ram={machine?.ram_gb ?? null} recommended={rec.live} />
              <div className="field-label">녹음 뒤 원본 스크립트</div>
              <ModelSelect role="final" value={final} onChange={setFinal} ram={machine?.ram_gb ?? null} recommended={rec.final} />
              <div className="row end">
                <button className="btn" onClick={() => setPanel(null)}>취소</button>
                <button className="btn primary" disabled={live === state.config.live && final === state.config.final} onClick={async () => {
                  if (!(await notRecording())) return
                  await call('save_config', { patch: { live, final } })
                  setPanel(null)
                  setInstalling('새 모델을 받는 중')
                }}>적용 (필요하면 받아요)</button>
              </div>
            </div>
          )}
        </li>

        <li>
          <div className="s-main">
            <div className="s-label">저장 위치</div>
            <div className="s-value" title={dataDir}>{shortPath(dataDir)}{free !== null && <span className="muted"> · 남은 공간 {free}GB</span>}</div>
          </div>
          <button className="btn small quiet" onClick={() => call('open_folder', { path: dataDir })}>열기</button>
          <button className="btn small" disabled={busy === 'folder'} onClick={async () => {
            const p = await call<string | null>('pick_folder', { start: dataDir })
            if (!p || p === dataDir || !(await notRecording())) return
            setBusy('folder')
            await call('save_config', { patch: { data_dir: p } })
            await refresh()
            await restart()
            setBusy(null)
          }}>바꾸기</button>
        </li>

        <li>
          <div className="s-main">
            <div className="s-label">화자 구분</div>
            <div className="s-value">{state.config.hf_token ? '토큰을 넣어 두었어요' : '꺼져 있어요 (토큰이 필요해요)'}</div>
          </div>
          <button className="btn small" onClick={() => setPanel(panel === 'speaker' ? null : 'speaker')}>설정</button>
          {panel === 'speaker' && (
            <div className="s-panel">
              <ol className="howto">
                <li><ExtLink href={catalog.speaker.terms_url} open={open}>화자 구분 모델 페이지</ExtLink>에서 약관에 동의해요.</li>
                <li><ExtLink href={catalog.speaker.token_url} open={open}>토큰 페이지</ExtLink>에서 Read 토큰을 만들어 붙여 넣어요.</li>
              </ol>
              <div className="token-row">
                <input type="password" placeholder="hf_로 시작하는 토큰" value={token} onChange={(e) => setToken(e.target.value)} spellCheck={false} autoComplete="off" />
                <button className="btn primary small" disabled={busy === 'hf'} onClick={async () => {
                  setBusy('hf')
                  await call('save_config', { patch: { hf_token: token.trim() || null } })
                  const r = token.trim() ? await installer<HfCheck>('check-hf') : null
                  setHf(r)
                  setBusy(null)
                  await refresh()
                }}>{busy === 'hf' ? '확인 중' : '확인'}</button>
              </div>
              {hf && <p className={`callout ${hf.ok ? 'ok' : 'warn'}`}>{hf.message}</p>}
              {hf?.ok && <div className="row end"><button className="btn primary" onClick={async () => { if (!(await notRecording())) return; setPanel(null); setInstalling('화자 구분 모델을 받는 중') }}>화자 구분 모델 받기</button></div>}
            </div>
          )}
        </li>

        <li>
          <div className="s-main">
            <div className="s-label">Claude 데스크톱</div>
            <div className="s-value">{claude ? (claude.connected ? '연결돼 있어요 · Claude에서 회의를 요약할 수 있어요' : '연결하지 않았어요') : '확인 중'}</div>
          </div>
          {claude && (
            <button className="btn small" disabled={busy === 'claude'} onClick={async () => {
              setBusy('claude')
              setClaude(await installer<ClaudeStatus>('claude', claude.connected ? 'disconnect' : 'connect'))
              setBusy(null)
            }}>{claude.connected ? '끊기' : '연결'}</button>
          )}
        </li>

        <li>
          <div className="s-main">
            <div className="s-label">로그인할 때 켜기</div>
            <div className="s-value">메뉴 막대에 조용히 켜 두고, 녹음할 때 바로 열어요</div>
          </div>
          <label className="switch">
            <input type="checkbox" checked={state.autostart} onChange={async (e) => { await call('autostart_set', { enabled: e.target.checked }); await refresh() }} />
            <span />
          </label>
        </li>

        <li>
          <div className="s-main">
            <div className="s-label">점검</div>
            <div className="s-value">패키지, 모델, 오디오 변환, 저장 공간을 확인해요</div>
          </div>
          <button className="btn small" disabled={busy === 'doctor'} onClick={async () => {
            setBusy('doctor')
            setPanel('doctor')
            setDoctor((await installer<{ items: DoctorItem[] }>('doctor')).items)
            setBusy(null)
          }}>{busy === 'doctor' ? '점검 중' : '점검하기'}</button>
          {panel === 'doctor' && doctor && (
            <ul className="s-panel checks">
              {doctor.map((c) => (
                <li key={c.id}><StatusIcon status={c.ok} /><b>{c.label}</b><span>{c.detail}</span></li>
              ))}
            </ul>
          )}
        </li>
      </ul>

      <footer className="row between">
        <button className="btn quiet" onClick={restart}>서버 다시 켜기</button>
        <button className="btn" onClick={onQuit}>끝내기</button>
      </footer>
    </div>
  )
}

function Header({ server, version }: { server: ServerStatus; version: string }) {
  return (
    <header className="home-head">
      <Logo size={36} />
      <div>
        <h1>회의 노트</h1>
        <span className="muted small">버전 {version}</span>
      </div>
      <span className={`pill s-${server.state}`}><i />{SERVER_LABEL[server.state]}</span>
    </header>
  )
}
