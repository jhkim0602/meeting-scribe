// 첨삭(댓글·메모·구분선…)을 바꾸고 결과를 화면에 바로 반영한다.
// 서버가 바뀐 항목의 두 화면 위치를 함께 돌려주므로 긴 기록 전체를 다시 받지 않는다.
import { api, type Annotation } from './api'
import { useStore } from './store'

const st = () => useStore.getState()

export const annActions = {
  add: async (meetingId: string, body: Record<string, unknown>): Promise<Annotation> => {
    const a = await api.addAnnotation(meetingId, body)
    st().upsertAnnotation(a)
    return a
  },
  patch: async (annId: string, patch: Parameters<typeof api.patchAnnotation>[1], view = 'live'): Promise<Annotation> => {
    const a = await api.patchAnnotation(annId, patch, view)
    st().upsertAnnotation(a)
    return a
  },
  reply: async (annId: string, text: string, view = 'live'): Promise<Annotation> => {
    const a = await api.reply(annId, text, view)
    st().upsertAnnotation(a)
    return a
  },
  remove: async (annId: string): Promise<void> => {
    await api.deleteAnnotation(annId)
    st().removeAnnotation(annId)
  },
  markExported: async (meetingId: string, ids: string[], exported = true): Promise<void> => {
    const r = await api.markExported(meetingId, ids, exported)
    st().stampExported(r.exported_at)
  },
}
