import { ArrowDownToLine, Eye, EyeOff, Info, ListChecks, Mic, RefreshCw, Sparkles, StickyNote } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Annotation } from '../lib/api'
import { currentClock, useStore, type Tab } from '../lib/store'
import { fmtRelative, fmtTime } from '../lib/time'
import { Floating } from './Floating'
import { ProcessingCard } from './ProcessingCard'
import { SpeakerLabel } from './SpeakerLabel'
import { SummaryView } from './SummaryView'
import { TranscriptView } from './TranscriptView'

const KIND_LABEL = { comment: '댓글', edit: '수정', memo: '메모', highlight: '강조' } as const

function engineLabel(engine: string | null): string {
  if (!engine) return ''
  const [kind, model] = engine.split(':')
  const name = model?.split('/').pop() || ''
  return kind === 'mock' ? '시연용 엔진' : name || kind
}

function AnnList({ anns, onClose, rect }: { anns: Annotation[]; onClose: () => void; rect: DOMRect }) {
  const flash = useStore((s) => s.flash)
  const sorted = [...anns].sort((a, b) => (a.anchor.t0 ?? a.anchor.t ?? 0) - (b.anchor.t0 ?? b.anchor.t ?? 0))
  return (
    <Floating rect={rect} onClose={onClose} className="ann-list">
      <div className="ann-list-head">첨삭 {anns.length}개</div>
      {sorted.length === 0 && <p className="muted pad">아직 없어요. 기록에서 글자를 드래그하면 댓글·수정·강조를 남길 수 있어요.</p>}
      {sorted.map((a) => {
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
            <span className="ann-list-kind">{KIND_LABEL[a.kind]}</span>
            <span className="ann-list-time">{fmtTime(a.anchor.t0 ?? a.anchor.t ?? 0)}</span>
            <span className="ann-list-text">
              {a.kind === 'edit' ? `${a.anchor.quote} → ${a.replacement}` : a.body || a.anchor.quote}
            </span>
          </button>
        )
      })}
    </Floating>
  )
}

