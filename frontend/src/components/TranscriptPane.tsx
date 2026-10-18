import { ArrowDown, Check, Keyboard, ListTree, MessageSquare, MessageSquarePlus, Mic, MoreHorizontal, NotebookPen, StickyNote } from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, useState, type ComponentProps, type RefObject } from 'react'
import { type Annotation, type Unit } from '../lib/api'
import { annActions } from '../lib/annActions'
import { sendToNotes } from '../lib/notesExport'
import { COMMENT_TO_NOTES, usePref, usePrefStr, VIEW_CLOCK, VIEW_COMMENTS_COLLAPSED, VIEW_FONT, type FontSize } from '../lib/prefs'
import { bridges, currentClock, useStore, type Tab } from '../lib/store'
import { fmtTime } from '../lib/time'
import { Floating } from './Floating'
import { ProcessingCard } from './ProcessingCard'
import { TranscriptView } from './TranscriptView'

// AI 요약(마크다운 그리기)은 탭을 열 때 불러온다
const SummaryView = lazy(() => import('./SummaryView').then((m) => ({ default: m.SummaryView })))

type Filter = 'comment' | 'memo' | 'all'

function AnnList({ anns, onClose, rect, meetingId }: { anns: Annotation[]; onClose: () => void; rect: DOMRect; meetingId: string }) {
  const flash = useStore((s) => s.flash)
  const toast = useStore((s) => s.toast)
  const [filter, setFilter] = useState<Filter>('comment')
  const [auto, setAuto] = usePref(COMMENT_TO_NOTES, false)
  const sorted = [...anns].sort((a, b) => (a.anchor.t0 ?? a.anchor.t ?? 0) - (b.anchor.t0 ?? b.anchor.t ?? 0))
  const shown = sorted.filter((a) => filter === 'all' || a.kind === filter)
  const exportable = sorted.filter((a) => (a.kind === 'comment' || a.kind === 'memo') && !a.exported_at)
  const count = (k: Filter) => (k === 'all' ? anns.length : anns.filter((a) => a.kind === k).length)

  const exportAll = async () => {
    const n = await sendToNotes(meetingId, exportable, { heading: exportable.length > 1 ? '회의 중 댓글' : undefined })
    if (n) toast(`댓글·메모 ${n}개를 노트에 넣었어요`, 'success')
  }

  return (
    <Floating rect={rect} onClose={onClose} className="ann-list">
      <div className="ann-list-tabs">
        {(['comment', 'memo', 'all'] as Filter[]).map((k) => (
          <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
            {k === 'comment' ? '댓글' : k === 'memo' ? '메모' : '전체'} <span>{count(k)}</span>
          </button>
        ))}
      </div>
      <div className="ann-list-export">
        <button className="btn btn-sm btn-primary" disabled={!exportable.length} onClick={exportAll}>
          <NotebookPen size={14} />
          {exportable.length ? `아직 안 보낸 ${exportable.length}개 노트로` : '모두 노트에 있어요'}
        </button>
        <label className="to-notes" title="댓글·메모를 달 때 노트에도 바로 추가해요">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          새 댓글 바로 노트에
        </label>
      </div>
      {shown.length === 0 && <p className="muted pad">아직 없어요. 기록에서 글자를 드래그하고 C를 누르면 댓글을 달 수 있어요.</p>}
      {shown.map((a) => {
        const target = a.pos ? ('start' in a.pos ? a.pos.start.unit : a.pos.after) : null
        return (
          <button
            key={a.id}
            className="ann-list-item"
            onClick={() => {
              if (target) flash(target)
              onClose()
            }}
          >
            <span className={`kind-dot kind-${a.kind}`} />
            <span className="ann-list-time">{fmtTime(a.anchor.t0 ?? a.anchor.t ?? 0)}</span>
            <span className="ann-list-text">
              {a.kind === 'divider' ? `구분선 ${a.body}` : a.body || a.anchor.quote}
              {a.kind === 'comment' && a.anchor.quote && <em>{a.anchor.quote}</em>}
            </span>
            {a.exported_at ? <NotebookPen size={12} className="ann-list-exported" /> : <span />}
          </button>
        )
      })}
    </Floating>
  )
}

