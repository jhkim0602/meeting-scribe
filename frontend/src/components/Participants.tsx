import { ChevronDown, Mic, Plus, Users, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Participant } from '../lib/api'
import { PEOPLE_DETAILS, setPref, usePref } from '../lib/prefs'
import { addParticipant, setParticipantsLocal } from '../lib/speakers'
import { useStore } from '../lib/store'
import { Floating } from './Floating'

/** 참여자 더하기: 이름을 쓰고 Enter (쉼표로 여러 명), 지난 회의 참여자·목소리를 기억하는 사람은 눌러서 */
function AddPeople({ rect, meetingId, onClose }: { rect: DOMRect; meetingId: string; onClose: () => void }) {
  const participants = useStore((s) => s.meeting?.participants || [])
  const [draft, setDraft] = useState('')
  const [suggest, setSuggest] = useState<{ name: string; voice: boolean; org?: string; role?: string }[]>([])
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
            <button
              key={h.name}
              className="picker-hint"
              onClick={() => add(h.name)}
              title={[[h.org, h.role].filter(Boolean).join(' · '), h.voice ? '목소리를 기억하고 있어서 회의 중에 바로 알아봐요' : '지난 회의 참여자'].filter(Boolean).join('\n')}
            >
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

/** 세부 정보 칸을 펼치고 그 사람의 첫 칸에 커서를 둔다 */
function openDetails(pid?: string) {
  setPref(PEOPLE_DETAILS, true)
  if (pid) requestAnimationFrame(() => document.querySelector<HTMLInputElement>(`.pd-row[data-pid="${pid}"] input`)?.focus())
}

/** 페이지 위 속성 줄의 참여자 */
export function ParticipantsProp() {
  const meeting = useStore((s) => s.meeting)!
  const toast = useStore((s) => s.toast)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [details, setDetails] = usePref(PEOPLE_DETAILS, false)
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
        <span
          key={p.id}
          className={`person sp-${(i % 8) + 1}`}
          title={[[p.org, p.role].filter(Boolean).join(' · '), i < 9 ? `숫자키 ${i + 1}` : ''].filter(Boolean).join('\n') || undefined}
          onClick={() => openDetails(p.id)}
        >
          <span className="sp-dot" />
          {p.name}
          <button className="person-x" onClick={(e) => { e.stopPropagation(); remove(p.id) }} title="참여자에서 빼기"><X size={11} /></button>
        </span>
      ))}
      {parts.length > 0 && (
        <button className="person-add" onClick={open} title="참여자 추가"><Plus size={13} /></button>
      )}
      {parts.length > 0 && (
        <button className={`person-add people-toggle ${details ? 'on' : ''}`} onClick={() => setDetails(!details)} title="소속·직무·메모">
          세부 정보<ChevronDown size={13} />
        </button>
      )}
      {rect && <AddPeople rect={rect} meetingId={meeting.id} onClose={() => setRect(null)} />}
    </span>
  )
}

type Field = 'org' | 'role' | 'note'
const FIELDS: { key: Field; label: string; hint: string; max: number }[] = [
  { key: 'org', label: '소속', hint: '팀·회사', max: 60 },
  { key: 'role', label: '직무', hint: '역할·직책', max: 60 },
  { key: 'note', label: '메모', hint: '이 사람에 대해 기억할 것', max: 500 },
]

/** 한 칸: 고치고 Enter나 다른 곳을 누르면 저장 */
function DetailInput({ person, field }: { person: Participant; field: (typeof FIELDS)[number] }) {
  const meetingId = useStore((s) => s.meeting?.id)!
  const saved = person[field.key] || ''
  const [v, setV] = useState(saved)
  useEffect(() => setV(saved), [saved])
  const save = async () => {
    const value = v.trim()
    if (value === saved) return
    const cur = useStore.getState().meeting?.participants || []
    const next = cur.map((p) => (p.id === person.id ? { ...p, [field.key]: value } : p))
    setParticipantsLocal(next)
    try {
      setParticipantsLocal((await api.setParticipants(meetingId, next)).participants)
    } catch (e) {
      setParticipantsLocal(cur)
      useStore.getState().toast((e as Error).message, 'error')
    }
  }
  return (
    <input
      className={`pd-input pd-${field.key}`}
      value={v}
      maxLength={field.max}
      placeholder={field.hint}
      aria-label={`${person.name} ${field.label}`}
      onChange={(e) => setV(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !e.nativeEvent.isComposing) (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') {
          setV(saved)
          requestAnimationFrame(() => (e.target as HTMLInputElement).blur())
        }
      }}
    />
  )
}

/** 참여자 아래 펼치는 세부 정보: 사람마다 소속·직무·메모. 전사 기록과 회의록에도 함께 나온다. */
export function ParticipantDetails() {
  const parts = useStore((s) => s.meeting?.participants || [])
  const [details, setDetails] = usePref(PEOPLE_DETAILS, false)
  if (!details || parts.length === 0) return null
  return (
    <div className="people-details">
      <div className="pd-row pd-cols">
        <span>참석자</span>
        {FIELDS.map((f) => <span key={f.key}>{f.label}</span>)}
        <button className="icon-btn pd-close" onClick={() => setDetails(false)} title="접기"><X size={13} /></button>
      </div>
      {parts.map((p, i) => (
        <div key={p.id} className="pd-row" data-pid={p.id}>
          <span className={`pd-name sp-${(i % 8) + 1}`}><span className="sp-dot" />{p.name}</span>
          {FIELDS.map((f) => <DetailInput key={f.key} person={p} field={f} />)}
        </div>
      ))}
      <p className="pd-tip">여기 적은 소속·직무·메모는 전사 기록과 회의록에 함께 나오고, 다음 회의에 같은 이름을 넣으면 소속·직무를 이어받아요.</p>
    </div>
  )
}
