import { useState } from 'react'
import { api } from '../lib/api'
import { addParticipant } from '../lib/speakers'
import { useStore } from '../lib/store'
import { SpeakerPicker } from './SpeakerPicker'

export function speakerIndex(label: string | null): number {
  if (!label) return 0
  const n = parseInt(label.replace(/\D/g, ''), 10)
  return Number.isFinite(n) ? ((n - 1) % 8) + 1 : 0
}

export function speakerDisplay(label: string | null, names: Record<string, string>): string {
  if (!label) return '화자 미상'
  if (names[label]) return names[label]
  return /^S\d+$/.test(label) ? `화자 ${label.slice(1)}` : label
}

export function SpeakerLabel({ label, compact }: { label: string | null; compact?: boolean }) {
  const meeting = useStore((s) => s.meeting)
  const refreshMeeting = useStore((s) => s.refreshMeeting)
  const [editing, setEditing] = useState(false)
  const [pick, setPick] = useState<DOMRect | null>(null)
  const names = meeting?.speakers || {}
  const name = speakerDisplay(label, names)
  const [draft, setDraft] = useState(name)
  const participants = meeting?.participants || []

  const setName = async (v: string) => {
    if (!meeting || !label) return
    await api.patchMeeting(meeting.id, { speakers: { ...names, [label]: v === `화자 ${label.slice(1)}` ? '' : v } })
    await Promise.all([refreshMeeting(), useStore.getState().refreshFinal()])
  }
  const save = async () => {
    setEditing(false)
    await setName(draft.trim())
  }

  return (
    <span className={`speaker sp-${speakerIndex(label)} ${compact ? 'compact' : ''}`}>
      <span className="sp-dot" />
      {editing ? (
        <input
          className="sp-input"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) save()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <button
          className="sp-name"
          title={label ? '눌러서 참여자 고르기' : undefined}
          onClick={(e) => { if (label) setPick(e.currentTarget.getBoundingClientRect()) }}
        >
          {name}
        </button>
      )}
      {pick && meeting && label && (
        <SpeakerPicker
          rect={pick}
          mode="final"
          participants={participants}
          current={participants.find((p) => p.name === names[label])?.id ?? null}
          onPick={(pid) => {
            setPick(null)
            // 방금 더한 참여자도 찾을 수 있게 최신 목록에서
            const fresh = useStore.getState().meeting?.participants || []
            setName(pid ? fresh.find((p) => p.id === pid)?.name || '' : '')
          }}
          onAdd={(n) => addParticipant(meeting.id, n)}
          onRename={() => { setPick(null); setDraft(name); setEditing(true) }}
          onClose={() => setPick(null)}
        />
      )}
    </span>
  )
}