/** 구분선으로 나뉜 구간 목차. 구분선이 있을 때만 보인다. */
function Toc({ anns, units, rect, onClose }: { anns: Annotation[]; units: Unit[]; rect: DOMRect; onClose: () => void }) {
  const dividers = anns.filter((a) => a.kind === 'divider' && a.pos && 'after' in a.pos).sort((a, b) => (a.anchor.t ?? 0) - (b.anchor.t ?? 0))
  const index = new Map(units.map((u, i) => [u.id, i]))
  const go = (unitId: string | undefined) => {
    if (unitId) bridges.transcript?.scrollToUnit(unitId, { align: 'start' })
    onClose()
  }
  return (
    <Floating rect={rect} onClose={onClose} className="toc">
      <button className="toc-item" onClick={() => go(units[0]?.id)}>
        <span className="toc-time">{fmtTime(units[0]?.t0 ?? 0)}</span>
        <span className="toc-label">처음</span>
      </button>
      {dividers.map((d) => {
        const after = d.pos && 'after' in d.pos ? d.pos.after : null
        const next = after === null ? units[0] : units[(index.get(after) ?? -2) + 1]
        const mins = d.meta?.break_start && d.meta?.break_end ? Math.round((d.meta.break_end - d.meta.break_start) / 60) : null
        return (
          <button key={d.id} className="toc-item" onClick={() => go(next?.id)} disabled={!next}>
            <span className="toc-time">{fmtTime(next?.t0 ?? d.anchor.t ?? 0)}</span>
            <span className="toc-label">{d.body || '구분선'}</span>
            {mins !== null && mins > 0 && <span className="toc-meta">{mins}분 쉼</span>}
          </button>
        )
      })}
    </Floating>
  )
}

/** ⋯ 보기 설정: 메모·댓글, 댓글 접기, 실제 시각, 글자 크기 */
function ViewMenu({ rect, onClose, hasClock }: { rect: DOMRect; onClose: () => void; hasClock: boolean }) {
  const showMarks = useStore((s) => s.showMarks)
  const set = useStore((s) => s.set)
  const [collapsed, setCollapsed] = usePref(VIEW_COMMENTS_COLLAPSED, false)
  const [clock, setClock] = usePref(VIEW_CLOCK, false)
  const [font, setFont] = usePrefStr<FontSize>(VIEW_FONT, 'm')
  const Item = ({ on, label, onClick, disabled, title }: { on: boolean; label: string; onClick: () => void; disabled?: boolean; title?: string }) => (
    <button className="menu-item check-item" onClick={onClick} disabled={disabled} title={title} role="menuitemcheckbox" aria-checked={on}>
      <span className="check-box">{on && <Check size={13} />}</span>
      {label}
    </button>
  )
  return (
    <Floating rect={rect} onClose={onClose} className="menu view-menu">
      <Item on={showMarks} label="메모·댓글 보기" onClick={() => set({ showMarks: !showMarks })} />
      <Item on={collapsed} label="댓글 한 줄로 접기" onClick={() => setCollapsed(!collapsed)} />
      <Item on={clock && hasClock} label="실제 시각으로 보기" onClick={() => setClock(!clock)} disabled={!hasClock} title={hasClock ? undefined : '올린 파일은 녹음한 시각을 몰라요'} />
      <div className="menu-sep" />
      <div className="menu-row">
        <span>글자 크기</span>
        <div className="seg">
          {(['s', 'm', 'l'] as FontSize[]).map((f) => (
            <button key={f} className={font === f ? 'on' : ''} onClick={() => setFont(f)}>{f === 's' ? '작게' : f === 'm' ? '보통' : '크게'}</button>
          ))}
        </div>
      </div>
      <div className="menu-sep" />
      <button className="menu-item" onClick={() => { onClose(); useStore.getState().set({ shortcutsOpen: true }) }}>
        <Keyboard size={15} />단축키<kbd className="menu-kbd">?</kbd>
      </button>
    </Floating>
  )
}

/** 아래 입력창. 메모 모드에서 보여 주는 지금 시각은 여기서만 초마다 바뀐다 (기록 화면은 다시 그리지 않는다). */
function MemoInput({ inputRef, mode, ...props }: { inputRef: RefObject<HTMLInputElement | null>; mode: 'comment' | 'memo' } & ComponentProps<'input'>) {
  const sec = useStore((s) => (mode === 'memo' ? Math.floor(s.rec.elapsed) : 0))
  return <input ref={inputRef} placeholder={mode === 'comment' ? '방금 문장에 댓글… (Enter)' : `${fmtTime(sec)} 지금 이 순간에 메모… (Enter)`} {...props} />
}

