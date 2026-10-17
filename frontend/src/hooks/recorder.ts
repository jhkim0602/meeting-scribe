// 마이크 → AudioWorklet(16 kHz PCM) → WebSocket. 서버는 파일에 저장하고 실시간 전사를 돌려준다.
//
// 알림은 '조용한 비서'처럼 세 단계로만 한다. 소리는 내지 않는다.
// 1) 아이콘 색: 잠깐의 문제(마이크 다시 연결 중, 서버 재연결 15초 미만, 마이크 음소거·무음 30초)는
//    녹음 표시줄 아이콘만 주황색으로 바뀐다.
// 2) 한 번 띄우는 알림: '쉬는 시간인가요?', 일시정지 중 말소리 → 15초 뒤 표시줄로 접힌다.
// 3) 계속 보이는 알림: 마이크 재연결 실패, 서버 15초 넘게 끊김, 무음 60초, 저장 공간 부족 → 해결될 때까지.
//
// 회의 중 생기는 예외 처리:
// - 일시정지: 소리를 보내지 않지만 마이크는 계속 듣는다. 30초가 지난 뒤 말소리가 들리면 한 번만 알린다.
// - 오래 조용함: 설정한 시간(기본 3분) 동안 말소리가 없으면 '쉬는 시간인가요?' → 조용해진 시점부터 쉬는 시간.
// - 마이크 끊김: 같은 마이크(없으면 기본 마이크)로 조용히 다시 연결. 실패하면 일시정지하고 알린다.
// - 서버 연결 끊김: 소리를 브라우저에 모아 두고 다시 연결되면 이어서 보낸다 (최대 30분).
// - 노트북 잠자기: 오디오 시계가 멈췄다가 다시 가면 잠들었던 것. 그동안을 쉬는 시간으로 남긴다.
//   (타이머 간격으로 재면 백그라운드 탭에서 오판하므로 오디오 시계와 실제 시계의 차이로 잰다)
// - 탭 닫기: 녹음 중이면 브라우저가 한 번 더 묻는다.
// - 잠자기 방지: 서버가 맥의 caffeinate로 막고, 그럴 수 없으면 화면 꺼짐 방지(Wake Lock)를 쓴다.
// - 저장 공간: 1시간 분량보다 적으면 알리고, 바닥나면 서버가 여기까지 저장하고 멈춘다.
import { api, type Job, type Unit } from '../lib/api'
import { isPersistent, REC_IDLE, useStore, type RecAlert, type Signal } from '../lib/store'

const SR = 16000
const MAX_BUFFER_SAMPLES = SR * 60 * 30
const DISK_LOW_S = 3600
const DISK_CRITICAL_S = 600

// E2E에서 줄여 쓸 수 있게 한곳에 모은다
const T = {
  audioGap: 4000, // 오디오 시계가 이만큼 밀리면 잠들었던 것
  deadIcon: 30000, // 음소거/완전 무음이 이만큼 이어지면 아이콘
  deadBanner: 60000, // ...이만큼이면 알림
  pausedGrace: 30000, // 일시정지 후 이 시간 동안은 말소리를 알리지 않는다
  netBanner: 15000, // 서버 재연결이 이보다 길어지면 알림
}

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
  pausedWall: number
  pausedSpeechAlerted: boolean
  stopping: boolean
  reconnecting: boolean
  netBanner: boolean
  lastSendWall: number
  /** 실제 시계 - 오디오 시계. 갑자기 커지면 그동안 오디오가 멈췄던 것(잠자기) */
  clockLag: number | null
  lastChunkWall: number
  /** 서버가 아직 확인하지 않은 잠자기 구간 */
  pendingGap: { fromWall: number } | null
  tick: number
  meter: SpeechMeter
  silencePrompted: boolean
  deadMs: number
  trackMuted: boolean
  micRetrying: boolean
  micFailed: boolean
  wakeLock: WakeLockSentinel | null
  resolveStop?: (job: Job | null) => void
}

let session: Session | null = null

function store() {
  return useStore.getState()
}

function elapsed(s: Session) {
  return s.offset + (s.sent + s.buffered) / SR
}

/** 알림을 띄운다. 계속 보이는 알림이 떠 있으면 한 번 띄우는 알림은 띄우지 않는다.
 *  같은 알림의 내용만 바뀌면(남은 공간 등) 접어 둔 상태를 그대로 둔다. */
function raise(alert: RecAlert) {
  const r = store().rec
  if (isPersistent(r.alert) && !isPersistent(alert)) return
  store().setRec({ alert, alertCollapsed: r.alert?.kind === alert.kind ? r.alertCollapsed : false })
}

