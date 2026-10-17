// 마이크 → AudioWorklet(16 kHz PCM) → WebSocket. 서버는 파일에 저장하고 실시간 전사를 돌려준다.
//
// 회의 중 생기는 예외 처리:
// - 일시정지: 소리를 보내지 않지만 마이크는 계속 듣는다. 말소리가 들리면 "녹음이 일시정지된 상태예요" 알림.
// - 오래 조용함: 설정한 시간(기본 3분) 동안 말소리가 없으면 "쉬는 시간인가요?" → 일시정지하면 조용해진 시점부터 쉬는 시간.
// - 마이크 끊김: 트랙이 끝나면 같은 마이크(없으면 기본 마이크)로 다시 연결. 실패하면 일시정지.
// - 마이크 무음: 소리가 완전히 0으로 3초 넘게 들어오면 경고 (마이크 꺼짐/다른 앱 점유).
// - 서버 연결 끊김: 소리를 브라우저에 모아 두고 다시 연결되면 이어서 보낸다 (최대 30분).
// - 노트북 잠자기: 깨어나면 오디오를 다시 켜고, 잠든 동안을 쉬는 시간으로 남긴다.
import { api, type Job, type Unit } from '../lib/api'
import { useStore } from '../lib/store'

const SR = 16000
const MAX_BUFFER_SAMPLES = SR * 60 * 30
const MIN_TICK_GAP_FOR_SLEEP = 4000

class SpeechMeter {
  floor = 0.004
  win: boolean[] = []
  lastSpeechAt = Date.now()
  lastSpeechT = 0
  update(rms: number): boolean {
    if (rms > 0) {
      if (rms < this.floor) this.floor = this.floor * 0.7 + rms * 0.3
      else this.floor = Math.min(this.floor * 1.0015, 0.05)
    }
    this.win.push(rms > Math.max(0.015, this.floor * 3.5))
    if (this.win.length > 15) this.win.shift()
    return this.win.filter(Boolean).length >= 6
  }
}

interface Session {
  meetingId: string
  deviceId?: string
  ws: WebSocket | null
  ctx: AudioContext
  stream: MediaStream
  source: MediaStreamAudioSourceNode
  node: AudioWorkletNode
  offset: number
  sent: number
  buffer: ArrayBuffer[]
  buffered: number
  overflow: boolean
  paused: boolean
  stopping: boolean
  reconnecting: boolean
  lastSendWall: number
  slept: boolean
  lastTick: number
  tick: number
  meter: SpeechMeter
  silencePrompted: boolean
  lastPausedAlert: number
  zeroMs: number
  micRetrying: boolean
  resolveStop?: (job: Job | null) => void
}

let session: Session | null = null

function store() {
  return useStore.getState()
}

function elapsed(s: Session) {
  return s.offset + (s.sent + s.buffered) / SR
}

function setAlert(alert: ReturnType<typeof store>['rec']['alert']) {
  store().setRec({ alert })
}

function clearAlert(kind: string) {
  if (store().rec.alert?.kind === kind) setAlert(null)
}

