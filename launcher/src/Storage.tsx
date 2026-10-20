import { useEffect, useState } from 'react'
import { call, deviceName } from './api'
import { shortPath } from './ui'

export type ModelOnDisk = { repo: string; label: string; bytes: number; in_use: boolean }
export type StorageInfo = {
  packages: number
  cache: number
  other: number
  support: string
  data_dir: string
  data: number
  app: string | null
  app_bytes: number
  models: { models: ModelOnDisk[]; models_bytes: number; meetings: number; hub: string } | null
}
type Removed = { models_freed: number; support_freed: number; meetings_trashed: boolean; meetings_error: string | null; data_dir: string; app: string | null }

export const fmtSize = (b: number) => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)}GB` : b >= 1024 ** 2 ? `${Math.round(b / 1024 ** 2)}MB` : `${Math.max(0, Math.round(b / 1024))}KB`)

/** 저장 공간·지우기: 모델마다 크기와 지우기, 설치 캐시 비우기, 완전히 지우기 */
export function StoragePanel({ notRecording, serverUrl }: { notRecording: () => Promise<boolean>; serverUrl: string }) {
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sure, setSure] = useState<string | null>(null) // 두 번 눌러 지우기: 처음 누르면 "정말 지울까요?"
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [uninstall, setUninstall] = useState(false)

  const load = () => call<StorageInfo>('storage_info').then(setInfo).catch((e) => setError(String(e)))
  useEffect(() => void load(), [])

  const removeModel = async (m: ModelOnDisk) => {
    if (sure !== m.repo) return setSure(m.repo)
    setSure(null)
    if (!(await notRecording())) return
    setBusy(m.repo)
    try {
      const r = await call<{ freed: number }>('remove_models', { repos: [m.repo] })
      setMsg(`${m.label}을(를) 지웠어요 · ${fmtSize(r.freed)} 비움${m.in_use ? '. 다음에 쓸 때 다시 받아요' : ''}`)
    } catch (e) {
      setMsg(String(e))
    }
    setBusy(null)
    await load()
  }
  const clearCache = async () => {
    setBusy('cache')
    const freed = await call<number>('clear_cache').catch(() => 0)
    setMsg(`설치 캐시를 비웠어요 · ${fmtSize(freed)}`)
    setBusy(null)
    await load()
  }

  if (error) return <p className="callout err s-panel">{error}</p>
  if (!info) return <div className="s-panel muted small">크기를 재는 중…</div>
  const models = info.models?.models ?? []
  return (
    <div className="s-panel storage">
      <div className="field-label">받아쓰기 모델 · {fmtSize(info.models?.models_bytes ?? 0)}</div>
      {models.length === 0 && <p className="muted small">받아 둔 모델이 없어요.</p>}
      <ul className="disk-list">
        {models.map((m) => (
          <li key={m.repo}>
            <div className="d-main">
              <b>{m.label}</b> {m.in_use && <span className="tag good">쓰는 중</span>}
              <span className="muted small">{m.repo}</span>
            </div>
            <span className="d-size">{fmtSize(m.bytes)}</span>
            <button className={`btn small ${sure === m.repo ? 'danger' : 'quiet'}`} disabled={busy !== null} onClick={() => removeModel(m)} onBlur={() => setSure(null)}>
              {busy === m.repo ? '지우는 중' : sure === m.repo ? (m.in_use ? '쓰는 중이에요. 그래도 지우기' : '정말 지우기') : '지우기'}
            </button>
          </li>
        ))}
      </ul>
      <ModelHelp hub={info.models?.hub ?? null} />
      <div className="field-label">앱이 설치한 것</div>
      <ul className="disk-list">
        <li>
          <div className="d-main"><b>파이썬·받아쓰기 패키지</b><span className="muted small">앱 전용으로 설치한 것</span></div>
          <span className="d-size">{fmtSize(info.packages)}</span>
          <span />
        </li>
        <li>
          <div className="d-main"><b>설치 캐시</b><span className="muted small">패키지를 받을 때만 써요. 비워도 괜찮아요</span></div>
          <span className="d-size">{fmtSize(info.cache)}</span>
          <button className="btn small quiet" disabled={busy !== null || info.cache === 0} onClick={clearCache}>{busy === 'cache' ? '비우는 중' : '비우기'}</button>
        </li>
        <li>
          <div className="d-main"><b>회의 기록</b><span className="muted small">{info.models ? `회의 ${info.models.meetings}개 · ` : ''}{shortPath(info.data_dir)}</span></div>
          <span className="d-size">{fmtSize(info.data)}</span>
          <button className="btn small quiet" onClick={() => call('open_folder', { path: info.data_dir })}>열기</button>
        </li>
      </ul>
      {msg && <p className="callout ok">{msg}</p>}
      <div className="row between danger-zone">
        <span className="muted small">앱과 받은 것을 모두 지워요. 회의 기록은 고를 수 있어요.</span>
        <button className="btn small danger-outline" onClick={async () => (await notRecording()) && setUninstall(true)}>회의 노트 완전히 지우기…</button>
      </div>
      {uninstall && <Uninstall info={info} serverUrl={serverUrl} onClose={() => setUninstall(false)} />}
    </div>
  )
}

/** 완전히 지우기: 무엇을 지울지 고르고 → 지우고 → 결과를 보여 준 뒤 앱을 지우며 끝낸다 */
function Uninstall({ info, serverUrl, onClose }: { info: StorageInfo; serverUrl: string; onClose: () => void }) {
  const [models, setModels] = useState(true)
  const [meetings, setMeetings] = useState(false)
  const [app, setApp] = useState(!!info.app)
  const [step, setStep] = useState<'ask' | 'running' | 'done'>('ask')
  const [result, setResult] = useState<Removed | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mac = deviceName === '맥'
  const modelBytes = info.models?.models_bytes ?? 0
  const always = info.packages + info.cache + info.other
  const total = always + (models ? modelBytes : 0) + (meetings ? info.data : 0) + (app ? info.app_bytes : 0)

  const run = async () => {
    setStep('running')
    try {
      setResult(await call<Removed>('uninstall_all', { models, meetings }))
      setStep('done')
    } catch (e) {
      setError(String(e))
      setStep('ask')
    }
  }
  const finish = () => call('finish_uninstall', { removeApp: app }).catch((e) => setError(String(e)))

  return (
    <div className="modal-bg" role="alertdialog" aria-labelledby="u-title">
      <div className="modal wide">
        {step === 'done' && result ? (
          <>
            <h3 id="u-title">다 지웠어요</h3>
            <p>{fmtSize(result.models_freed + result.support_freed)}를 비웠어요. Claude 데스크톱 연결과 로그인할 때 켜기도 껐어요.</p>
            {meetings && result.meetings_trashed && <p>회의 기록은 휴지통으로 옮겼어요. 휴지통을 비우기 전에는 되살릴 수 있어요.</p>}
            {meetings && !result.meetings_trashed && <p className="callout warn">회의 기록 폴더를 옮기지 못했어요{result.meetings_error ? ` (${result.meetings_error})` : ''}. 직접 지워 주세요: {result.data_dir}</p>}
            {!meetings && <p>회의 기록은 그대로 두었어요: {shortPath(result.data_dir)}. 다시 설치하면 이어서 써요.</p>}
            {app && <p>{mac ? '끝내기를 누르면 앱을 휴지통으로 옮기고 닫아요.' : '끝내기를 누르면 제거 도구가 열려요. 안내대로 마무리해 주세요.'}</p>}
            {error && <p className="callout err">{error}</p>}
            <div className="row end"><button className="btn primary" autoFocus onClick={finish}>끝내기</button></div>
          </>
        ) : (
          <>
            <h3 id="u-title">회의 노트를 지울까요?</h3>
            <p>항상 지우는 것: 파이썬·패키지·설치 캐시·설정·로그 ({fmtSize(always)}). Claude 데스크톱 연결과 로그인할 때 켜기도 꺼요.</p>
            <div className="callout warn">
              <b>회의 기록은 아래에서 고르지 않으면 지우지 않아요.</b> 전사 기록·회의록(HTML), 녹음 파일, 노트는 저장 폴더에 그대로 남아요. 그래도 혹시 모르니 지우기 전에 백업해 두세요.
              <div className="row"><button className="btn small" onClick={() => call('open_link', { url: `${serverUrl}/api/backup.zip` })}>전체 백업 받기 (zip)</button></div>
            </div>
            <label className="check"><input type="checkbox" checked={models} onChange={(e) => setModels(e.target.checked)} />받아쓰기 모델 {fmtSize(modelBytes)} <span className="muted">— 회의 노트 목록에 있는 모델만 지워요. 같은 모델을 다른 프로그램도 쓰고 있으면 그쪽은 다시 받아야 해요</span></label>
            <label className="check"><input type="checkbox" checked={meetings} onChange={(e) => setMeetings(e.target.checked)} />회의 기록 {fmtSize(info.data)}{info.models ? ` (${info.models.meetings}개)` : ''} <span className="muted">— 휴지통으로 옮겨요</span></label>
            {meetings && <p className="callout warn">녹음·기록·노트가 모두 휴지통으로 가요. 남겨 두려면 회의 노트의 설정 → 전체 백업 내려받기를 먼저 해 두세요.</p>}
            {info.app && <label className="check"><input type="checkbox" checked={app} onChange={(e) => setApp(e.target.checked)} />앱 자체 {fmtSize(info.app_bytes)} <span className="muted">— {mac ? '휴지통으로 옮겨요' : '윈도우 제거 도구로 지워요'}</span></label>}
            {error && <p className="callout err">{error}</p>}
            <p className="sum">비우는 양 약 <b>{fmtSize(total)}</b></p>
            <div className="row end">
              <button className="btn" onClick={onClose} disabled={step === 'running'}>취소</button>
              <button className="btn danger" onClick={run} disabled={step === 'running'}>{step === 'running' ? '지우는 중…' : '지우기'}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** 도움말: 앱 없이 모델을 직접 지우는 방법 */
function ModelHelp({ hub }: { hub: string | null }) {
  const mac = deviceName === '맥'
  const where = hub ?? (mac ? '~/.cache/huggingface/hub' : '%USERPROFILE%\\.cache\\huggingface\\hub')
  return (
    <details className="help">
      <summary>도움말: 모델을 직접 지우려면</summary>
      <ol>
        <li>받아쓰기 모델은 Hugging Face 공용 폴더에 있어요: <code>{where}</code></li>
        <li>{mac ? 'Finder → 이동 → 폴더로 이동(⇧⌘G)에 위 경로를 붙여 넣어요.' : '파일 탐색기 주소창에 위 경로를 붙여 넣어요.'}</li>
        <li><code>models--만든곳--모델이름</code> 폴더가 모델 하나예요. 예: <code>models--moona3k--mlx-qwen3-asr-0.6b-8bit</code>. 지울 폴더를 휴지통으로 옮기고 휴지통을 비워요.</li>
        <li>회의 노트가 받는 모델: Qwen3-ASR(<code>moona3k</code>, <code>Qwen</code>), Whisper(<code>mlx-community</code>, <code>Systran</code>, <code>mobiuslabsgmbh</code>), 화자 구분(<code>pyannote</code>). 다른 이름의 폴더는 다른 프로그램 것이니 두세요.</li>
      </ol>
      <p className="muted small">위 [지우기] 버튼을 쓰면 여러 모델이 같이 쓰는 큰 파일까지 알아서 정리해서 더 깔끔해요. 직접 지운 모델은 다음에 쓸 때 다시 받아요.</p>
    </details>
  )
}