function clearAlert(kind: RecAlert['kind']) {
  if (store().rec.alert?.kind === kind) store().setRec({ alert: null, alertCollapsed: false })
}

/** 한 번 띄우는 알림만 지운다 (일시정지/재개할 때) */
function clearTransientAlert() {
  const cur = store().rec.alert
  if (cur && !isPersistent(cur)) store().setRec({ alert: null, alertCollapsed: false })
}

function micState(s: Session): Signal {
  if (s.micFailed) return 'bad'
  if (s.micRetrying || s.trackMuted || s.deadMs >= T.deadIcon) return 'warn'
  return 'ok'
}

function updateSignals(s: Session) {
  const r = store().rec
  const mic = micState(s)
  const net: Signal = s.reconnecting ? 'warn' : 'ok'
  if (r.mic !== mic || r.net !== net) store().setRec({ mic, net })
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

// ---------- 화면 꺼짐 방지 (서버가 잠자기를 막을 수 없을 때만) ----------

async function holdWakeLock(s: Session) {
  if (store().health?.keep_awake || s.wakeLock || document.visibilityState !== 'visible') return
  try {
    s.wakeLock = (await navigator.wakeLock?.request('screen')) ?? null
    s.wakeLock?.addEventListener('release', () => {
      if (session === s) s.wakeLock = null
    })
  } catch {
    /* 지원하지 않거나 거절됨: 잠자기는 깨어난 뒤 쉬는 시간으로 처리 */
  }
}

document.addEventListener('visibilitychange', () => {
  // 화면 꺼짐 방지는 탭이 가려지면 풀리므로 돌아오면 다시 잡는다
  if (session && !session.stopping && document.visibilityState === 'visible') holdWakeLock(session)
})

window.addEventListener('beforeunload', (e) => {
  if (!session || session.stopping) return
  e.preventDefault()
  e.returnValue = ''
})

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
  s.wakeLock?.release().catch(() => undefined)
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
  disk_free_s?: number
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
          s.pendingGap = null
          if (msg.divider) refreshLiveSoon(s.meetingId)
          break
        case 'stopped':
          s.resolveStop?.(msg.job ?? null)
          break
        case 'disk': {
          // 10분 분량 아래로 처음 내려가면 접어 둔 알림을 다시 펼친다
          const cur = st.rec.alert
          const wasCritical = cur?.kind === 'disk-low' && cur.freeS < DISK_CRITICAL_S
          raise({ kind: 'disk-low', freeS: msg.free_s })
          if (!wasCritical && msg.free_s < DISK_CRITICAL_S) st.setRec({ alertCollapsed: false })
          break
        }
        case 'disk-full':
          onDiskFull(s)
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
  if (r.disk_free_s !== undefined) {
    if (r.disk_free_s < DISK_LOW_S) raise({ kind: 'disk-low', freeS: r.disk_free_s })
    else clearAlert('disk-low')
  }
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
  s.netBanner = false
  updateSignals(s)
  const since = s.lastSendWall
  // 잠깐 끊긴 것은 아이콘으로만, 15초가 넘으면 알린다
  setTimeout(() => {
    if (session === s && s.reconnecting && !s.stopping) {
      s.netBanner = true
      raise({ kind: 'server-lost', since })
    }
  }, T.netBanner)
  for (let attempt = 0; attempt < 120 && session === s && !s.stopping; attempt++) {
    await new Promise((r) => setTimeout(r, attempt < 3 ? 1000 * (attempt + 1) : 5000))
    if (session !== s || s.stopping) return
    const params = new URLSearchParams()
    // 모아 둔 소리가 끊김 없이 이어지면 쉬는 시간이 아니다. 잠들었거나 너무 오래 끊겼으면 그때부터 쉬는 시간.
    if (!s.overflow && !s.pendingGap) params.set('continuous', '1')
    else params.set('since', String((s.pendingGap?.fromWall ?? since) / 1000))
    if (s.paused) params.set('paused', '1')
    try {
      const r = await openSocket(s, `?${params}`)
      afterReady(s, r)
      flushBuffer(s)
      s.reconnecting = false
      s.pendingGap = null
      updateSignals(s)
      if (s.netBanner) {
        clearAlert('server-lost')
        store().toast('서버에 다시 연결했어요. 끊긴 동안의 소리도 저장했어요.', 'success')
      }
      s.netBanner = false
      return
    } catch {
      /* 다시 시도 */
    }
  }
  if (session === s) {
    s.reconnecting = false
    cleanup()
    const st = store()
    st.setRec({ ...REC_IDLE })
    st.toast('서버에 다시 연결하지 못했어요. 녹음된 부분은 저장되어 있고 이어서 녹음할 수 있어요.', 'error')
    st.loadMeetings()
  }
}