export function TranscriptPane() {
  const meeting = useStore((s) => s.meeting)!
  const live = useStore((s) => s.live)
  const final = useStore((s) => s.final)
  const finalLoaded = useStore((s) => s.finalLoaded)
  const job = useStore((s) => s.job)
  const tab = useStore((s) => s.tab)
  const setTab = useStore((s) => s.setTab)
  const partial = useStore((s) => s.partial)
  const rec = useStore((s) => s.rec)
  const showMarks = useStore((s) => s.showMarks)
  const follow = useStore((s) => s.follow)
  const set = useStore((s) => s.set)
  const refreshViews = useStore((s) => s.refreshViews)
  const toast = useStore((s) => s.toast)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [listRect, setListRect] = useState<DOMRect | null>(null)
  const [memo, setMemo] = useState('')
  const recordingHere = rec.status !== 'idle' && rec.meetingId === meeting.id
  const jobRunning = !!job && (job.status === 'queued' || job.status === 'running')

  const anns = tab === 'final' ? final?.annotations || [] : live?.annotations || []
  const movedCount = useMemo(() => (final?.annotations || []).filter((a) => a.origin === 'live' && a.pos).length, [final])

  // 녹음 중에는 새 문장이 오면 아래로 따라간다
  const liveCount = live?.units.length || 0
  useEffect(() => {
    const el = scrollRef.current
    if (!el || tab !== 'live' || !follow) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240
    if (nearBottom || recordingHere) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [liveCount, partial?.text, tab, follow, recordingHere])

  const addMemoNow = async () => {
    const text = memo.trim()
    if (!text) return
    setMemo('')
    const units = live?.units || []
    try {
      await api.addAnnotation(meeting.id, { kind: 'memo', view: 'live', t: currentClock(), after: units.length ? units[units.length - 1].id : null, body: text })
      await refreshViews()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const tabs: { key: Tab; label: string; badge?: string }[] = [
    { key: 'live', label: '실시간 기록', badge: liveCount ? String(liveCount) : undefined },
    { key: 'final', label: '원본', badge: jobRunning ? `${Math.round((job?.progress || 0) * 100)}%` : final ? `v${final.version}` : undefined },
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
            <button className="tool-btn" onClick={(e) => setListRect(e.currentTarget.getBoundingClientRect())} title="첨삭 목록">
              <ListChecks size={15} />
              <span>첨삭 {anns.length}</span>
            </button>
            <button className={`tool-btn ${showMarks ? '' : 'off'}`} onClick={() => set({ showMarks: !showMarks })} title={showMarks ? '첨삭 숨기기' : '첨삭 보기'}>
              {showMarks ? <Eye size={15} /> : <EyeOff size={15} />}
            </button>
            <button className={`tool-btn ${follow ? 'on' : ''}`} onClick={() => set({ follow: !follow })} title="말하는/재생 중인 곳 따라가기">
              <ArrowDownToLine size={15} />
            </button>
          </>
        )}
      </header>

      <div className="pane-scroll" ref={scrollRef}>
        {tab === 'live' && (
          <>
            {live && live.units.length === 0 && !partial ? (
              <div className="empty-state">
                {recordingHere ? (
                  <>
                    <div className="listening"><span /><span /><span /></div>
                    <h3>듣고 있어요</h3>
                    <p className="muted">말하면 1~2초 안에 여기에 적혀요. 적힌 글자를 드래그하면 댓글·수정·강조를 남길 수 있어요.</p>
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
              <TranscriptView view="live" meetingId={meeting.id} units={live.units} annotations={live.annotations} partial={partial} recording={recordingHere} />
            ) : null}
          </>
        )}

        {tab === 'final' && finalLoaded && (
          <>
            {final ? (
              <>
                {jobRunning && <ProcessingCard meeting={meeting} job={job} compact />}
                {!jobRunning && meeting.status === 'recorded' && (
                  <div className="proc-banner info">
                    <RefreshCw size={14} />
                    <span>원본을 만든 뒤에 녹음이 더 추가되었어요.</span>
                    <button className="btn btn-sm" onClick={async () => { await api.process(meeting.id); useStore.getState().refreshMeeting() }}>원본 다시 만들기</button>
                  </div>
                )}
                <div className="final-meta">
                  <div className="speakers-legend">
                    {final.speakers.length ? final.speakers.map((sp) => <SpeakerLabel key={sp} label={sp} compact />) : <span className="muted">화자 구분 없음</span>}
                  </div>
                  <span className="spacer" />
                  <span className="meta-text" title={`${final.engine} · ${final.diarizer}`}>
                    <Sparkles size={12} />
                    {engineLabel(final.engine)} · v{final.version} · {fmtRelative(final.created_at)}
                  </span>
                </div>
                {movedCount > 0 && showMarks && (
                  <div className="moved-note">
                    <Info size={14} />
                    회의 중 남긴 메모·댓글·수정 {movedCount}개를 원본의 같은 자리에 옮겼어요. 빨간 글씨는 내가 고친 부분이에요.
                  </div>
                )}
                <TranscriptView view="final" meetingId={meeting.id} units={final.units} annotations={final.annotations} />
              </>
            ) : (
              <ProcessingCard meeting={meeting} job={job} />
            )}
          </>
        )}

        {tab === 'summary' && <SummaryView meeting={meeting} />}
      </div>

      {tab === 'live' && recordingHere && (
        <div className="memo-bar">
          <StickyNote size={15} />
          <input
            value={memo}
            placeholder={`${fmtTime(rec.elapsed)} 지금 이 순간에 메모… (Enter)`}
            onChange={(e) => setMemo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) addMemoNow()
            }}
          />
        </div>
      )}
      {listRect && <AnnList anns={anns} rect={listRect} onClose={() => setListRect(null)} />}
    </section>
  )
}
