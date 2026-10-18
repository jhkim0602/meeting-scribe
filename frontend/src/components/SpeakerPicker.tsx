import { Check, Mic, Plus, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Participant } from '../lib/api'
import { setVoiceScope, voiceScope } from '../lib/speakers'
import { Floating } from './Floating'

interface Props {
  rect: DOMRect
  participants: Participant[]
  current: string | null
  onPick: (pid: string | null) => void
  onAdd: (name: string) => Promise<Participant | null>
  onClose: () => void
  /** 문장 하나만 고를 때 (같은 목소리 옵션 숨김) */
  single?: boolean
}

/** 참여자 고르기: 숫자키로 바로, 없으면 이름을 써서 새 참여자 */
export function SpeakerPicker({ rect, participants, current, onPick, onAdd, onClose, single }: Props) {
  const [draft, setDraft] = useState('')
  const [scope, setScope] = useState(voiceScope())
  const [suggest, setSuggest] = useState<{ name: string; voice: boolean }[]>([])
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.suggestParticipants().then(setSuggest).catch(() => undefined)
  }, [])

  // 숫자키 1~9로 바로 고르기 (입력창에 쓰는 중이 아닐 때)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.activeElement === input.current || e.metaKey || e.ctrlKey || e.altKey) return
      const n = /^Digit([1-9])$/.exec(e.code)
      if (n && participants[Number(n[1]) - 1]) {
        e.preventDefault()
        e.stopPropagation()
        onPick(participants[Number(n[1]) - 1].id)
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [participants, onPick])

  const names = useMemo(() => new Set(participants.map((p) => p.name)), [participants])
  const hints = suggest.filter((s) => !names.has(s.name) && (!draft.trim() || s.name.includes(draft.trim()))).slice(0, 5)

  const add = async (name: string) => {
    const p = await onAdd(name)
    setDraft('')
    if (p) onPick(p.id)
  }

  return (
    <Floating rect={rect} onClose={onClose} className="speaker-picker">
      {participants.length > 0 && <p className="picker-label">{single ? '이 문장은 누가 말했나요?' : '누가 말했나요?'}</p>}
      {participants.map((p, i) => (
        <button key={p.id} className={`menu-item picker-item sp-${(i % 8) + 1}`} onClick={() => onPick(p.id)}>
          <span className="sp-dot" />
          <span className="picker-name">{p.name}</span>
          {current === p.id && <Check size={14} className="picker-check" />}
          {i < 9 && <kbd>{i + 1}</kbd>}
        </button>
      ))}
      <div className="picker-add">
        <Plus size={14} />
        <input
          ref={input}
          value={draft}
          autoFocus={participants.length === 0}
          placeholder={participants.length ? '새 참여자 이름' : '참여자 이름을 쓰고 Enter'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing && draft.trim()) add(draft)
            if (e.key === 'Escape') onClose()
          }}
        />
      </div>
      {hints.length > 0 && (
        <div className="picker-hints">
          {hints.map((h) => (
            <button key={h.name} className="picker-hint" onClick={() => add(h.name)} title={h.voice ? '목소리를 기억하고 있어요' : '지난 회의 참여자'}>
              {h.voice && <Mic size={11} />}
              {h.name}
            </button>
          ))}
        </div>
      )}
      <div className="menu-sep" />
      {!single && (
        <label className="picker-scope">
          <input type="checkbox" checked={scope} onChange={(e) => { setScope(e.target.checked); setVoiceScope(e.target.checked) }} />
          같은 목소리 문장도 함께
        </label>
      )}
      {current && (
        <button className="menu-item picker-clear" onClick={() => onPick(null)}>
          <X size={14} />화자 지우기
        </button>
      )}
      {participants.length > 0 && <p className="picker-tip">문장에 마우스를 올리고 숫자키를 누르면 바로 정해져요. Shift+숫자는 그 문장만.</p>}
    </Floating>
  )
}