/** 오디오가 몇 초 멈췄다가 다시 들어왔다 (노트북 잠자기 등). 그동안은 녹음되지 않았다. */
function onAudioGap(s: Session, fromWall: number) {
  s.meter.lastSpeechAt = Date.now()
  if (s.paused) return // 이미 쉬는 중
  s.pendingGap = { fromWall }
  if (s.ws && s.ws.readyState === WebSocket.OPEN && !s.reconnecting) {
    // 서버가 'resumed'로 답하면 pendingGap을 지운다. 답이 없이 끊기면 다시 연결할 때 since로 알린다.
    s.ws.send(JSON.stringify({ type: 'pause', from_t: elapsed(s), from_wall: fromWall / 1000 }))
    s.ws.send(JSON.stringify({ type: 'resume' }))
  }
}

function onDiskFull(s: Session) {
  s.stopping = true
  const meetingId = s.meetingId
  cleanup()
  const st = store()
  st.setRec({ ...REC_IDLE, alert: { kind: 'disk-full' } })
  st.setPartial(null)
  st.loadMeetings()
  if (st.selectedId === meetingId) {
    st.refreshMeeting()
    st.refreshViews()
  }
}

function onChunk(s: Session, pcm: ArrayBuffer | null, rms: number) {
  const now = Date.now()
  // 잠자기: 오디오 시계는 멈춰 있었는데 실제 시계만 흘렀다
  const lag = now / 1000 - s.ctx.currentTime
  if (s.clockLag !== null && (lag - s.clockLag) * 1000 > T.audioGap) onAudioGap(s, now - (lag - s.clockLag) * 1000)
  s.clockLag = lag
  s.lastChunkWall = now

  const speaking = s.meter.update(rms)

  // 마이크가 소리를 주지 않음: 트랙 음소거 또는 완전한 0
  const dead = rms === 0 || s.trackMuted
  s.deadMs = dead ? s.deadMs + 100 : 0
  updateSignals(s)
  const alert = store().rec.alert
  if (dead && !s.paused && !s.micRetrying && s.deadMs >= T.deadBanner) {
    if (alert?.kind !== 'mic-silent') raise({ kind: 'mic-silent' })
  } else if (!dead && alert?.kind === 'mic-silent') clearAlert('mic-silent')

  if (s.paused) {
    if (speaking && now - s.pausedWall > T.pausedGrace) {
      if (!s.pausedSpeechAlerted) {
        s.pausedSpeechAlerted = true
        raise({ kind: 'paused-speech' })
      }
      if (!store().rec.heard) store().setRec({ heard: true })
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
  // 오디오가 들어오지 않으면(깨어난 뒤 멈춘 AudioContext 등) 다시 켠다
  if (now - s.lastChunkWall > T.audioGap && s.ctx.state !== 'running') s.ctx.resume().catch(() => undefined)

  // 오래 조용하면 쉬는 시간인지 묻는다
  const minutes = st.settings?.break_prompt_minutes ?? 3
  if (!s.paused && minutes > 0 && !s.silencePrompted && now - s.meter.lastSpeechAt > minutes * 60000) {
    s.silencePrompted = true
    raise({ kind: 'silence', fromT: s.meter.lastSpeechT, fromWall: s.meter.lastSpeechAt / 1000, minutes })
  }
  if (st.rec.status === 'recording') document.title = '녹음 중 · 회의 노트'
  else if (st.rec.status === 'paused') document.title = '일시정지 · 회의 노트'
}

function attachTrack(s: Session) {
  const track = s.stream.getAudioTracks()[0]
  if (!track) return
  const current = () => session === s && s.stream.getAudioTracks()[0] === track
  s.trackMuted = track.muted
  track.addEventListener('ended', () => {
    if (current() && !s.stopping) reconnectMic()
  })
  // 다른 앱이 마이크를 가져가거나 시스템이 막으면 mute가 온다
  track.addEventListener('mute', () => {
    if (!current()) return
    s.trackMuted = true
    updateSignals(s)
  })
  track.addEventListener('unmute', () => {
    if (!current()) return
    s.trackMuted = false
    updateSignals(s)
  })
}

/** 녹음 중에 마이크를 바꾼다 */
export async function switchMic(deviceId: string | undefined): Promise<boolean> {
  const s = session
  if (!s) return false
  s.deviceId = deviceId
  return reconnectMic({ manual: true, message: '마이크를 바꿨어요.' })
}

/** 마이크를 다시 연결한다. 끊겼을 때는 조용히, 사용자가 눌렀을 때(manual)는 결과를 알려 준다. */
export async function reconnectMic(opts?: { manual?: boolean; message?: string }): Promise<boolean> {
  const s = session
  if (!s || s.micRetrying) return false
  s.micRetrying = true
  updateSignals(s)
  const st = store()
  const hadBanner = st.rec.alert?.kind === 'mic-lost' || st.rec.alert?.kind === 'mic-silent'
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
      s.deadMs = 0
      s.micRetrying = false
      s.micFailed = false
      updateSignals(s)
      clearAlert('mic-lost')
      clearAlert('mic-silent')
      if (fallback) st.toast('원래 마이크를 찾을 수 없어 기본 마이크로 다시 연결했어요.')
      else if (opts?.manual || hadBanner) st.toast(opts?.message || '마이크를 다시 연결했어요.', 'success')
      return true
    } catch {
      await new Promise((r) => setTimeout(r, 1000))
    }
  }
  s.micRetrying = false
  s.micFailed = true
  if (!s.paused) pauseRecording()
  updateSignals(s)
  raise({ kind: 'mic-lost' })
  return false
}

