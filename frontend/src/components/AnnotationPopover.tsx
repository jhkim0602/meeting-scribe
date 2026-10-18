import { CornerDownLeft, MessageSquare, Pencil, Trash2, Highlighter } from 'lucide-react'
import { useState } from 'react'
import { api, type Annotation } from '../lib/api'
import { annActions } from '../lib/annActions'
import { useStore } from '../lib/store'
import { fmtTime } from '../lib/time'
import { Floating } from './Floating'

const COLORS = ['yellow', 'green', 'blue', 'pink', 'purple']
const KIND = { comment: '댓글', memo: '메모', highlight: '강조', divider: '구분선' } as const

interface Props {
  anns: Annotation[]
  rect: DOMRect
  onClose: () => void
}

export function AnnotationPopover({ anns, rect, onClose }: Props) {
  return (
    <Floating rect={rect} onClose={onClose} className="ann-pop">
      {anns.map((a) => (
        <AnnCard key={a.id} ann={a} onDeleted={anns.length === 1 ? onClose : undefined} />
      ))}
    </Floating>
  )
}

function AnnCard({ ann, onDeleted }: { ann: Annotation; onDeleted?: () => void }) {
  const toast = useStore((s) => s.toast)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.body)
  const [reply, setReply] = useState('')
  const t = ann.anchor.t0 ?? ann.anchor.t ?? 0

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const patch = (p: Parameters<typeof api.patchAnnotation>[1]) => run(() => annActions.patch(ann.id, p))
  const remove = () => run(async () => {
    await annActions.remove(ann.id)
    onDeleted?.()
  })

  const saveDraft = () => {
    const v = draft.trim()
    if (!v) return
    setEditing(false)
    patch({ body: v })
  }

  const original = ann.pos && 'text' in ann.pos ? ann.pos.text : ann.anchor.quote

  return (
    <div className={`ann-card kind-${ann.kind}`}>
      <div className="ann-card-head">
        <span className={`kind-dot kind-${ann.kind}`} />
        <span className="ann-kind">{KIND[ann.kind]}</span>
        <span className="ann-time">{fmtTime(t)}</span>
        {ann.status === 'resolved' && <span className="chip chip-ok">해결됨</span>}
        <span className="spacer" />
        {ann.kind !== 'highlight' && (
          <button className="icon-btn" title="편집" onClick={() => setEditing((v) => !v)}><Pencil size={14} /></button>
        )}
        <button className="icon-btn" title="삭제" onClick={remove}><Trash2 size={14} /></button>
      </div>

      {
        <>
          {original && ann.kind === 'comment' && <blockquote className="ann-quote">{original}</blockquote>}
          {ann.kind === 'highlight' ? (
            <div className="ann-actions">
              <Highlighter size={14} className="muted" />
              {COLORS.map((c) => (
                <button key={c} className={`swatch hl-${c} ${ann.color === c ? 'on' : ''}`} title={c} onClick={() => patch({ color: c })} />
              ))}
            </div>
          ) : editing ? (
            <div className="ann-reply">
              <textarea className="input" rows={3} value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} />
              <div className="ann-actions">
                <button className="btn btn-sm btn-primary" onClick={saveDraft}>저장</button>
                <button className="btn btn-sm" onClick={() => setEditing(false)}>취소</button>
              </div>
            </div>
          ) : (
            <p className="ann-body">{ann.body}</p>
          )}
          {ann.kind === 'comment' && (
            <>
              {ann.replies.map((r, i) => (
                <div key={i} className="ann-replyline"><CornerDownLeft size={12} />{r.text}</div>
              ))}
              <div className="ann-reply-row">
                <input
                  className="input"
                  placeholder="답글"
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.nativeEvent.isComposing && reply.trim()) {
                      const v = reply.trim()
                      setReply('')
                      run(() => annActions.reply(ann.id, v))
                    }
                  }}
                />
                <button className="btn btn-sm" onClick={() => patch({ status: ann.status === 'resolved' ? 'open' : 'resolved' })}>
                  <MessageSquare size={14} />{ann.status === 'resolved' ? '다시 열기' : '해결'}
                </button>
              </div>
            </>
          )}
        </>
      }
    </div>
  )
}
