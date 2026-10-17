import { Mic, Plus, Users, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { addParticipant, setParticipantsLocal } from '../lib/speakers'
import { useStore } from '../lib/store'
import { Floating } from './Floating'

/** 참여자 더하기: 이름을 쓰고 Enter (쉼표로 여러 명), 지난 회의 참여자·목소리를 기억하는 사람은 눌러서 */
function AddPeople({ rect, meetingId, onClose }: { rect: DOMRect; meetingId: string; onClose: () => void }) {
  const participants = useStore((s) => s.meeting?.participants || [])
  const [draft, setDraft] = useState('')
  const [suggest, setSuggest] = useState<{ name: string; voice: boolean }[]>([])
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    api.suggestParticipants().then(setSuggest).catch(() => undefined)
  }, [])
  const names = useMemo(() => new Set(participants.map((p) => p.name)), [participants])
  const hints = suggest.filter((s) => !names.has(s.name) && (!draft.trim() || s.name.includes(draft.trim()))).slice(0, 8)
  const add = async (text: string) => {
    for (const name of text.split(/[,，]/)) await addParticipant(meetingId, name)
    setDraft('')
    input.current?.focus()
  }
  return (
    <Floating rect={rect} onClose={onClose} className="people-pop">
      <input
        ref={input}
        className="people-input"
        value={draft}
        autoFocus
        placeholder="이름을 쓰고 Enter (쉼표로 여러 명)"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.nativeEvent.isComposing && draft.trim()) add(draft)
          if (e.key === 'Escape') onClose()
        }}
      />
      {hints.length > 0 && (
        <div className="people-hints">
          {hints.map((h) => (
            <button key={h.name} className="picker-hint" onClick={() => add(h.name)} title={h.voice ? '목소리를 기억하고 있어서 회의 중에 바로 알아봐요' : '지난 회의 참여자'}>
              {h.voice && <Mic size={11} />}
              {h.name}
            </button>
          ))}
        </div>
      )}
      <p className="people-tip">미리 넣어 두면 회의 중에 문장의 화자를 눌러 고르거나 숫자키(1, 2, 3…)로 바로 정할 수 있어요.</p>
    </Floating>
  )
}

/** 페이지 위 속성 줄의 참여자 */
export function ParticipantsProp() {
  const meeting = useStore((s) => s.meeting)!
  const toast = useStore((s) => s.toast)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const parts = meeting.participants || []
  const open = (e: React.MouseEvent<HTMLElement>) => setRect(e.currentTarget.getBoundingClientRect())
  const remove = async (pid: string) => {
    const before = parts
    const r = await api.setParticipants(meeting.id, parts.filter((p) => p.id !== pid))
    setParticipantsLocal(r.participants)
    toast(`${before.find((p) => p.id === pid)?.name || ''} 님을 참여자에서 뺐어요`, 'info', {
      label: '되돌리기',
      run: async () => setParticipantsLocal((await api.setParticipants(meeting.id, before)).participants),
    })
  }
  return (
    <span className="prop prop-people">
      <Users size={14} />
      {parts.length === 0 && (
        <button className="people-empty" onClick={open} title="회의 참여자를 미리 넣어 두면 화자를 고르기 쉬워요">참여자 추가</button>
      )}
      {parts.map((p, i) => (
        <span key={p.id} className={`person sp-${(i % 8) + 1}`} title={i < 9 ? `숫자키 ${i + 1}` : undefined}>
          <span className="sp-dot" />
          {p.name}
          <button className="person-x" onClick={() => remove(p.id)} title="참여자에서 빼기"><X size={11} /></button>
        </span>
      ))}
      {parts.length > 0 && (
        <button className="person-add" onClick={open} title="참여자 추가"><Plus size={13} /></button>
      )}
      {rect && <AddPeople rect={rect} meetingId={meeting.id} onClose={() => setRect(null)} />}
    </span>
  )
}
