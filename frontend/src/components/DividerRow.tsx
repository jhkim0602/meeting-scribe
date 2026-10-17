import { Pencil, Trash2 } from 'lucide-react'
import { memo, useState } from 'react'
import { api, type Annotation } from '../lib/api'
import type { View } from '../lib/model'
import { useStore } from '../lib/store'
import { fmtDuration } from '../lib/time'

function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** 쉬는 시간·단락 구분선. 자동(일시정지)으로 생긴 것은 실제 시각과 길이를 함께 보여준다. */
export const DividerRow = memo(function DividerRow({ ann, view }: { ann: Annotation; view: View }) {
  const refreshViews = useStore((s) => s.refreshViews)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.body)
  const bs = ann.meta?.break_start
  const be = ann.meta?.break_end

  const save = async () => {
    setEditing(false)
    if (draft.trim() !== ann.body) {
      await api.patchAnnotation(ann.id, { body: draft.trim() }, view)
      await refreshViews()
    }
  }

  return (
    <div className="divider-row" id={`ann-${ann.id}`}>
      <span className="dv-line" />
      {editing ? (
        <input
          className="dv-input"
          value={draft}
          autoFocus
          placeholder="구분선 이름"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) save()
            if (e.key === 'Escape') {
              setDraft(ann.body)
              setEditing(false)
            }
          }}
        />
      ) : (
        <span className="dv-label" onDoubleClick={() => setEditing(true)}>
          {ann.body && <b>{ann.body}</b>}
          {bs && be && (
            <span className="dv-meta">
              {clock(bs)}–{clock(be)} · {fmtDuration(be - bs)}
            </span>
          )}
        </span>
      )}
      <span className="dv-line" />
      <span className="dv-actions">
        <button className="icon-btn" title="이름 바꾸기" onClick={() => { setDraft(ann.body); setEditing(true) }}><Pencil size={13} /></button>
        <button
          className="icon-btn"
          title="구분선 삭제"
          onClick={async () => {
            await api.deleteAnnotation(ann.id)
            await refreshViews()
          }}
        >
          <Trash2 size={13} />
        </button>
      </span>
    </div>
  )
})
