// 댓글·메모·강조·구분선을 바꾸고 결과를 화면에 바로 반영한다.
// 서버가 바뀐 항목의 두 화면 위치를 함께 돌려주므로 긴 기록 전체를 다시 받지 않는다.
// 지우기는 확인창 대신 '되돌리기' 알림을 띄우고, 알림이 사라질 때 서버에서 지운다.
import { api, type Annotation } from './api'
import { useStore } from './store'

const st = () => useStore.getState()
const UNDO_MS = 6000
const KIND_LABEL: Record<string, string> = { comment: '댓글을', memo: '메모를', divider: '구분선을', edit: '수정을', highlight: '강조를' }

// 되돌리기를 기다리는 지우기. 탭을 닫으면 바로 보낸다.
const pending = new Map<string, { url: string; timer: number }>()

export function flushPendingDeletes() {
  for (const [key, p] of pending) {
    window.clearTimeout(p.timer)
    fetch(p.url, { method: 'DELETE', keepalive: true }).catch(() => undefined)
    pending.delete(key)
  }
}
window.addEventListener('pagehide', flushPendingDeletes)

/** 화면에서 먼저 지우고, 되돌리기 시간이 지나면 서버에서 지운다. */
export function deleteLater(key: string, url: string, label: string, onUndo: () => void, onFail: (e: Error) => void, ms = UNDO_MS, onDone?: () => void) {
  const timer = window.setTimeout(async () => {
    pending.delete(key)
    try {
      const res = await fetch(url, { method: 'DELETE' })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `${res.status}`)
      onDone?.()
    } catch (e) {
      onFail(e as Error)
    }
  }, ms)
  pending.set(key, { url, timer })
  st().toast(`${label} 지웠어요`.trim(), 'info', {
    label: '되돌리기',
    run: () => {
      const p = pending.get(key)
      if (!p) return
      window.clearTimeout(p.timer)
      pending.delete(key)
      onUndo()
    },
  }, ms)
}

export const annActions = {
  add: async (meetingId: string, body: Record<string, unknown>): Promise<Annotation> => {
    const a = await api.addAnnotation(meetingId, body)
    st().upsertAnnotation(a)
    return a
  },
  patch: async (annId: string, patch: Parameters<typeof api.patchAnnotation>[1]): Promise<Annotation> => {
    const a = await api.patchAnnotation(annId, patch)
    st().upsertAnnotation(a)
    return a
  },
  reply: async (annId: string, text: string): Promise<Annotation> => {
    const a = await api.reply(annId, text)
    st().upsertAnnotation(a)
    return a
  },
  remove: async (annId: string): Promise<void> => {
    const snap = st().takeAnnotation(annId)
    const kind = snap.live?.item.kind || ''
    deleteLater(
      `ann:${annId}`,
      `/api/annotations/${annId}`,
      KIND_LABEL[kind] || '',
      () => st().restoreAnnotation(snap),
      (e) => {
        st().restoreAnnotation(snap)
        st().toast(`지우지 못했어요: ${e.message}`, 'error')
      },
    )
  },
  markExported: async (meetingId: string, ids: string[], exported = true): Promise<void> => {
    const r = await api.markExported(meetingId, ids, exported)
    st().stampExported(r.exported_at)
  },
}
