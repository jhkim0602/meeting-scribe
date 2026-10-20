import { ChevronRight, Copy, X } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import { api, type MemoryInfo, type StorageInfo } from '../lib/api'
import { useStore } from '../lib/store'

function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)}GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)}MB`
  return `${Math.max(1, Math.round(n / 1024))}KB`
}

function CopyBlock({ text }: { text: string }) {
  return (
    <div className="copy-block">
      <pre>{text}</pre>
      <button
        className="icon-btn"
        title="복사"
        onClick={() => navigator.clipboard.writeText(text).then(() => useStore.getState().toast('복사했어요', 'success'))}
      >
        <Copy size={14} />
      </button>
    </div>
  )
}

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const health = useStore((s) => s.health)
  const settings = useStore((s) => s.settings)
  const loadSettings = useStore((s) => s.loadSettings)
  const toast = useStore((s) => s.toast)
  const [vocab, setVocab] = useState(settings?.vocabulary || '')
  const [auto, setAuto] = useState(settings?.auto_process ?? true)
  const [onBattery, setOnBattery] = useState(settings?.auto_on_battery ?? false)
  const [breakMin, setBreakMin] = useState(settings?.break_prompt_minutes ?? 3)
  const [storage, setStorage] = useState<StorageInfo | null>(null)
  const [log, setLog] = useState<string | null>(null)
  const [mem, setMem] = useState<MemoryInfo | null>(null)
  const logRef = useRef<HTMLPreElement>(null)

  // 최근 줄이 보이게 맨 아래로
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  useEffect(() => {
    api.storage().then(setStorage).catch(() => undefined)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const loadMem = () => api.memory().then(setMem).catch((e) => toast(`메모리를 읽지 못했어요: ${e.message}`, 'error'))
  const gb = (n: number | null | undefined) => (n == null ? '?' : `${(n / 1024 ** 3).toFixed(1)}GB`)

  const loadLog = () =>
    api.serverLog()
      .then((r) => setLog(r.available ? r.lines.join('\n') || '(비어 있어요)' : '앱으로 설치했을 때만 볼 수 있어요 (터미널로 켰으면 그 터미널에 나와요)'))
      .catch((e) => setLog(`불러오지 못했어요: ${e.message}`))

  const save = async () => {
    await api.saveSettings({ vocabulary: vocab, auto_process: auto, auto_on_battery: onBattery, break_prompt_minutes: breakMin })
    await loadSettings()
    toast('설정을 저장했어요', 'success')
    onClose()
  }

  // 터미널로 설치했으면 scripts/mcp.sh, 앱으로 설치했으면 앱의 파이썬으로 바로 띄운다 (경로에 띄어쓰기가 있어 따옴표로 감싼다)
  const root = health?.project_root || '<프로젝트 폴더>'
  const mcp = health?.mcp ?? { command: `${root}/scripts/mcp.sh`, args: [], env: {} }
  const q = (s: string) => (/^[\w./:=@%+-]+$/.test(s) ? s : `"${s.replace(/(["\\$`])/g, '\\$1')}"`)
  const codeCmd = ['claude mcp add meeting-scribe', ...Object.entries(mcp.env).map(([k, v]) => `-e ${q(`${k}=${v}`)}`), '--', q(mcp.command), ...mcp.args.map(q)].join(' ')
  const desktop = JSON.stringify({ mcpServers: { 'meeting-scribe': health?.mcp ?? { command: mcp.command } } }, null, 2)

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label="설정">
        <div className="modal-head">
          <h2>설정</h2>
          <button className="icon-btn" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-body">
          <section>
            <h3>받아쓰기</h3>
            <label className="field">
              <span>자주 나오는 용어·이름</span>
              <textarea className="input" rows={3} value={vocab} placeholder="예: 김민지 박준호 OKR 리텐션 Qwen" onChange={(e) => setVocab(e.target.value)} />
              <em>띄어쓰기로 구분해 적으면 받아쓰기가 이 단어들을 더 잘 알아들어요.</em>
            </label>
            <label className="check">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              원본 스크립트(참고용) 저절로 만들기 — 녹음하지 않을 때만. 끄면 ⋯ 메뉴에서 만들 때만 만들어요
            </label>
            <label className="check sub">
              <input type="checkbox" checked={onBattery} disabled={!auto} onChange={(e) => setOnBattery(e.target.checked)} />
              배터리로 쓸 때도 만들기 — 끄면 전원을 연결할 때까지 기다려요. 1시간 녹음에 15분쯤 칩을 바쁘게 써서 배터리가 더 줄어요
            </label>
            <label className="field-inline">
              <span>조용한 시간이 이어지면 쉬는 시간인지 묻기</span>
              <select className="input select" value={breakMin} onChange={(e) => setBreakMin(Number(e.target.value))}>
                <option value={0}>묻지 않음</option>
                {[2, 3, 5, 10, 15].map((m) => <option key={m} value={m}>{m}분</option>)}
              </select>
            </label>
          </section>
          <section>
            <h3>저장 공간</h3>
            {storage ? (
              <>
                <div className="storage-bar" aria-hidden>
                  <span className="s-audio" style={{ flex: storage.audio_bytes }} />
                  <span className="s-other" style={{ flex: storage.other_bytes }} />
                  <span className="s-free" style={{ flex: storage.free_bytes }} />
                </div>
                <p className="storage-text">
                  회의 {storage.meetings}개 · 녹음 {fmtBytes(storage.audio_bytes)} · 기록과 노트 {fmtBytes(storage.other_bytes)}
                  <span className="muted"> · 남은 공간 {fmtBytes(storage.free_bytes)} (약 {Math.floor(storage.free_s / 3600)}시간 더 녹음할 수 있어요)</span>
                </p>
                <p className="storage-help muted">
                  원본 스크립트까지 만든 회의는 6시간 뒤 녹음 파일을 m4a로 줄여 1시간에 약 29MB만 써요.
                  <a className="storage-backup" href="/api/backup.zip" download>전체 백업 내려받기</a>
                </p>
              </>
            ) : (
              <p className="muted">계산하는 중…</p>
            )}
          </section>
          <section>
            <h3>Claude 연결 (MCP)</h3>
            <p className="muted">내가 구독한 Claude가 회의 기록·노트·메모·댓글을 읽고 요약을 이 앱에 저장할 수 있어요.</p>
            <p className="field-label">Claude Code</p>
            <CopyBlock text={codeCmd} />
            <p className="field-label">Claude Desktop · 설정 → 개발자 → 구성 편집</p>
            {health?.app_install && <p className="muted mcp-hint">메뉴 막대의 회의 노트 → 설치·설정 창 → Claude 데스크톱 "연결"을 누르면 저절로 넣어 줘요.</p>}
            <CopyBlock text={desktop} />
            <p className="field-label">회의록 스킬 (Claude Code)</p>
            <CopyBlock text={`mkdir -p ~/.claude/skills && cp -r ${q(`${root}/claude/skills/meeting-minutes`)} ~/.claude/skills/`} />
          </section>
          <details className="advanced">
            <summary><ChevronRight size={14} />고급</summary>
            {health ? (
              <dl className="kv">
                <dt>실시간</dt><dd>{health.live.kind} · {health.live.model} <em className={`st-${health.live.state}`}>{health.live.state}</em></dd>
                <dt>원본 스크립트</dt><dd>{health.final.kind} · {health.final.model}</dd>
                {health.app_install && <><dt /><dd className="muted">모델은 메뉴 막대의 회의 노트 → 설치·설정 창 → 받아쓰기 모델에서 바꿔요</dd></>}
                <dt>화자 구분</dt><dd>{health.diarizer === 'none' ? '사용 안 함 (HF_TOKEN 설정 필요)' : health.diarizer}</dd>
                <dt>데이터 위치</dt><dd><code>{health.data_dir}</code></dd>
                <dt>잠자기 방지</dt><dd>{health.keep_awake ? '녹음 중 잠들지 않게 함' : '브라우저 화면 꺼짐 방지'}</dd>
              </dl>
            ) : (
              <p className="muted">서버에 연결되지 않았어요.</p>
            )}
            {health?.live.error && <p className="error-text">{health.live.error}</p>}
            <p className="field-label">
              메모리 <button className="btn btn-sm" onClick={loadMem}>{mem === null ? '불러오기' : '새로고침'}</button>
            </p>
            {mem && (
              <dl className="kv">
                <dt>이 컴퓨터</dt>
                <dd>
                  전체 {gb(mem.system.total)} · 남은 {gb(mem.system.available)}
                  {mem.system.swap_used ? <span className={mem.system.swap_used > 4 * 1024 ** 3 ? 'error-text' : 'muted'}> · 디스크로 밀려남 {gb(mem.system.swap_used)}</span> : null}
                </dd>
                <dt>회의 노트 서버</dt><dd>{gb(mem.server)}</dd>
                {mem.jobs.map((j) => <Fragment key={j.meeting_id}><dt>원본 스크립트 작업</dt><dd>{gb(j.bytes)}</dd></Fragment>)}
                <dt>실시간 모델</dt><dd>{mem.live_model.model} <em className={`st-${mem.live_model.state}`}>{mem.live_model.state}</em></dd>
                <dt>화자 모델</dt>
                <dd>
                  {mem.speaker_model.loaded ? '올라와 있음' : '안 올라옴'}
                  {mem.speaker_model.failures > 0 && <span className="error-text"> · 실패 {mem.speaker_model.failures}회 (다시 시도 중)</span>}
                </dd>
                {mem.recording.map((r) => (
                  <Fragment key={r.meeting_id}><dt>녹음 중 밀린 소리</dt><dd className={r.backlog_s > 30 ? 'error-text' : undefined}>{r.backlog_s}초</dd></Fragment>
                ))}
              </dl>
            )}
            <p className="field-label">
              서버 로그 <button className="btn btn-sm" onClick={loadLog}>{log === null ? '불러오기' : '새로고침'}</button>
            </p>
            {log !== null && <pre className="log-view" ref={logRef}>{log}</pre>}
          </details>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>취소</button>
          <button className="btn btn-primary" onClick={save}>저장</button>
        </div>
      </div>
    </div>
  )
}
