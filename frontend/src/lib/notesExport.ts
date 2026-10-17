// 댓글·메모 → 노트 블록. 노트에서는 "시각 + 댓글"이 한 줄, 그 아래에 원문 인용과 답글이 들어간다.
import { annActions } from './annActions'
import { type Annotation } from './api'
import type { View } from './model'
import { bridges, useStore } from './store'

function round(t: number) {
  return Math.round(t * 10) / 10
}

export function quoteOf(a: Annotation, view: View): string {
  if (view === 'final' && a.pos && 'text' in a.pos && a.pos.text) return a.pos.text
  return a.anchor.quote || ''
}

export function annotationTime(a: Annotation): number {
  return a.anchor.t0 ?? a.anchor.t ?? 0
}

function blockFor(a: Annotation, view: View): unknown {
  const t = round(annotationTime(a))
  const children: unknown[] = []
  const quote = quoteOf(a, view)
  if (a.kind === 'comment' && quote) children.push({ type: 'quote', content: quote })
  for (const r of a.replies || []) children.push({ type: 'bulletListItem', content: `답글: ${r.text}` })
  const label = a.kind === 'memo' ? '메모 ' : ''
  return {
    type: 'bulletListItem',
    content: [
      { type: 'timestamp', props: { t } },
      { type: 'text', text: ` ${label}`, styles: {} },
      { type: 'text', text: a.body || quote, styles: { bold: true } },
    ],
    children,
  }
}

/** 댓글/메모를 노트 끝에 넣고 '노트에 있음'으로 표시한다. 넣은 개수를 돌려준다. */
export async function sendToNotes(meetingId: string, anns: Annotation[], view: View, opts: { heading?: string } = {}): Promise<number> {
  const st = useStore.getState()
  const items = [...anns].filter((a) => a.kind === 'comment' || a.kind === 'memo').sort((a, b) => annotationTime(a) - annotationTime(b))
  if (!items.length) return 0
  if (!bridges.notes) {
    st.toast('노트를 불러오는 중이에요. 잠시 후 다시 시도해 주세요.', 'error')
    return 0
  }
  const blocks: unknown[] = []
  if (opts.heading) blocks.push({ type: 'heading', props: { level: 3 }, content: opts.heading })
  blocks.push(...items.map((a) => blockFor(a, view)))
  bridges.notes.insertBlocks(blocks)
  try {
    await annActions.markExported(meetingId, items.map((a) => a.id))
  } catch {
    /* 표시만 실패해도 노트에는 들어갔다 */
  }
  return items.length
}
