import { Folder, Mic, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { startRecording } from '../hooks/recorder'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { useMicChoice } from './Dock'

type Hint = { name: string; voice: boolean; org?: string; role?: string }

/**
 * 새 회의 준비: 녹음을 시작하기 전에 제목·참석자·폴더·마이크를 정한다.
 * [녹음 시작]은 만들고 바로 녹음, [만들기만]은 미리 준비해 두고 나중에 녹음.
 */
export function NewMeetingDialog({ onClose }: { onClose: () => void }) {
  const groups = useStore((s) => s.groups)
  const recordingAny = useStore((s) => s.rec.status !== 'idle')
  const toast = useStore((s) => s.toast)
  const { mics, mic, choose } = useMicChoice()
  const [title, setTitle] = useState('')
  const [people, setPeople] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [group, setGroup] = useState<string>(() => {
    try {
      return localStorage.getItem('newMeetingFolder') || ''
    } catch {
      return ''
    }
  })
  const [hints, setHints] = useState<Hint[]>([])
  const [busy, setBusy] = useState(false)
  const titleRef = useRef<HTMLInputElement>(null)
  const peopleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    titleRef.current?.focus()
    api.suggestParticipants().then(setHints).catch(() => undefined)
  }, [])

  // 폴더: 위에서부터 들여 써서 고르기 (지운 폴더를 기억하고 있으면 폴더 밖으로)
  const folders = useMemo(() => {
    const out: { id: string; name: string; d: number }[] = []
    const walk = (p: string | null, d: number) =>
      groups.filter((g) => (g.parent_id || null) === p).forEach((g) => (out.push({ id: g.id, name: g.name, d }), walk(g.id, d + 1)))
    walk(null, 0)
    return out
  }, [groups])
  const groupId = folders.some((f) => f.id === group) ? group : ''

  const addPeople = (text: string) => {
    const names = text.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    setPeople((p) => [...p, ...names.filter((n) => !p.includes(n))])
    setDraft('')
  }
  const shown = hints.filter((h) => !people.includes(h.name) && (!draft.trim() || h.name.includes(draft.trim()))).slice(0, 10)

  const create = async (record: boolean) => {
    if (busy) return
    if (record && recordingAny) {
      toast('다른 회의를 녹음 중이에요. 먼저 종료해 주세요.', 'error')
      return
    }
    setBusy(true)
    const st = useStore.getState()
    try {
      const all = draft.trim() ? [...people, ...draft.split(/[,，]/).map((s) => s.trim()).filter(Boolean)] : people
      const m = await api.createMeeting(title.trim())
      if (all.length) await api.setParticipants(m.id, [...new Set(all)].map((name) => ({ name })))
      if (groupId) await api.moveMeeting(m.id, groupId)
      try {
        localStorage.setItem('newMeetingFolder', groupId)
      } catch {
        /* 무시 */
      }
      await st.loadMeetings()
      location.hash = `#/m/${m.id}`
      await st.select(m.id)
      onClose()
      if (record) await startRecording(m.id, mic || undefined)
      else st.toast('회의를 만들어 두었어요. 준비되면 아래 빨간 버튼으로 녹음을 시작하세요.', 'success')
    } catch (e) {
      toast((e as Error).message, 'error')
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div
        className="modal new-meeting"
        role="dialog"
        aria-label="새 회의"
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !busy) onClose()
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) create(true)
        }}
      >
        <div className="modal-head">
          <h2>새 회의</h2>
          <button className="icon-btn" onClick={onClose} disabled={busy}><X size={16} /></button>
        </div>
        <div className="modal-body">
          <label className="field">
            <span>제목</span>
            <input
              ref={titleRef}
              className="input"
              value={title}
              placeholder="예: 주간 회의 (비워 두면 '제목 없는 회의')"
              maxLength={120}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing && !e.metaKey && !e.ctrlKey) {
                  e.preventDefault()
                  peopleRef.current?.focus()
                }
              }}
            />
          </label>

          <div className="field">
            <span>참석자 <em className="muted">미리 넣어 두면 회의 중에 화자를 바로 고를 수 있어요 (숫자키 1, 2, 3…)</em></span>
            <div className="chip-input" onClick={() => peopleRef.current?.focus()}>
              {people.map((n, i) => (
                <span key={n} className={`chip sp-${(i % 8) + 1}`}>
                  <span className="sp-dot" />{n}
                  <button className="chip-x" onClick={() => setPeople((p) => p.filter((x) => x !== n))} title="빼기"><X size={11} /></button>
                </span>
              ))}
              <input
                ref={peopleRef}
                value={draft}
                placeholder={people.length ? '' : '이름을 쓰고 Enter (쉼표로 여러 명)'}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing && !e.metaKey && !e.ctrlKey) {
                    e.preventDefault()
                    if (draft.trim()) addPeople(draft)
                  }
                  if (e.key === 'Backspace' && !draft && people.length) setPeople((p) => p.slice(0, -1))
                }}
              />
            </div>
            {shown.length > 0 && (
              <div className="people-hints">
                {shown.map((h) => (
                  <button
                    key={h.name}
                    className="picker-hint"
                    onClick={() => addPeople(h.name)}
                    title={[[h.org, h.role].filter(Boolean).join(' · '), h.voice ? '목소리를 기억하고 있어서 회의 중에 바로 알아봐요' : '지난 회의 참여자'].filter(Boolean).join('\n')}
                  >
                    {h.voice && <Mic size={11} />}
                    {h.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="field-row">
            <label className="field">
              <span><Folder size={12} /> 폴더</span>
              <select className="input select" value={groupId} onChange={(e) => setGroup(e.target.value)}>
                <option value="">폴더 밖</option>
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>{'  '.repeat(f.d)}{f.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span><Mic size={12} /> 마이크</span>
              <select className="input select" value={mic} onChange={(e) => choose(e.target.value)}>
                <option value="">기본 마이크</option>
                {mics.filter((d) => d.deviceId && d.deviceId !== 'default').map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>{d.label || `마이크 ${i + 1}`}</option>
                ))}
              </select>
            </label>
          </div>
          <p className="muted small">제목·참석자는 녹음하는 중에도, 끝난 뒤에도 바꿀 수 있어요. 참석자의 소속·직무는 지난 회의에서 이어받아요.</p>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={() => create(false)} disabled={busy} title="미리 만들어 두고 나중에 녹음해요">만들기만</button>
          <button className="btn btn-primary rec-start" onClick={() => create(true)} disabled={busy} title="⌘/Ctrl + Enter">
            <span className="rec-dot" />{busy ? '준비하는 중…' : '녹음 시작'}
          </button>
        </div>
      </div>
    </div>
  )
}