export async function startRecording(meetingId: string, deviceId?: string): Promise<void> {
  const st = store()
  if (session) {
    st.toast('이미 녹음 중이에요')
    return
  }
  st.setRec({ ...REC_IDLE, status: 'connecting', meetingId })
  let stream: MediaStream
  try {
    stream = await getMic(deviceId)
  } catch (e) {
    st.setRec({ ...REC_IDLE })
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

  const now = Date.now()
  const s: Session = {
    meetingId, deviceId, ws: null, ctx, stream, source, node, offset: 0, sent: 0, buffer: [], buffered: 0, overflow: false,
    paused: false, pausedWall: 0, pausedSpeechAlerted: false, stopping: false, reconnecting: false, netBanner: false,
    lastSendWall: now, clockLag: null, lastChunkWall: now, pendingGap: null, tick: 0,
    meter: new SpeechMeter(), silencePrompted: false, deadMs: 0, trackMuted: false, micRetrying: false, micFailed: false, wakeLock: null,
  }
  session = s
  let r: Ready
  try {
    r = await openSocket(s, '')
  } catch (e) {
    cleanup()
    st.setRec({ ...REC_IDLE })
    st.toast((e as Error).message, 'error')
    throw e
  }
  afterReady(s, r)
  s.meter.lastSpeechT = s.offset
  attachTrack(s)
  holdWakeLock(s)
  let lastLevel = 0
  node.port.onmessage = (ev) => {
    const { pcm, rms } = ev.data as { pcm: ArrayBuffer | null; rms: number }
    onChunk(s, pcm, rms)
    const t = performance.now()
    if (t - lastLevel > 80) {
      lastLevel = t
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
  s.pausedWall = Date.now()
  s.pausedSpeechAlerted = false
  s.node.port.postMessage({ type: 'enable', value: false })
  if (s.ws && s.ws.readyState === WebSocket.OPEN) {
    s.ws.send(JSON.stringify({ type: 'pause', from_t: opts?.fromT, from_wall: opts?.fromWall }))
  }
  const pausedAt = opts?.fromWall ? opts.fromWall * 1000 : Date.now()
  clearTransientAlert()
  store().setRec({ status: 'paused', pausedAt, level: 0, heard: false })
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
  clearTransientAlert()
  store().setRec({ status: 'recording', pausedAt: null, heard: false })
}

export async function stopRecording(): Promise<Job | null> {
  const s = session
  if (!s) return null
  const st = store()
  st.setRec({ status: 'stopping', alert: null, alertCollapsed: false })
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
  st.setRec({ ...REC_IDLE })
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

// 개발/E2E용: 마이크 끊김, 서버 끊김, 잠자기를 흉내 내고 기다리는 시간을 줄인다
;(window as unknown as { __scribeDebug: unknown }).__scribeDebug = {
  endTrack: () => session?.stream.getAudioTracks()[0]?.dispatchEvent(new Event('ended')),
  muteTrack: (muted: boolean) => session?.stream.getAudioTracks()[0]?.dispatchEvent(new Event(muted ? 'mute' : 'unmute')),
  dropSocket: () => session?.ws?.close(),
  // 오디오 시계가 sec초 멈췄던 것처럼 만든다 (잠자기)
  sleep: (sec: number) => {
    if (session && session.clockLag !== null) session.clockLag -= sec
  },
  timing: (patch: Partial<typeof T>) => Object.assign(T, patch),
  state: () =>
    session
      ? {
          paused: session.paused, buffered: session.buffered, sent: session.sent, offset: session.offset,
          reconnecting: session.reconnecting, micRetrying: session.micRetrying, wakeLock: !!session.wakeLock,
        }
      : null,
}
