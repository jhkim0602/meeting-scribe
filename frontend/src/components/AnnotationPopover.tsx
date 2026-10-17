import { Check, CornerDownLeft, MessageSquare, Pencil, RotateCcw, Trash2, X, Highlighter } from 'lucide-react'
import { useState } from 'react'
import { api, type Annotation } from '../lib/api'
import { isRange, type View } from '../lib/model'
import { useStore } from '../lib/store'
import { fmtTime } from '../lib/time'
import { Floating } from './Floating'

const COLORS = ['yellow', 'green', 'blue', 'pink', 'purple']
const KIND = { comment: '댓글', edit: '수정', memo: '메모', highlight: '강조' } as const

interface Props {
  anns: Annotation[]
  rect: DOMRect
  view: View
  onClose: () => void
}

export function AnnotationPopover({ anns, rect, view, onClose }: Props) {
  return (
    <Floating rect={rect} onClose={onClose} className="ann-pop">
      {anns.map((a) => (
        <AnnCard key={a.id} ann={a} view={view} onDeleted={anns.length === 1 ? onClose : undefined} />
      ))}
    </Floating>
  )
}

function AnnCard({ ann, view, onDeleted }: { ann: Annotation; view: View; onDeleted?: () => void }) {
  const refreshViews = useStore((s) => s.refreshViews)
  const toast = useStore((s) => s.toast)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(ann.kind === 'edit' ? ann.replacement || '' : ann.body)
  const [reply, setReply] = useState('')
  const t = ann.anchor.t0 ?? ann.anchor.t ?? 0
  const approx = isRange(ann) && ann.pos.approx
  const movedFromLive = view === 'final' && ann.origin === 'live'

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await refreshViews()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const patch = (p: Parameters<typeof api.patchAnnotation>[1]) => run(() => api.patchAnnotation(ann.id, p, view))
  const remove = () => run(async () => {
    await api.deleteAnnotation(ann.id)
    onDeleted?.()
  })

  const saveDraft = () => {
    const v = draft.trim()
    if (!v) return
    setEditing(false)
    patch(ann.kind === 'edit' ? { replacement: v } : { body: v })
  }

  const original = ann.kind === 'edit' ? (view === 'final' && isRange(ann) ? ann.pos.text : ann.anchor.quote) : ann.anchor.quote

  return (
    <div className={`ann-card kind-${ann.kind}`}>
      <div className="ann-card-head">
        <span className={`kind-dot kind-${ann.kind}`} />
        <span className="ann-kind">{KIND[ann.kind]}</span>
        <span className="ann-time">{fmtTime(t)}</span>
        {movedFromLive && <span className="chip chip-soft" title="실시간 기록에서 남긴 것을 원본 위치로 옮겨 표시했어요">실시간에서</span>}
        {approx && <span className="chip chip-warn" title="문장이 많이 달라 시간 기준으로 위치를 추정했어요">위치 추정</span>}
        {ann.status === 'resolved' && <span className="chip chip-ok">해결됨</span>}
        <span className="spacer" />
        {ann.kind !== 'highlight' && (
          <button className="icon-btn" title="편집" onClick={() => setEditing((v) => !v)}><Pencil size={14} /></button>
        )}
        <button className="icon-btn" title="삭제" onClick={remove}><Trash2 size={14} /></button>
      </div>

      {ann.kind === 'edit' ? (
        <div className="ann-edit">
          <div className="ann-edit-row"><span className="lbl">{view === 'final' ? '원본' : '원문'}</span><del>{original}</del></div>
          <div className="ann-edit-row"><span className="lbl">수정</span>
            {editing ? (
              <input className="input" value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) saveDraft() }} />
            ) : (
              <ins>{ann.replacement}</ins>
            )}
          </div>
          {view === 'final' && ann.match === 'same' && <p className="ann-note">원본도 이렇게 받아 적었어요.</p>}
          {view === 'final' && ann.match !== 'same' && (
            <div className="ann-actions">
              {ann.status === 'open' ? (
                <>
                  <button className="btn btn-sm btn-primary" onClick={() => patch({ status: 'accepted' })}><Check size={14} />원본에 적용</button>
                  <button className="btn btn-sm" onClick={() => patch({ status: 'dismissed' })}><X size={14} />무시</button>
                </>
              ) : (
                <button className="btn btn-sm" onClick={() => patch({ status: 'open' })}><RotateCcw size={14} />{ann.status === 'accepted' ? '적용 취소' : '다시 보기'}</button>
              )}
            </div>
          )}
        </div>
      ) : (
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
                      run(() => api.reply(ann.id, v, view))
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
      )}
    </div>
  )
}
