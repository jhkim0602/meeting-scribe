// 마이크 → AudioWorklet(16 kHz PCM) → WebSocket. 서버는 파일에 저장하고 실시간 전사를 돌려준다.
import { api, type Job, type Unit } from '../lib/api'
import { useStore } from '../lib/store'

interface Session {
  ws: WebSocket
  ctx: AudioContext
  stream: MediaStream
  node: AudioWorkletNode
  sent: number
  stopping: boolean
  resolveStop?: (job: Job | null) => void
}

let session: Session | null = null

function wsUrl(meetingId: string) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/meetings/${meetingId}/record`
}

export async function listMics(): Promise<MediaDeviceInfo[]> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    return devices.filter((d) => d.kind === 'audioinput')
  } catch {
    return []
  }
}

function cleanup() {
  if (!session) return
  try {
    session.node.port.onmessage = null
    session.node.disconnect()
  } catch {
    /* 이미 해제됨 */
  }
  session.stream.getTracks().forEach((t) => t.stop())
  session.ctx.close().catch(() => undefined)
  session = null
}

export async function startRecording(meetingId: string, deviceId?: string): Promise<void> {
  const st = useStore.getState()
  if (session) {
    st.toast('이미 녹음 중이에요')
    return
  }
  st.setRec({ status: 'connecting', meetingId, level: 0 })
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: true,
      },
    })
  } catch (e) {
    st.setRec({ status: 'idle', meetingId: null })
    st.toast('마이크를 쓸 수 없어요. 브라우저 주소창 왼쪽에서 마이크 권한을 허용해 주세요.', 'error')
    throw e
  }

  const ws = new WebSocket(wsUrl(meetingId))
  ws.binaryType = 'arraybuffer'
  const ready = new Promise<number>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('서버 연결 시간 초과')), 10000)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data as string)
      if (msg.type === 'ready') {
        clearTimeout(timer)
        resolve(msg.offset as number)
      } else if (msg.type === 'error') {
        clearTimeout(timer)
        reject(new Error(msg.message))
      }
    }
    ws.onerror = () => {
      clearTimeout(timer)
      reject(new Error('서버에 연결할 수 없어요'))
    }
  })

  let offset: number
  try {
    offset = await ready
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop())
    ws.close()
    st.setRec({ status: 'idle', meetingId: null })
    st.toast((e as Error).message, 'error')
    throw e
  }

  const ctx = new AudioContext()
  await ctx.audioWorklet.addModule('/pcm-worklet.js')
  const source = ctx.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(ctx, 'pcm-capture')
  const mute = ctx.createGain()
  mute.gain.value = 0
  source.connect(node)
  node.connect(mute).connect(ctx.destination)

  session = { ws, ctx, stream, node, sent: 0, stopping: false }
  const s = session
  let lastLevelUpdate = 0
  node.port.onmessage = (ev) => {
    const { pcm, rms } = ev.data as { pcm: ArrayBuffer | null; rms: number }
    const store = useStore.getState()
    if (pcm && ws.readyState === WebSocket.OPEN && !s.stopping) {
      ws.send(pcm)
      s.sent += pcm.byteLength / 2
    }
    const now = performance.now()
    if (now - lastLevelUpdate > 80) {
      lastLevelUpdate = now
      store.setRec({ level: Math.min(1, rms * 6), elapsed: offset + s.sent / 16000 })
    }
  }

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data as string)
    const store = useStore.getState()
    const onThisMeeting = store.selectedId === meetingId
    if (msg.type === 'segment') {
      if (onThisMeeting) store.addLiveSegment(msg.segment as Unit)
    } else if (msg.type === 'partial') {
      if (onThisMeeting) store.setPartial(msg.text ? { t0: msg.t0, t1: msg.t1, text: msg.text } : null)
    } else if (msg.type === 'paused') {
      store.setRec({ status: 'paused' })
    } else if (msg.type === 'stopped') {
      s.resolveStop?.(msg.job ?? null)
    } else if (msg.type === 'error') {
      store.toast(msg.message, 'error')
    }
  }
  ws.onclose = () => {
    const store = useStore.getState()
    const unexpected = !s.stopping
    cleanup()
    store.setRec({ status: 'idle', meetingId: null, level: 0 })
    store.setPartial(null)
    if (unexpected) store.toast('연결이 끊겨 녹음을 멈췄어요. 녹음된 부분은 저장되어 있고 이어서 녹음할 수 있어요.', 'error')
    store.loadMeetings()
    if (store.selectedId === meetingId) store.refreshMeeting()
    s.resolveStop?.(null)
  }

  st.setRec({ status: 'recording', meetingId, offset, elapsed: offset })
  st.loadMeetings()
  if (st.selectedId === meetingId) st.refreshMeeting()
}

export function pauseRecording() {
  if (!session) return
  session.node.port.postMessage({ type: 'enable', value: false })
  session.ws.send(JSON.stringify({ type: 'pause' }))
}

export function resumeRecording() {
  if (!session) return
  session.node.port.postMessage({ type: 'enable', value: true })
  useStore.getState().setRec({ status: 'recording' })
}

export async function stopRecording(): Promise<Job | null> {
  if (!session) return null
  const s = session
  const store = useStore.getState()
  store.setRec({ status: 'stopping' })
  s.stopping = true
  const done = new Promise<Job | null>((resolve) => {
    s.resolveStop = resolve
  })
  s.ws.send(JSON.stringify({ type: 'stop' }))
  const job = await Promise.race([done, new Promise<null>((r) => setTimeout(() => r(null), 120000))])
  try {
    s.ws.close()
  } catch {
    /* 이미 닫힘 */
  }
  cleanup()
  store.setRec({ status: 'idle', meetingId: null, level: 0 })
  store.setPartial(null)
  await store.loadMeetings()
  if (store.selectedId) {
    await store.refreshMeeting()
    await store.refreshViews()
  }
  return job
}

export function isRecordingActive() {
  return session !== null
}

export async function createAndRecord(deviceId?: string) {
  const st = useStore.getState()
  const m = await api.createMeeting('')
  await st.loadMeetings()
  location.hash = `#/m/${m.id}`
  await st.select(m.id)
  await startRecording(m.id, deviceId)
}
