import { NotebookPen, StickyNote, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { type Annotation } from '../lib/api'
import { annActions } from '../lib/annActions'
import type { View } from '../lib/model'
import { sendToNotes } from '../lib/notesExport'
import { seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'

export function MemoCard({ ann, view }: { ann: Annotation; view: View }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.body)
  const t = ann.anchor.t ?? 0

  const save = async () => {
    setEditing(false)
    if (draft.trim() && draft.trim() !== ann.body) {
      await annActions.patch(ann.id, { body: draft.trim() }, view)
    }
  }

  return (
    <div className="memo-card" id={`ann-${ann.id}`}>
      <div className="memo-head">
        <StickyNote size={13} />
        <button className="memo-time" onClick={() => seekTo(t)}>{fmtTime(t)}</button>
        <span>메모</span>
        {view === 'final' && ann.origin === 'live' && <span className="memo-origin">회의 중 작성</span>}
        {ann.exported_at && <span className="chip chip-ok" title="노트에 들어가 있어요"><NotebookPen size={11} />노트</span>}
        <span className="spacer" />
        <button
          className="icon-btn memo-del"
          title={ann.exported_at ? '노트에 한 번 더 넣기' : '노트로 보내기'}
          onClick={async () => {
            if (await sendToNotes(ann.meeting_id, [ann], view)) useStore.getState().toast('노트에 넣었어요', 'success')
          }}
        >
          <NotebookPen size={13} />
        </button>
        <button
          className="icon-btn memo-del"
          title="삭제"
          onClick={async () => {
            await annActions.remove(ann.id)
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
