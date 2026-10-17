import { Check, CornerDownRight, MessageSquare, NotebookPen, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { memo, useState } from 'react'
import { type Annotation } from '../lib/api'
import { annActions } from '../lib/annActions'
import type { View } from '../lib/model'
import { annotationTime, quoteOf, sendToNotes } from '../lib/notesExport'
import { seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'

interface Props {
  ann: Annotation
  view: View
  meetingId: string
  flash: boolean
  onHover: (id: string | null) => void
}

/** 문장 바로 아래에 붙는 댓글 스레드 */
export const CommentThread = memo(function CommentThread({ ann, view, meetingId, flash, onHover }: Props) {
  const toast = useStore((s) => s.toast)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.body)
  const [replying, setReplying] = useState(false)
  const [reply, setReply] = useState('')
  const [expanded, setExpanded] = useState(false)
  const t = annotationTime(ann)
  const quote = quoteOf(ann, view)
  const resolved = ann.status === 'resolved'
  const approx = !!ann.pos && 'approx' in ann.pos && ann.pos.approx

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const save = () => {
    setEditing(false)
    const v = draft.trim()
    if (v && v !== ann.body) run(() => annActions.patch(ann.id, { body: v }, view))
  }
  const sendReply = () => {
    const v = reply.trim()
    if (!v) return
    setReply('')
    setReplying(false)
    run(() => annActions.reply(ann.id, v, view))
  }
  const toNotes = async () => {
    const n = await sendToNotes(meetingId, [ann], view)
    if (n) toast('노트에 넣었어요', 'success')
  }

  if (resolved && !expanded) {
    return (
      <button className="cmt cmt-resolved" id={`cmt-${ann.id}`} onClick={() => setExpanded(true)} onMouseEnter={() => onHover(ann.id)} onMouseLeave={() => onHover(null)}>
        <Check size={12} />
        <span>해결됨</span>
        <span className="cmt-resolved-body">{ann.body}</span>
      </button>
    )
  }

  return (
    <div
      className={`cmt ${flash ? 'flash' : ''} ${resolved ? 'is-resolved' : ''}`}
      id={`cmt-${ann.id}`}
      tabIndex={-1}
      onMouseEnter={() => onHover(ann.id)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="cmt-head">
        <MessageSquare size={13} className="cmt-icon" />
        <button className="cmt-time" onClick={() => seekTo(t)} title="이 시점부터 듣기">{fmtTime(t)}</button>
        {quote && <span className="cmt-quote" title={quote}>{quote}</span>}
        {view === 'final' && ann.origin === 'live' && <span className="chip chip-soft" title="실시간 기록에서 단 댓글을 원본 위치로 옮겼어요">실시간에서</span>}
        {approx && <span className="chip chip-warn" title="문장이 많이 달라 시간 기준으로 위치를 추정했어요">위치 추정</span>}
        {ann.exported_at && <span className="chip chip-ok" title="노트에 들어가 있어요"><NotebookPen size={11} />노트</span>}
        <span className="spacer" />
        <div className="cmt-actions">
          <button className="icon-btn" title="답글" onClick={() => setReplying((v) => !v)}><CornerDownRight size={14} /></button>
          <button className="icon-btn" title={ann.exported_at ? '노트에 한 번 더 넣기' : '노트로 보내기'} onClick={toNotes}><NotebookPen size={14} /></button>
          <button className="icon-btn" title={resolved ? '다시 열기' : '해결'} onClick={() => run(() => annActions.patch(ann.id, { status: resolved ? 'open' : 'resolved' }, view))}>
            {resolved ? <RotateCcw size={14} /> : <Check size={14} />}
          </button>
          <button className="icon-btn" title="편집" onClick={() => { setDraft(ann.body); setEditing(true) }}><Pencil size={14} /></button>
          <button className="icon-btn" title="삭제" onClick={() => run(() => annActions.remove(ann.id))}><Trash2 size={14} /></button>
        </div>
      </div>
      {editing ? (
        <textarea
          className="cmt-input"
          value={draft}
          autoFocus
          rows={Math.max(1, draft.split('\n').length)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              save()
            }
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <p className="cmt-body" onDoubleClick={() => { setDraft(ann.body); setEditing(true) }}>{ann.body}</p>
      )}
      {ann.replies.map((r, i) => (
        <div key={i} className="cmt-reply"><CornerDownRight size={12} />{r.text}</div>
      ))}
      {replying && (
        <input
          className="cmt-input"
          placeholder="답글을 쓰고 Enter"
          value={reply}
          autoFocus
          onChange={(e) => setReply(e.target.value)}
          onBlur={() => !reply.trim() && setReplying(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) sendReply()
            if (e.key === 'Escape') setReplying(false)
          }}
        />
      )}
    </div>
  )
})
