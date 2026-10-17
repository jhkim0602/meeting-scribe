import { useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'

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
  const names = meeting?.speakers || {}
  const name = speakerDisplay(label, names)
  const [draft, setDraft] = useState(name)

  const save = async () => {
    setEditing(false)
    if (!meeting || !label) return
    const v = draft.trim()
    await api.patchMeeting(meeting.id, { speakers: { ...names, [label]: v === `화자 ${label.slice(1)}` ? '' : v } })
    await refreshMeeting()
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
        <button className="sp-name" title={label ? '눌러서 이름 바꾸기' : undefined} onClick={() => { if (label) { setDraft(name); setEditing(true) } }}>
          {name}
        </button>
      )}
    </span>
  )
}
