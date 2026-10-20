import { useEffect, useState } from 'react'
import { call, catalog, deviceName, downloadGb, installer, recommend, thisOs, type AppState, type ClaudeStatus, type DoctorItem, type HfCheck, type Machine } from './api'
import { Install } from './Install'
import { ExtLink, Field, Logo, ModelSelect, StatusIcon, shortPath } from './ui'

type Draft = { live: string; final: string; data_dir: string; hf_token: string; autostart: boolean; claude: boolean }
type Result = { label: string; ok: boolean | null; detail: string }

const NAMES = ['시작', '모델', '저장 위치', '설치', '완료']

/** 처음 설치: 이 맥 확인 → 모델 → 저장 위치·화자 구분 → 설치 → 완료 */
export function Wizard({ state, onFinish }: { state: AppState; onFinish: () => void }) {
  const [step, setStep] = useState(0)
  const [machine, setMachine] = useState<Machine | null>(null)
  const [d, setD] = useState<Draft>(() => ({
    live: state.config.live ?? '', final: state.config.final ?? '', data_dir: state.config.data_dir ?? state.paths.default_data_dir,
    hf_token: state.config.hf_token ?? '', autostart: state.autostart, claude: true,
  }))
  const [free, setFree] = useState<number | null>(null)
  const [results, setResults] = useState<Result[] | null>(null)
  const [checks, setChecks] = useState<DoctorItem[] | null>(null)
  const [showToken, setShowToken] = useState(false)
  const open = (url: string) => void call('open_link', { url })
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }))

  useEffect(() => {
    call<Machine>('machine_info').then((m) => {
      setMachine(m)
      const rec = recommend(m.ram_gb)
      setD((x) => ({ ...x, live: x.live || rec.live, final: x.final || rec.final }))
      setFree(m.free_gb)
    })
  }, [])
  useEffect(() => {
    call<number | null>('free_space', { path: d.data_dir }).then(setFree)
  }, [d.data_dir])

  const rec = recommend(machine?.ram_gb ?? null)
  const need = downloadGb(d.live, d.final, !!d.hf_token.trim()) + 1.2 // 패키지 약 1GB + 여유
  const lowSpace = free !== null && free < need + 2

  const startInstall = async () => {
    await call('save_config', { patch: { live: d.live, final: d.final, data_dir: d.data_dir, hf_token: d.hf_token.trim() || null, open_browser: true } })
    setStep(3)
  }

  const finish = async (doctor: DoctorItem[] | null) => {
    setChecks(doctor)
    setStep(4)
    const out: Result[] = [{ label: '받아쓰기', ok: true, detail: `${catalog.live.find((c) => c.id === d.live)?.label} · 원본 스크립트 ${catalog.final.find((c) => c.id === d.final)?.label}` }]
    if (d.hf_token.trim()) {
      const hf = await installer<HfCheck>('check-hf').catch((e) => ({ ok: false, message: String(e) }) as HfCheck)
      out.push({ label: '화자 구분', ok: hf.ok ? true : null, detail: hf.message })
    } else out.push({ label: '화자 구분', ok: null, detail: '토큰 없이 설치했어요. 설정에서 언제든 켤 수 있어요.' })
    if (d.claude) {
      const c = await installer<ClaudeStatus>('claude', 'connect').catch((e) => ({ ok: false, message: String(e) }) as ClaudeStatus)
      out.push({ label: 'Claude 데스크톱', ok: c.ok && c.connected ? true : null, detail: c.message })
    }
    if (d.autostart) {
      const on = await call<boolean>('autostart_set', { enabled: true }).catch(() => false)
      out.push({ label: '로그인할 때 켜기', ok: on ? true : null, detail: on ? '로그인하면 메뉴 막대에 저절로 켜져요' : '켜지 못했어요. 설정에서 다시 해 보세요.' })
    }
    setResults(out)
  }

  return (
    <div className="wizard">
      <nav className="dots" aria-label="설치 단계">
        {NAMES.map((n, i) => (
          <span key={n} className={i === step ? 'on' : i < step ? 'past' : ''}>
            <i />
            {n}
          </span>
        ))}
      </nav>

      {step === 0 && (
        <section className="page welcome">
          <Logo size={64} />
          <h1>회의 노트 설치</h1>
          <p className="lead">회의를 들으며 받아 적고, 끝나면 문서로 정리하는 앱이에요.<br />녹음과 받아쓰기는 모두 이 {deviceName} 안에서만 돌아가요.</p>
          {machine && (
            <dl className="machine">
              <div><dt>{deviceName}</dt><dd>{machine.chip} · {machine.os}</dd></div>
              <div><dt>메모리</dt><dd>{machine.ram_gb ?? '?'}GB</dd></div>
              <div><dt>남은 공간</dt><dd>{machine.free_gb ?? '?'}GB</dd></div>
            </dl>
          )}
          {machine && thisOs === 'windows' && <p className="callout">윈도우에서는 받아쓰기가 CPU로 돌아가서 맥보다 조금 느려요 (실시간 Whisper small, 녹음 후 정리 Whisper large-v3 turbo). 회의 중에는 전원을 연결해 두면 좋아요.</p>}
          {machine && thisOs === 'macos' && !machine.apple_silicon && <p className="callout warn">이 앱은 Apple Silicon(M1 이상) 맥에 맞춰져 있어요. 이 기기에서는 받아쓰기가 느리거나 동작하지 않을 수 있어요.</p>}
          <p className="muted small">설치는 처음 한 번, 10분 안팎이에요 (인터넷 속도에 따라).<br />필요한 것은 앱 전용 폴더에만 설치해요.</p>
          <div className="row end">
            <button className="btn primary big" onClick={() => setStep(1)} disabled={!machine}>시작하기</button>
          </div>
        </section>
      )}

      {step === 1 && (
        <section className="page">
          <h2>받아쓰기 모델</h2>
          <p className="lead">이 {deviceName}{machine?.ram_gb ? `(메모리 ${machine.ram_gb}GB)` : ''}에 맞게 골라 두었어요. 나중에 설정에서 바꿀 수 있어요.</p>
          <Field label="회의 중 받아쓰기" hint="말하는 동안 글자로 바꿔요.">
            <ModelSelect role="live" value={d.live} onChange={(live) => set({ live })} ram={machine?.ram_gb ?? null} recommended={rec.live} />
          </Field>
          <Field label="녹음 뒤 원본 스크립트 (참고용)" hint="녹음을 마친 뒤 더 큰 모델로 다시 받아 적어 둬요. 전원이 연결돼 있고 녹음하지 않을 때만 만들어요. 올린 녹음 파일을 받아 적을 때도 써요.">
            <ModelSelect role="final" value={d.final} onChange={(final) => set({ final })} ram={machine?.ram_gb ?? null} recommended={rec.final} />
          </Field>
          <p className="sum">받을 모델 약 <b>{downloadGb(d.live, d.final)}GB</b> · 남은 공간 {free ?? '?'}GB</p>
          <div className="row between">
            <button className="btn" onClick={() => setStep(0)}>이전</button>
            <button className="btn primary" onClick={() => setStep(2)}>다음</button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="page">
          <h2>저장 위치와 화자 구분</h2>
          <Field label="녹음과 기록을 저장할 폴더" hint={free !== null ? `남은 공간 ${free}GB · 녹음 1시간에 약 115MB` : undefined}>
            <div className="path-row">
              <code title={d.data_dir}>{shortPath(d.data_dir)}</code>
              <button className="btn small" onClick={async () => { const p = await call<string | null>('pick_folder', { start: d.data_dir }); if (p) set({ data_dir: p }) }}>바꾸기</button>
            </div>
          </Field>
          {lowSpace && <p className="callout warn">남은 공간이 부족할 수 있어요. 설치에 약 {need.toFixed(1)}GB가 필요해요.</p>}

          <div className="group">
            <div className="group-title">화자 구분 <span className="muted">(선택)</span></div>
            <p className="small">누가 말했는지 나누려면 Hugging Face 토큰이 한 번 필요해요. 없어도 받아쓰기는 돼요.</p>
            <ol className="howto">
              <li><ExtLink href={catalog.speaker.terms_url} open={open}>화자 구분 모델 페이지</ExtLink>에서 로그인하고 약관에 동의해요.</li>
              <li><ExtLink href={catalog.speaker.token_url} open={open}>토큰 페이지</ExtLink>에서 Read 토큰을 만들어 복사해요.</li>
              <li>아래에 붙여 넣어요. 설치하면서 맞는지 확인해요.</li>
            </ol>
            <div className="token-row">
              <input type={showToken ? 'text' : 'password'} placeholder="hf_로 시작하는 토큰" value={d.hf_token} onChange={(e) => set({ hf_token: e.target.value })} spellCheck={false} autoComplete="off" />
              <button className="btn small quiet" onClick={() => setShowToken(!showToken)}>{showToken ? '가리기' : '보기'}</button>
            </div>
          </div>

          <div className="group">
            <label className="check"><input type="checkbox" checked={d.claude} onChange={(e) => set({ claude: e.target.checked })} />Claude 데스크톱에 연결 <span className="muted">— 내 Claude 구독으로 회의를 요약해요</span></label>
            <label className="check"><input type="checkbox" checked={d.autostart} onChange={(e) => set({ autostart: e.target.checked })} />로그인할 때 저절로 켜기 <span className="muted">— 메뉴 막대에 조용히 켜져요</span></label>
          </div>
          <div className="row between">
            <button className="btn" onClick={() => setStep(1)}>이전</button>
            <button className="btn primary" onClick={startInstall}>설치하기 · 약 {need.toFixed(1)}GB</button>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="page">
          <Install onDone={finish} />
        </section>
      )}

      {step === 4 && (
        <section className="page done">
          <div className="done-head">
            <StatusIcon status="done" />
            <h2>다 됐어요</h2>
          </div>
          <ul className="results">
            {(results ?? [{ label: '마무리하는 중', ok: null, detail: 'Claude 연결, 화자 구분 토큰을 확인하고 있어요' }]).map((r) => (
              <li key={r.label}>
                {results ? <StatusIcon status={r.ok} /> : <span className="spin" />}
                <div><b>{r.label}</b><span>{r.detail}</span></div>
              </li>
            ))}
          </ul>
          {checks && checks.some((c) => c.ok === false) && (
            <p className="callout warn">점검에서 확인할 것이 있어요: {checks.filter((c) => c.ok === false).map((c) => `${c.label}(${c.detail})`).join(', ')}</p>
          )}
          <p className="muted small">창을 닫아도 회의 노트는 메뉴 막대에 켜져 있어요. 메뉴 막대의 아이콘에서 언제든 다시 열 수 있어요.</p>
          <div className="row end">
            <button className="btn primary big" disabled={!results} onClick={async () => { await call('open_app'); onFinish() }}>회의 노트 열기</button>
          </div>
        </section>
      )}
    </div>
  )
}
