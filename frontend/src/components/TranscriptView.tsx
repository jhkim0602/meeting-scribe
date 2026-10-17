import { memo, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as RMouseEvent } from 'react'
import { Highlighter, MessageSquarePlus, NotebookPen, PenLine, StickyNote } from 'lucide-react'
import { api, type Annotation, type Unit } from '../lib/api'
import { buildModel, piecesFor, timeAtOffset, unitAtTime, type Piece, type View } from '../lib/model'
import { readSelection, type TextSelection } from '../lib/selection'
import { bridges, seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'
import { sendToNotes } from '../lib/notesExport'
import { AnnotationPopover } from './AnnotationPopover'
import { CommentThread } from './CommentThread'
import { Composer } from './Composer'
import { Floating } from './Floating'
import { MemoCard } from './MemoCard'
import { SpeakerLabel } from './SpeakerLabel'

interface Props {
  view: View
  meetingId: string
  units: Unit[]
  annotations: Annotation[]
  partial?: { t0: number; t1: number; text: string } | null
  recording?: boolean
}

type ComposerState =
  | { mode: 'comment' | 'edit'; rect: DOMRect; start: { unit: string; offset: number }; end: { unit: string; offset: number }; quote: string }
  | { mode: 'memo'; rect: DOMRect; t: number; after: string | null }

function markClasses(anns: Annotation[]): string {
  const cls: string[] = []
  for (const a of anns) {
    if (a.kind === 'comment') cls.push('mk-comment', a.status === 'resolved' ? 'resolved' : '')
    if (a.kind === 'highlight') cls.push('mk-hl', `hl-${a.color || 'yellow'}`)
    if (a.pos && 'approx' in a.pos && a.pos.approx) cls.push('mk-approx')
  }
  return cls.filter(Boolean).join(' ')
}

function PieceView({ p, onOpen, hover }: { p: Piece; onOpen: (ids: string[], el: HTMLElement) => void; hover: string | null }) {
  const ids = p.type === 'edit' ? [p.ann.id, ...p.anns.map((a) => a.id)] : p.anns.map((a) => a.id)
  const hot = hover !== null && ids.includes(hover) ? ' hot' : ''
  const click = ids.length
    ? (e: RMouseEvent<HTMLElement>) => {
        if (window.getSelection()?.isCollapsed !== false) onOpen(ids, e.currentTarget)
      }
    : undefined
  if (p.type === 'text') {
    return (
      <span data-o={p.o} data-k="o" data-len={p.text.length} className={(markClasses(p.anns) + hot) || undefined} onClick={click}>
        {p.text}
      </span>
    )
  }
  const extra = markClasses(p.anns)
  const repl = p.ann.replacement || ''
  switch (p.display) {
    case 'proof':
      return (
        <span className={`mk-edit proof ${extra}`} onClick={click} title="회의 중 고친 내용이에요. 눌러서 원본에 적용할 수 있어요.">
          <del data-o={p.o} data-k="o" data-len={p.text.length}>{p.text}</del>
          <ins data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</ins>
        </span>
      )
    case 'applied':
      return (
        <span className={`mk-edit applied ${extra}`} onClick={click} title={`원문: ${p.text}`}>
          <span data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</span>
        </span>
      )
    case 'same':
      return (
        <span className={`mk-edit same ${extra}`} onClick={click} title="회의 중 고친 내용과 원본이 같아요">
          <span data-o={p.o} data-k="o" data-len={p.text.length}>{p.text}</span>
        </span>
      )
    case 'clean-applied':
      return <span data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</span>
    default:
      return <span data-o={p.o} data-k="o" data-len={p.text.length} className={extra || undefined} onClick={extra ? click : undefined}>{p.text}</span>
  }
}

interface RowProps {
  u: Unit
  pieces: Piece[]
  showSpeaker: boolean
  active: boolean
  flash: boolean
  comments: Annotation[] | undefined
  memos: Annotation[] | undefined
  view: View
  meetingId: string
  showMarks: boolean
  hover: string | null
  flashAnn: string | null
  onOpen: (ids: string[], el: HTMLElement) => void
  onMemo: (u: Unit, el: HTMLElement) => void
  onComment: (u: Unit, el: HTMLElement) => void
  onHover: (id: string | null) => void
}

const UnitRow = memo(function UnitRow({ u, pieces, showSpeaker, active, flash, comments, memos, view, meetingId, showMarks, hover, flashAnn, onOpen, onMemo, onComment, onHover }: RowProps) {
  return (
    <>
      {showSpeaker && (
        <div className="speaker-row">
          <SpeakerLabel label={u.speaker ?? null} />
        </div>
      )}
      <div id={`unit-${u.id}`} className={`unit ${active ? 'active' : ''} ${flash ? 'flash' : ''}`}>
        <button className="u-time" onClick={() => seekTo(u.t0)} title="이 부분부터 듣기">
          {fmtTime(u.t0)}
        </button>
        <p className="utext" data-unit={u.id} data-len={u.text.length}>
          {pieces.map((p, k) => (
            <PieceView key={k} p={p} onOpen={onOpen} hover={hover} />
          ))}
        </p>
        <div className="u-actions">
          <button className="u-act" title="이 문장에 댓글" onClick={(e) => onComment(u, e.currentTarget)}>
            <MessageSquarePlus size={14} />
          </button>
          <button className="u-act" title="이 문장 뒤에 메모" onClick={(e) => onMemo(u, e.currentTarget)}>
            <StickyNote size={14} />
          </button>
        </div>
      </div>
      {showMarks && comments?.map((c) => <CommentThread key={c.id} ann={c} view={view} meetingId={meetingId} flash={flashAnn === c.id} onHover={onHover} />)}
      {showMarks && memos?.map((m) => <MemoCard key={m.id} ann={m} view={view} />)}
    </>
  )
})

function unitHasAnn(model: ReturnType<typeof buildModel>, unitId: string, annId: string): boolean {
  return !!model.perUnit.get(unitId)?.some((r) => r.ann.id === annId)
}

export function TranscriptView({ view, meetingId, units, annotations, partial, recording }: Props) {
  const showMarks = useStore((s) => s.showMarks)
  const playing = useStore((s) => s.playing)
  const follow = useStore((s) => s.follow)
  const flashUnit = useStore((s) => s.flashUnit)
  const refreshViews = useStore((s) => s.refreshViews)
  const toast = useStore((s) => s.toast)
  const ref = useRef<HTMLDivElement>(null)
  const [sel, setSel] = useState<TextSelection | null>(null)
  const [composer, setComposer] = useState<ComposerState | null>(null)
  const [pop, setPop] = useState<{ ids: string[]; rect: DOMRect } | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [flashAnn, setFlashAnn] = useState<string | null>(null)

  const model = useMemo(() => buildModel(units, annotations), [units, annotations])
  const byId = useMemo(() => new Map(annotations.map((a) => [a.id, a])), [annotations])
  const unitById = useMemo(() => new Map(units.map((u) => [u.id, u])), [units])
  const activeId = useStore((s) => (recording || (!s.playing && s.playerTime <= 0) ? null : unitAtTime(units, s.playerTime)))
  const piecesByUnit = useMemo(() => units.map((u) => piecesFor(u.text, model.perUnit.get(u.id), view, showMarks)), [units, model, view, showMarks])

  const captureSelection = useCallback(() => {
    setTimeout(() => {
      if (!ref.current) return
      setSel(readSelection(ref.current))
    }, 0)
  }, [])

  useEffect(() => {
    const onSel = () => {
      const s = window.getSelection()
      if (!s || s.isCollapsed) setSel(null)
    }
    document.addEventListener('selectionchange', onSel)
    return () => document.removeEventListener('selectionchange', onSel)
  }, [])

  // 재생 중인 문장 따라가기
  useEffect(() => {
    if (!activeId || !playing || !follow) return
    document.getElementById(`unit-${activeId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [activeId, playing, follow])

  useEffect(() => {
    if (!flashUnit) return
    document.getElementById(`unit-${flashUnit}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [flashUnit])

  const create = async (body: Record<string, unknown>, toNotes = false) => {
    try {
      const ann = await api.addAnnotation(meetingId, { view, ...body })
      if (toNotes) await sendToNotes(meetingId, [ann], view)
      else await refreshViews()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const act = async (kind: 'comment' | 'edit' | 'highlight' | 'note') => {
    if (!sel) return
    const s = sel
    window.getSelection()?.removeAllRanges()
    setSel(null)
    if (kind === 'highlight') {
      await create({ kind: 'highlight', start: s.start, end: s.end, color: 'yellow' })
    } else if (kind === 'note') {
      const u = unitById.get(s.start.unit)
      const t = u ? timeAtOffset(u, s.start.offset) : 0
      if (bridges.notes) {
        bridges.notes.insertQuote(s.text, t)
        toast('노트에 인용으로 넣었어요', 'success')
      }
    } else {
      setComposer({ mode: kind, rect: s.rect, start: s.start, end: s.end, quote: s.text })
    }
  }

  const openPop = useCallback(
    (ids: string[], el: HTMLElement) => {
      const comment = ids.map((id) => byId.get(id)).find((a) => a?.kind === 'comment')
      if (comment) {
        // 댓글은 문장 아래에 보이므로 그쪽으로 이동해 깜빡인다
        document.getElementById(`cmt-${comment.id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
        setFlashAnn(comment.id)
        setTimeout(() => setFlashAnn((v) => (v === comment.id ? null : v)), 1400)
        return
      }
      setPop({ ids, rect: el.getBoundingClientRect() })
    },
    [byId],
  )
  const openMemo = useCallback((u: Unit, el: HTMLElement) => setComposer({ mode: 'memo', rect: el.getBoundingClientRect(), t: u.t1 + 0.01, after: u.id }), [])
  const openComment = useCallback(
    (u: Unit, el: HTMLElement) =>
      setComposer({ mode: 'comment', rect: el.getBoundingClientRect(), start: { unit: u.id, offset: 0 }, end: { unit: u.id, offset: u.text.length }, quote: u.text }),
    [],
  )

  // 글자를 선택한 상태에서 C 또는 ⌘⌥M → 댓글
  useEffect(() => {
    if (!sel || composer) return
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase()
      const plainC = k === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
      const docsShortcut = (e.metaKey || e.ctrlKey) && e.altKey && (k === 'm' || e.code === 'KeyM')
      if (plainC || docsShortcut) {
        e.preventDefault()
        act('comment')
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  const popAnns = pop ? pop.ids.map((id) => byId.get(id)).filter((a): a is Annotation => !!a) : []

  return (
    <div className={`transcript view-${view} ${showMarks ? '' : 'marks-off'}`} ref={ref} onMouseUp={captureSelection} onKeyUp={(e) => e.shiftKey && captureSelection()}>
      {showMarks && model.memosBefore.map((m) => <MemoCard key={m.id} ann={m} view={view} />)}
      {units.map((u, i) => (
        <UnitRow
          key={u.id}
          u={u}
          pieces={piecesByUnit[i]}
          showSpeaker={view === 'final' && (i === 0 || units[i - 1].speaker !== u.speaker)}
          active={u.id === activeId}
          flash={u.id === flashUnit}
          comments={model.commentEnds.get(u.id)}
          memos={model.memosAfter.get(u.id)}
          view={view}
          meetingId={meetingId}
          showMarks={showMarks}
          hover={hover !== null && unitHasAnn(model, u.id, hover) ? hover : null}
          flashAnn={flashAnn !== null && model.commentEnds.get(u.id)?.some((c) => c.id === flashAnn) ? flashAnn : null}
          onOpen={openPop}
          onMemo={openMemo}
          onComment={openComment}
          onHover={setHover}
        />
      ))}
      {partial && partial.text && (
        <div className="unit partial">
          <span className="u-time">{fmtTime(partial.t0)}</span>
          <p className="utext-partial">
            {partial.text}
            <span className="typing" />
          </p>
        </div>
      )}

      {sel && !composer && (
        <Floating rect={sel.rect} placement="above" className="sel-toolbar">
          <button className="primary" onMouseDown={(e) => e.preventDefault()} onClick={() => act('comment')} title="댓글 (C)"><MessageSquarePlus size={15} />댓글<kbd>C</kbd></button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('highlight')}><Highlighter size={15} />강조</button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('edit')}><PenLine size={15} />수정</button>
          <span className="sep" />
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('note')}><NotebookPen size={15} />노트로</button>
        </Floating>
      )}

      {composer && composer.mode !== 'memo' && (
        <Composer
          rect={composer.rect}
          mode={composer.mode}
          quote={composer.quote}
          initial={composer.mode === 'edit' ? composer.quote : ''}
          onClose={() => setComposer(null)}
          onSubmit={(text, toNotes) =>
            create(
              composer.mode === 'comment'
                ? { kind: 'comment', start: composer.start, end: composer.end, body: text }
                : { kind: 'edit', start: composer.start, end: composer.end, replacement: text },
              toNotes,
            )
          }
        />
      )}
      {composer && composer.mode === 'memo' && (
        <Composer rect={composer.rect} mode="memo" onClose={() => setComposer(null)} onSubmit={(text, toNotes) => create({ kind: 'memo', t: composer.t, after: composer.after, body: text }, toNotes)} />
      )}
      {pop && popAnns.length > 0 && <AnnotationPopover anns={popAnns} rect={pop.rect} view={view} onClose={() => setPop(null)} />}
    </div>
  )
}
