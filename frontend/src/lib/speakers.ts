// 실시간 임시 화자: 참여자 고르기. 같은 목소리 문장에도 바로 반영하고, "이 문장만"으로 되돌릴 수 있다.
import { api, type Participant, type SpeakerChange, type Unit } from './api'
import { getPref, setPref } from './prefs'
import { useStore } from './store'

/** 같은 목소리 문장에도 함께 반영할지 (기억) */
export const SPEAKER_SCOPE_VOICE = 'speakerScopeVoice'

export function participantIndex(participants: Participant[], pid: string | null | undefined): number {
  const i = participants.findIndex((p) => p.id === pid)
  return i < 0 ? 0 : (i % 8) + 1
}

export function voiceScope(): boolean {
  return getPref(SPEAKER_SCOPE_VOICE, true)
}

export function setVoiceScope(v: boolean) {
  setPref(SPEAKER_SCOPE_VOICE, v)
}

function snapshot(u: Unit): SpeakerChange {
  return { id: u.id, speaker: u.speaker ?? null, speaker_auto: u.speaker_auto ?? null, speaker_src: u.speaker_src ?? null, speaker_conf: u.speaker_conf ?? null }
}

/** 문장들의 화자를 고른다. scope 생략 시 기억해 둔 설정(기본: 같은 목소리도 함께). */
export async function assignSpeaker(meetingId: string, unitIds: string[], pid: string | null, scope?: 'voice' | 'one') {
  const st = useStore.getState()
  const units = st.live?.units || []
  const before = new Map(units.map((u) => [u.id, snapshot(u)]))
  const sc = scope ?? (voiceScope() ? 'voice' : 'one')
  try {
    const r = await api.assignSpeaker(meetingId, unitIds, pid, sc)
    st.applySpeakerChanges(r.changes)
    const picked = new Set(unitIds)
    const others = r.changes.filter((c) => !picked.has(c.id))
    if (others.length) {
      const name = st.meeting?.participants.find((p) => p.id === pid)?.name || '화자 없음'
      st.toast(`같은 목소리 ${others.length}문장도 ${name}(으)로 바꿨어요`, 'info', {
        label: '이 문장만',
        run: async () => {
          const rows = others.map((c) => before.get(c.id)).filter((x): x is SpeakerChange => !!x)
          const back = await api.restoreSpeakers(meetingId, rows, r.previous_map)
          useStore.getState().applySpeakerChanges(back.changes)
        },
      }, 6000)
    }
  } catch (e) {
    st.toast((e as Error).message, 'error')
  }
}

/** 새 참여자를 더하고 그 참여자를 돌려준다 */
export async function addParticipant(meetingId: string, name: string): Promise<Participant | null> {
  const st = useStore.getState()
  const cur = st.meeting?.participants || []
  const v = name.trim()
  if (!v) return null
  const exists = cur.find((p) => p.name === v)
  if (exists) return exists
  const r = await api.setParticipants(meetingId, [...cur, { name: v }])
  setParticipantsLocal(r.participants)
  return r.participants.find((p) => p.name === v) || null
}

export async function removeParticipant(meetingId: string, pid: string) {
  const cur = useStore.getState().meeting?.participants || []
  const r = await api.setParticipants(meetingId, cur.filter((p) => p.id !== pid))
  setParticipantsLocal(r.participants)
}

export function setParticipantsLocal(participants: Participant[]) {
  const m = useStore.getState().meeting
  if (m) useStore.setState({ meeting: { ...m, participants } })
}