export function TranscriptPane() {
  const meeting = useStore((s) => s.meeting)!
  const live = useStore((s) => s.live)
  const job = useStore((s) => s.job)
  const tab = useStore((s) => s.tab)
  const setTab = useStore((s) => s.setTab)
  const partial = useStore((s) => s.partial)
  // 녹음 상태만 본다: 소리 크기·시간은 1초에 10번 넘게 바뀌므로 여기서 받으면 긴 기록 화면이 계속 다시 그려진다
  const recordingHere = useStore((s) => s.rec.status !== 'idle' && s.rec.meetingId === s.meeting?.id)
  const showMarks = useStore((s) => s.showMarks)
  const follow = useStore((s) => s.follow)
  const playing = useStore((s) => s.playing)
  const set = useStore((s) => s.set)
  const toast = useStore((s) => s.toast)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [listRect, setListRect] = useState<DOMRect | null>(null)
  const [tocRect, setTocRect] = useState<DOMRect | null>(null)
  const [viewRect, setViewRect] = useState<DOMRect | null>(null)
  const memoInput = useRef<HTMLInputElement>(null)
  const seenCount = useRef(0)
  const userScrollAt = useRef(0)
  const [memo, setMemo] = useState('')
  const [barMode, setBarMode] = useState<'comment' | 'memo'>('comment')
  const [toNotes] = usePref(COMMENT_TO_NOTES, false)
  const jobActive = !!job && (job.status === 'queued' || job.status === 'running' || job.status === 'error')

  const anns = live?.annotations || []
  const viewUnits = live?.units || []
  const hasDividers = anns.some((a) => a.kind === 'divider' && a.pos)

  // 따라가기: 녹음 중에는 맨 아래(새 문장), 재생 중에는 재생 위치를 따라간다.
  // 사용자가 직접 스크롤해 다른 곳을 보면 멈추고 "새 문장 n개" / "재생 위치로" 버튼을 띄운다.
  const liveCount = live?.units.length || 0
  useEffect(() => {
    const el = scrollRef.current
    if (!el || tab !== 'live' || !recordingHere || !follow) return
    el.scrollTo({ top: el.scrollHeight })
    seenCount.current = liveCount
  }, [liveCount, partial?.text, tab, follow, recordingHere])

  useEffect(() => {
    set({ follow: true })
  }, [tab, meeting.id, set])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const mark = () => {
      userScrollAt.current = Date.now()
    }
    const onScroll = () => {
      if (Date.now() - userScrollAt.current > 800) return // 프로그램이 움직인 스크롤
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120
      const st = useStore.getState()
      if (st.tab === 'live' && recordingHere) {
        if (near !== st.follow) set({ follow: near })
        if (near) seenCount.current = useStore.getState().live?.units.length || 0
      } else if (st.playing && st.follow) set({ follow: false })
    }
    el.addEventListener('wheel', mark, { passive: true })
    el.addEventListener('touchmove', mark, { passive: true })
    el.addEventListener('pointerdown', mark)
    el.addEventListener('keydown', mark)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('wheel', mark)
      el.removeEventListener('touchmove', mark)
      el.removeEventListener('pointerdown', mark)
      el.removeEventListener('keydown', mark)
      el.removeEventListener('scroll', onScroll)
    }
  }, [recordingHere, set])

  const newCount = tab === 'live' && recordingHere && !follow ? Math.max(0, liveCount - seenCount.current) : 0
  const showJump = !follow && (newCount > 0 || (playing && tab !== 'summary'))

  // C/M 단축키로 아래 입력창에 바로 쓰기
  useEffect(() => {
    if (!(tab === 'live' && recordingHere)) return
    bridges.memoBar = {
      focus: (mode) => {
        setBarMode(mode)
        memoInput.current?.focus()
      },
    }
    return () => {
      bridges.memoBar = null
    }
  }, [tab, recordingHere])

  const addFromBar = async () => {
    const text = memo.trim()
    if (!text) return
    const units = live?.units || []
    const last = units[units.length - 1]
    if (barMode === 'comment' && !last) {
      toast('아직 확정된 문장이 없어요. 메모로 남기거나 잠시 후 다시 시도해 주세요.', 'error')
      return
    }
    setMemo('')
    try {
      const ann = await annActions.add(
        meeting.id,
        barMode === 'comment'
          ? { kind: 'comment', view: 'live', start: { unit: last.id, offset: 0 }, end: { unit: last.id, offset: last.text.length }, body: text }
          : { kind: 'memo', view: 'live', t: currentClock(), after: last ? last.id : null, body: text },
      )
      if (toNotes) await sendToNotes(meeting.id, [ann])
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const tabs: { key: Tab; label: string; badge?: string }[] = [
    { key: 'live', label: '기록', badge: liveCount ? String(liveCount) : undefined },
    { key: 'summary', label: 'AI 요약', badge: meeting.has_summary ? '●' : undefined },
  ]

  return (
    <section className="pane transcript-pane">
      <header className="pane-head">
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key} className={`tab ${tab === t.key ? 'on' : ''}`} onClick={() => setTab(t.key)}>
              {t.key === 'live' && recordingHere && <span className="rec-dot" />}
              {t.label}
              {t.badge && <span className="tab-badge">{t.badge}</span>}
            </button>
          ))}
        </div>
        <span className="spacer" />
        {tab !== 'summary' && (
          <>
            {hasDividers && (
              <button className="tool-btn" onClick={(e) => setTocRect(e.currentTarget.getBoundingClientRect())} title="구간 목차 (구분선)">
                <ListTree size={15} />
              </button>
            )}
            <button className="tool-btn" onClick={(e) => setListRect(e.currentTarget.getBoundingClientRect())} title="댓글·메모 목록, 노트로 내보내기">
              <MessageSquare size={15} />
              <span>댓글 {anns.filter((a) => a.kind === 'comment').length}</span>
            </button>
            <button className={`tool-btn ${showMarks ? '' : 'off'}`} onClick={(e) => setViewRect(e.currentTarget.getBoundingClientRect())} title="보기 설정">
              <MoreHorizontal size={15} />
            </button>
          </>
        )}
      </header>

      <div className="pane-scroll" ref={scrollRef}>
        {tab === 'live' && (
          <>
            {meeting.source === 'upload' && live && live.units.length === 0 ? (
              <ProcessingCard meeting={meeting} job={job} />
            ) : (
              <>
                {jobActive && <ProcessingCard meeting={meeting} job={job} compact />}
                {live && live.units.length === 0 && !partial ? (
                  <div className="empty-state">
                    {recordingHere ? (
                      <>
                        <div className="listening"><span /><span /><span /></div>
                        <h3>듣고 있어요</h3>
                        <p className="muted">말하면 1~2초 안에 여기에 적혀요. 틀린 글자는 문장에 마우스를 올려 연필을 누르거나 E로 바로 고쳐요.</p>
                      </>
                    ) : (
                      <>
                        <Mic size={24} />
                        <h3>녹음을 시작하면 실시간으로 받아 적어요</h3>
                        <p className="muted">아래 빨간 버튼을 누르거나, 이미 녹음한 파일은 왼쪽에서 올릴 수 있어요.</p>
                      </>
                    )}
                  </div>
                ) : live ? (
                  <TranscriptView meetingId={meeting.id} units={live.units} annotations={live.annotations} partial={partial} recording={recordingHere} scrollRef={scrollRef} />
                ) : null}
              </>
            )}
          </>
        )}

        {tab === 'summary' && (
          <Suspense fallback={null}>
            <SummaryView meeting={meeting} />
          </Suspense>
        )}
      </div>

      {showJump && (
        <button
          className="jump-pill"
          onClick={() => {
            userScrollAt.current = 0 // 이제부터의 스크롤은 우리가 움직이는 것
            set({ follow: true })
            if (tab === 'live' && recordingHere) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
          }}
        >
          <ArrowDown size={14} />
          {newCount > 0 ? `새 문장 ${newCount}개` : '재생 위치로'}
        </button>
      )}
      {tab === 'live' && recordingHere && (
        <div className={`memo-bar mode-${barMode}`}>
          <div className="bar-mode">
            <button className={barMode === 'comment' ? 'on' : ''} onClick={() => setBarMode('comment')} title="마지막 문장에 댓글">
              <MessageSquarePlus size={14} />댓글
            </button>
            <button className={barMode === 'memo' ? 'on' : ''} onClick={() => setBarMode('memo')} title="지금 시각에 메모">
              <StickyNote size={14} />메모
            </button>
          </div>
          <MemoInput
            inputRef={memoInput}
            mode={barMode}
            value={memo}
            onChange={(e) => setMemo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) addFromBar()
              if (e.key === 'Tab') {
                e.preventDefault()
                setBarMode((m) => (m === 'comment' ? 'memo' : 'comment'))
              }
            }}
          />
          {toNotes && <span className="bar-notes" title="노트에도 함께 추가돼요"><NotebookPen size={13} /></span>}
        </div>
      )}
      {viewRect && <ViewMenu rect={viewRect} onClose={() => setViewRect(null)} hasClock={(meeting.clock?.length || 0) > 0} />}
      {tocRect && <Toc anns={anns} units={viewUnits} rect={tocRect} onClose={() => setTocRect(null)} />}
      {listRect && <AnnList anns={anns} rect={listRect} meetingId={meeting.id} onClose={() => setListRect(null)} />}
    </section>
  )
}
