import { Copy, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = async () => {
    await api.saveSettings({ vocabulary: vocab, auto_process: auto })
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
          </section>
          <section>
            <h3>엔진</h3>
            {health ? (
              <dl className="kv">
                <dt>실시간</dt><dd>{health.live.kind} · {health.live.model} <em className={`st-${health.live.state}`}>{health.live.state}</em></dd>
                <dt>원본</dt><dd>{health.final.kind} · {health.final.model}</dd>
                <dt>화자 구분</dt><dd>{health.diarizer === 'none' ? '사용 안 함 (HF_TOKEN 설정 필요)' : health.diarizer}</dd>
                <dt>데이터 위치</dt><dd><code>{health.data_dir}</code></dd>
              </dl>
            ) : (
              <p className="muted">서버에 연결되지 않았어요.</p>
            )}
            {health?.live.error && <p className="error-text">{health.live.error}</p>}
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
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>취소</button>
          <button className="btn btn-primary" onClick={save}>저장</button>
        </div>
      </div>
    </div>
  )
}
