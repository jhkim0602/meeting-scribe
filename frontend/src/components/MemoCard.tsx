import { StickyNote, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api, type Annotation } from '../lib/api'
import type { View } from '../lib/model'
import { seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'

export function MemoCard({ ann, view }: { ann: Annotation; view: View }) {
  const refreshViews = useStore((s) => s.refreshViews)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.body)
  const t = ann.anchor.t ?? 0

  const save = async () => {
    setEditing(false)
    if (draft.trim() && draft.trim() !== ann.body) {
      await api.patchAnnotation(ann.id, { body: draft.trim() }, view)
      await refreshViews()
    }
  }

  return (
    <div className="memo-card" id={`ann-${ann.id}`}>
      <div className="memo-head">
        <StickyNote size={13} />
        <button className="memo-time" onClick={() => seekTo(t)}>{fmtTime(t)}</button>
        <span>메모</span>
        {view === 'final' && ann.origin === 'live' && <span className="memo-origin">회의 중 작성</span>}
        <span className="spacer" />
        <button
          className="icon-btn memo-del"
          title="삭제"
          onClick={async () => {
            await api.deleteAnnotation(ann.id)
            await refreshViews()
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>
      {editing ? (
        <textarea
          className="memo-input"
          value={draft}
          autoFocus
          rows={Math.max(2, draft.split('\n').length)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              save()
            }
            if (e.key === 'Escape') {
              setDraft(ann.body)
              setEditing(false)
            }
          }}
        />
      ) : (
        <p className="memo-body" onClick={() => setEditing(true)}>{ann.body}</p>
      )}
    </div>
  )
}
