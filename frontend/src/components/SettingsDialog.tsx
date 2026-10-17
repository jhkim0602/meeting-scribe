import { ChevronRight, Copy, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type StorageInfo } from '../lib/api'
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
  const [breakMin, setBreakMin] = useState(settings?.break_prompt_minutes ?? 3)
  const [storage, setStorage] = useState<StorageInfo | null>(null)

  useEffect(() => {
    api.storage().then(setStorage).catch(() => undefined)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = async () => {
    await api.saveSettings({ vocabulary: vocab, auto_process: auto, break_prompt_minutes: breakMin })
    await loadSettings()
    toast('설정을 저장했어요', 'success')
    onClose()
  }

  const mcpPath = `${health?.project_root || '<프로젝트 폴더>'}/scripts/mcp.sh`
  const desktop = JSON.stringify({ mcpServers: { 'meeting-scribe': { command: mcpPath } } }, null, 2)

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
              녹음을 마치면 자동으로 원본 만들기
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
              </>
            ) : (
              <p className="muted">계산하는 중…</p>
            )}
          </section>
          <section>
            <h3>Claude 연결 (MCP)</h3>
            <p className="muted">내가 구독한 Claude가 회의 기록·노트·첨삭을 읽고 요약을 이 앱에 저장할 수 있어요.</p>
            <p className="field-label">Claude Code</p>
            <CopyBlock text={`claude mcp add meeting-scribe -- ${mcpPath}`} />
            <p className="field-label">Claude Desktop · 설정 → 개발자 → 구성 편집</p>
            <CopyBlock text={desktop} />
            <p className="field-label">회의록 스킬 (Claude Code)</p>
            <CopyBlock text={`mkdir -p ~/.claude/skills && cp -r "${health?.project_root || '<프로젝트 폴더>'}/claude/skills/meeting-minutes" ~/.claude/skills/`} />
          </section>
          <details className="advanced">
            <summary><ChevronRight size={14} />고급</summary>
            {health ? (
              <dl className="kv">
                <dt>실시간</dt><dd>{health.live.kind} · {health.live.model} <em className={`st-${health.live.state}`}>{health.live.state}</em></dd>
                <dt>원본</dt><dd>{health.final.kind} · {health.final.model}</dd>
                <dt>화자 구분</dt><dd>{health.diarizer === 'none' ? '사용 안 함 (HF_TOKEN 설정 필요)' : health.diarizer}</dd>
                <dt>데이터 위치</dt><dd><code>{health.data_dir}</code></dd>
                <dt>잠자기 방지</dt><dd>{health.keep_awake ? '녹음 중 caffeinate' : '브라우저 화면 꺼짐 방지'}</dd>
              </dl>
            ) : (
              <p className="muted">서버에 연결되지 않았어요.</p>
            )}
            {health?.live.error && <p className="error-text">{health.live.error}</p>}
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
