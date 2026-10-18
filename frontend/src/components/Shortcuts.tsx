import { X } from 'lucide-react'
import { useEffect } from 'react'
import { pauseRecording, resumeRecording } from '../hooks/recorder'
import { bridges, currentClock, useStore } from '../lib/store'

const isMac = /Mac|iPhone|iPad/.test(navigator.platform)
const MOD = isMac ? '⌘' : 'Ctrl'

const LIST: { keys: string[]; label: string }[] = [
  { keys: [MOD, '⇧', 'P'], label: '녹음 일시정지 / 이어하기' },
  { keys: ['E'], label: '문장 그 자리에서 고치기 (마우스를 올린 문장, 녹음 중이면 방금 문장 · Enter 저장, Esc 취소)' },
  { keys: ['C'], label: '고른 글자에 댓글 (녹음 중 선택이 없으면 방금 문장에)' },
  { keys: ['M'], label: '지금 시각에 메모' },
  { keys: ['1', '…', '9'], label: '문장의 화자를 참석자로 (마우스를 올린 문장, 녹음 중이면 방금 문장 · 같은 목소리도 함께)' },
  { keys: ['⇧', '1…9'], label: '그 문장만 화자 정하기' },
  { keys: [MOD, 'F'], label: '기록에서 찾기 (한 번 더 누르면 브라우저 찾기)' },
  { keys: ['Esc'], label: '창 닫기' },
  { keys: ['?'], label: '단축키 보기' },
]

function typing(el: EventTarget | null): boolean {
  const t = el as HTMLElement | null
  if (!t) return false
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)
}

/** 앱 전체 단축키 */
export function useGlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStore.getState()
      const k = e.key.toLowerCase()
      const recHere = st.rec.status !== 'idle' && st.rec.meetingId === st.selectedId

      // ⌘⇧P: 녹음 일시정지 / 이어하기 (어디서든)
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && !e.altKey && k === 'p') {
        if (st.rec.status === 'recording') pauseRecording()
        else if (st.rec.status === 'paused') resumeRecording()
        else return
        e.preventDefault()
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || e.isComposing) return

      if (e.key === '?') {
        e.preventDefault()
        st.set({ shortcutsOpen: !st.shortcutsOpen })
        return
      }
      if (!st.selectedId) return
      const hasSelection = !(window.getSelection()?.isCollapsed ?? true)
      if (k === 'c' && !hasSelection && recHere && st.tab === 'live' && bridges.memoBar) {
        e.preventDefault()
        bridges.memoBar.focus('comment')
      } else if (k === 'm' && !hasSelection) {
        if (recHere && st.tab === 'live' && bridges.memoBar) {
          e.preventDefault()
          bridges.memoBar.focus('memo')
        } else if (!recHere && st.tab !== 'summary' && bridges.transcript) {
          e.preventDefault()
          bridges.transcript.memoAt(currentClock())
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
}

export function ShortcutsOverlay() {
  const open = useStore((s) => s.shortcutsOpen)
  const set = useStore((s) => s.set)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && set({ shortcutsOpen: false })
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, set])
  if (!open) return null
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && set({ shortcutsOpen: false })}>
      <div className="modal shortcuts" role="dialog" aria-label="단축키">
        <div className="modal-head">
          <h2>단축키</h2>
          <button className="icon-btn" onClick={() => set({ shortcutsOpen: false })}><X size={16} /></button>
        </div>
        <div className="modal-body">
          {LIST.map((s) => (
            <div key={s.label} className="shortcut-row">
              <span>{s.label}</span>
              <span className="keys">{s.keys.map((k) => <kbd key={k}>{k}</kbd>)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