function wsUrl(meetingId: string, query: string) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/meetings/${meetingId}/record${query}`
}

export async function listMics(): Promise<MediaDeviceInfo[]> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    return devices.filter((d) => d.kind === 'audioinput')
  } catch {
    return []
  }
}

function getMic(deviceId?: string) {
  return navigator.mediaDevices.getUserMedia({
    audio: {
      deviceId: deviceId ? { exact: deviceId } : undefined,
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: true,
    },
  })
}

function cleanup() {
  const s = session
  if (!s) return
  window.clearInterval(s.tick)
  try {
    s.node.port.onmessage = null
    s.node.disconnect()
    s.source.disconnect()
  } catch {
    /* 이미 해제됨 */
  }
  s.stream.getTracks().forEach((t) => t.stop())
  s.ctx.close().catch(() => undefined)
  session = null
  document.title = '회의 노트'
}

function refreshLiveSoon(meetingId: string) {
  setTimeout(() => {
    const st = store()
    if (st.selectedId === meetingId) st.refreshLive()
  }, 300)
}

interface Ready {
  offset: number
  divider: unknown
  cancelled_job: boolean
}

/** WebSocket을 열고 'ready'를 기다린다. 이후 메시지와 끊김 처리를 연결한다. */
function openSocket(s: Session, query: string): Promise<Ready> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl(s.meetingId, query))
    ws.binaryType = 'arraybuffer'
    let ready = false
    const timer = setTimeout(() => {
      if (!ready) {
        ws.close()
        reject(new Error('서버 연결 시간 초과'))
      }
    }, 12000)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data as string)
      const st = store()
      const here = st.selectedId === s.meetingId
      switch (msg.type) {
        case 'ready':
          ready = true
          clearTimeout(timer)
          s.ws = ws
          resolve(msg as Ready)
          break
        case 'segment':
          if (here) st.addLiveSegment(msg.segment as Unit)
          break
        case 'partial':
          if (here) st.setPartial(msg.text ? { t0: msg.t0, t1: msg.t1, text: msg.text } : null)
          break
        case 'resumed':
          if (msg.divider) refreshLiveSoon(s.meetingId)
          break
        case 'stopped':
          s.resolveStop?.(msg.job ?? null)
          break
        case 'error':
          if (!ready) {
            clearTimeout(timer)
            reject(new Error(msg.message))
          } else st.toast(msg.message, 'error')
          break
      }
    }
    ws.onerror = () => {
      if (!ready) {
        clearTimeout(timer)
        reject(new Error('서버에 연결할 수 없어요'))
      }
    }
    ws.onclose = () => {
      if (!ready) return
      if (session !== s) return
      if (s.ws === ws) s.ws = null
      if (s.stopping) {
        s.resolveStop?.(null)
        return
      }
      startReconnect(s)
    }
  })
}

function afterReady(s: Session, r: Ready) {
  s.offset = r.offset
  s.sent = 0
  if (r.divider) refreshLiveSoon(s.meetingId)
  if (r.cancelled_job) store().toast('원본 만들기를 멈추고 이어서 녹음해요. 녹음을 마치면 처음부터 다시 만들어요.')
}

function flushBuffer(s: Session) {
  if (!s.ws || s.ws.readyState !== WebSocket.OPEN) return
  for (const chunk of s.buffer) {
    s.ws.send(chunk)
    s.sent += chunk.byteLength / 2
  }
  s.buffer = []
  s.buffered = 0
  s.overflow = false
  s.lastSendWall = Date.now()
}

async function startReconnect(s: Session) {
  if (s.reconnecting) return
  s.reconnecting = true
  const since = s.lastSendWall
  setAlert({ kind: 'server-lost', since })
  for (let attempt = 0; attempt < 120 && session === s && !s.stopping; attempt++) {
    await new Promise((r) => setTimeout(r, attempt < 3 ? 1000 * (attempt + 1) : 5000))
    if (session !== s || s.stopping) return
    const params = new URLSearchParams()
    if (!s.overflow && !s.slept) params.set('continuous', '1')
    else params.set('since', String(since / 1000))
    if (s.paused) params.set('paused', '1')
    try {
      const r = await openSocket(s, `?${params}`)
      afterReady(s, r)
      flushBuffer(s)
      s.reconnecting = false
      s.slept = false
      clearAlert('server-lost')
      store().toast('서버에 다시 연결했어요. 끊긴 동안의 소리도 저장했어요.', 'success')
      return
    } catch {
      /* 다시 시도 */
    }
  }
  if (session === s) {
    s.reconnecting = false
    cleanup()
    const st = store()
    st.setRec({ status: 'idle', meetingId: null, level: 0, pausedAt: null, alert: null })
    st.toast('서버에 다시 연결하지 못했어요. 녹음된 부분은 저장되어 있고 이어서 녹음할 수 있어요.', 'error')
    st.loadMeetings()
  }
}

function onChunk(s: Session, pcm: ArrayBuffer | null, rms: number) {
  const st = store()
  const now = Date.now()
  const speaking = s.meter.update(rms)

  // 완전한 0이 이어지면 마이크가 소리를 주지 않는 것
  if (rms === 0) s.zeroMs += 100
  else {
    if (s.zeroMs >= 3000) clearAlert('mic-silent')
    s.zeroMs = 0
  }
  if (!s.paused && s.zeroMs >= 3000 && st.rec.alert?.kind !== 'mic-silent' && !s.micRetrying) setAlert({ kind: 'mic-silent' })

  if (s.paused) {
    if (speaking && st.rec.pausedAt && now - st.rec.pausedAt > 3000 && now - s.lastPausedAlert > 60000) {
      s.lastPausedAlert = now
      setAlert({ kind: 'paused-speech' })
    }
  } else if (speaking) {
    s.meter.lastSpeechAt = now
    s.meter.lastSpeechT = elapsed(s)
    if (s.silencePrompted) {
      s.silencePrompted = false
      clearAlert('silence')
    }
  }

  if (pcm && !s.paused && !s.stopping) {
    if (s.ws && s.ws.readyState === WebSocket.OPEN && !s.reconnecting) {
      s.ws.send(pcm)
      s.sent += pcm.byteLength / 2
      s.lastSendWall = now
    } else if (s.buffered < MAX_BUFFER_SAMPLES) {
      s.buffer.push(pcm)
      s.buffered += pcm.byteLength / 2
    } else {
      s.overflow = true
    }
  }
}

function onTick(s: Session) {
  const now = Date.now()
  const st = store()
  // 노트북 잠자기: 타이머가 몇 초 이상 멈췄다가 다시 돈다
  if (now - s.lastTick > MIN_TICK_GAP_FOR_SLEEP) {
    const sleptFrom = s.lastTick
    const tAtSleep = elapsed(s)
    if (s.ctx.state === 'suspended') s.ctx.resume().catch(() => undefined)
    if (s.ws && s.ws.readyState === WebSocket.OPEN && !s.paused) {
      // 잠든 동안은 녹음되지 않았으니 그 구간을 쉬는 시간으로 남긴다
      s.ws.send(JSON.stringify({ type: 'pause', from_t: tAtSleep, from_wall: sleptFrom / 1000 }))
      s.ws.send(JSON.stringify({ type: 'resume' }))
    } else {
      s.slept = true
    }
    s.meter.lastSpeechAt = now
  }
  s.lastTick = now

  // 오래 조용하면 쉬는 시간인지 묻는다
  const minutes = st.settings?.break_prompt_minutes ?? 3
  if (!s.paused && minutes > 0 && !s.silencePrompted && now - s.meter.lastSpeechAt > minutes * 60000) {
    s.silencePrompted = true
    setAlert({ kind: 'silence', fromT: s.meter.lastSpeechT, fromWall: s.meter.lastSpeechAt / 1000, minutes })
  }
  if (st.rec.status === 'recording') document.title = '녹음 중 · 회의 노트'
  else if (st.rec.status === 'paused') document.title = '일시정지 · 회의 노트'
}

function attachTrack(s: Session) {
  const track = s.stream.getAudioTracks()[0]
  track?.addEventListener('ended', () => {
    if (session === s && !s.stopping) reconnectMic()
  })
}

/** 마이크를 다시 연결한다 (끊김, 무음 경고, 사용자가 '다시 연결'을 누를 때). */
export async function reconnectMic(): Promise<boolean> {
  const s = session
  if (!s || s.micRetrying) return false
  s.micRetrying = true
  setAlert({ kind: 'mic-lost', state: 'reconnecting' })
  const st = store()
  for (let i = 0; i < 3; i++) {
    if (session !== s) return false
    try {
      let stream: MediaStream
      let fallback = false
      try {
        stream = await getMic(s.deviceId)
      } catch {
        stream = await getMic(undefined)
        fallback = !!s.deviceId
      }
      try {
        s.source.disconnect()
      } catch {
        /* 이미 해제됨 */
      }
      s.stream.getTracks().forEach((t) => t.stop())
      s.stream = stream
      s.source = s.ctx.createMediaStreamSource(stream)
      s.source.connect(s.node)
      if (s.ctx.state === 'suspended') await s.ctx.resume()
      attachTrack(s)
      s.zeroMs = 0
      s.micRetrying = false
      setAlert(null)
      st.toast(fallback ? '원래 마이크를 찾을 수 없어 기본 마이크로 다시 연결했어요.' : '마이크를 다시 연결했어요.', 'success')
      return true
    } catch {
      await new Promise((r) => setTimeout(r, 1000))
    }
  }
  s.micRetrying = false
  if (!s.paused) pauseRecording()
  setAlert({ kind: 'mic-lost', state: 'failed' })
  return false
}

export async function startRecording(meetingId: string, deviceId?: string): Promise<void> {
  const st = store()
  if (session) {
    st.toast('이미 녹음 중이에요')
    return
  }
  st.setRec({ status: 'connecting', meetingId, level: 0, alert: null, pausedAt: null })
  let stream: MediaStream
  try {
    stream = await getMic(deviceId)
  } catch (e) {
    st.setRec({ status: 'idle', meetingId: null })
    st.toast('마이크를 쓸 수 없어요. 브라우저 주소창 왼쪽에서 마이크 권한을 허용해 주세요.', 'error')
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

  const s: Session = {
    meetingId, deviceId, ws: null, ctx, stream, source, node, offset: 0, sent: 0, buffer: [], buffered: 0, overflow: false,
    paused: false, stopping: false, reconnecting: false, lastSendWall: Date.now(), slept: false, lastTick: Date.now(), tick: 0,
    meter: new SpeechMeter(), silencePrompted: false, lastPausedAlert: 0, zeroMs: 0, micRetrying: false,
  }
  session = s
  let r: Ready
  try {
    r = await openSocket(s, '')
  } catch (e) {
    cleanup()
    st.setRec({ status: 'idle', meetingId: null })
    st.toast((e as Error).message, 'error')
    throw e
  }
  afterReady(s, r)
  s.meter.lastSpeechT = s.offset
  attachTrack(s)
  let lastLevel = 0
  node.port.onmessage = (ev) => {
    const { pcm, rms } = ev.data as { pcm: ArrayBuffer | null; rms: number }
    onChunk(s, pcm, rms)
    const now = performance.now()
    if (now - lastLevel > 80) {
      lastLevel = now
      store().setRec({ level: Math.min(1, rms * 6), elapsed: elapsed(s) })
    }
  }
  s.tick = window.setInterval(() => onTick(s), 1000)
  st.setRec({ status: 'recording', meetingId, offset: s.offset, elapsed: s.offset })
  st.loadMeetings()
  if (st.selectedId === meetingId) st.refreshMeeting()
}

/** 일시정지. fromT/fromWall을 주면 그 시점(조용해진 때)부터 쉬는 시간으로 본다. */
export function pauseRecording(opts?: { fromT?: number; fromWall?: number }) {
  const s = session
  if (!s || s.paused) return
  s.paused = true
  s.node.port.postMessage({ type: 'enable', value: false })
  if (s.ws && s.ws.readyState === WebSocket.OPEN) {
    s.ws.send(JSON.stringify({ type: 'pause', from_t: opts?.fromT, from_wall: opts?.fromWall }))
  }
  const pausedAt = opts?.fromWall ? opts.fromWall * 1000 : Date.now()
  store().setRec({ status: 'paused', pausedAt, alert: null, level: 0 })
}

export function resumeRecording() {
  const s = session
  if (!s) return
  s.paused = false
  s.silencePrompted = false
  s.meter.lastSpeechAt = Date.now()
  s.meter.lastSpeechT = elapsed(s)
  s.node.port.postMessage({ type: 'enable', value: true })
  if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: 'resume' }))
  store().setRec({ status: 'recording', pausedAt: null, alert: null })
}

export async function stopRecording(): Promise<Job | null> {
  const s = session
  if (!s) return null
  const st = store()
  st.setRec({ status: 'stopping', alert: null })
  s.stopping = true
  let job: Job | null = null
  if (s.ws && s.ws.readyState === WebSocket.OPEN) {
    flushBuffer(s)
    const done = new Promise<Job | null>((resolve) => {
      s.resolveStop = resolve
    })
    s.ws.send(JSON.stringify({ type: 'stop' }))
    job = await Promise.race([done, new Promise<null>((r) => setTimeout(() => r(null), 120000))])
    try {
      s.ws?.close()
    } catch {
      /* 이미 닫힘 */
    }
  }
  const meetingId = s.meetingId
  cleanup()
  st.setRec({ status: 'idle', meetingId: null, level: 0, pausedAt: null, alert: null })
  st.setPartial(null)
  await st.loadMeetings()
  if (st.selectedId) {
    await st.refreshMeeting()
    await st.refreshViews()
  }
  // 실수로 끝냈을 때 바로 되돌릴 수 있게
  st.toast(
    job ? '녹음을 마쳤어요. 원본을 만들기 시작했어요.' : '녹음을 마쳤어요.',
    'success',
    { label: '이어서 녹음', run: () => startRecording(meetingId, s.deviceId).catch(() => undefined) },
    10000,
  )
  return job
}

export function isRecordingActive() {
  return session !== null
}

export async function createAndRecord(deviceId?: string) {
  const st = store()
  const m = await api.createMeeting('')
  await st.loadMeetings()
  location.hash = `#/m/${m.id}`
  await st.select(m.id)
  await startRecording(m.id, deviceId)
}

// 개발/E2E용: 마이크 끊김, 서버 끊김을 흉내 낸다
;(window as unknown as { __scribeDebug: unknown }).__scribeDebug = {
  endTrack: () => session?.stream.getAudioTracks()[0]?.dispatchEvent(new Event('ended')),
  dropSocket: () => session?.ws?.close(),
  state: () => (session ? { paused: session.paused, buffered: session.buffered, sent: session.sent, offset: session.offset, reconnecting: session.reconnecting } : null),
}
