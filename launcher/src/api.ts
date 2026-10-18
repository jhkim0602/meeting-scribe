// 앱(Rust)에 부탁하는 명령과 알림. 브라우저에서 화면만 열면(개발·스크린샷) 흉내 내는 응답을 쓴다.
import catalogJson from '../../backend/scribe/models.json'

export type Config = {
  data_dir?: string
  port?: number
  live?: string
  final?: string
  hf_token?: string
  open_browser?: boolean
  setup_done?: boolean
  packages_rev?: string
}

export type ServerStatus = { state: 'running' | 'starting' | 'stopped' | 'error'; port: number; url: string; external: boolean; error: string | null }

export type AppState = {
  config: Config
  needs_install: boolean
  installing: boolean
  paths: { support: string; config: string; logs: string; default_data_dir: string }
  server: ServerStatus
  version: string
  autostart: boolean
  /** 맥에서 디스크 이미지·다운로드 폴더에서 바로 열었다 (응용 프로그램 폴더로 옮겨야 한다) */
  misplaced: boolean
}

export type Machine = { chip: string; ram_gb: number | null; os: string; apple_silicon: boolean; platform: string; free_gb: number | null }

export type Step = 'python' | 'packages' | 'models' | 'check' | 'all'
export type InstallEvent = { step: Step; status: 'running' | 'done' | 'skip' | 'warn' | 'error'; progress: number | null; message: string; detail?: unknown }

export type DoctorItem = { id: string; ok: boolean | null; label: string; detail: string }
export type HfCheck = { ok: boolean; valid: boolean | null; access: boolean | null; user?: string; message: string; terms_url?: string }
export type ClaudeStatus = { ok: boolean; connected: boolean; installed: boolean; changed?: boolean; message: string; path: string }

export type Choice = { id: string; engine: string; model: string; label: string; tag: string; detail: string; repos: string[]; memory_gb: number; min_ram_gb?: number }
export const catalog = catalogJson as unknown as {
  sizes_gb: Record<string, number>
  live: Choice[]
  final: Choice[]
  recommend: { max_ram_gb?: number; live: string; final: string }[]
  speaker: { repos: string[]; terms_url: string; token_url: string }
}

export function recommend(ram: number | null): { live: string; final: string } {
  const rule = catalog.recommend.find((r) => r.max_ram_gb === undefined || (ram !== null && ram <= r.max_ram_gb)) ?? catalog.recommend[catalog.recommend.length - 1]
  return { live: rule.live, final: rule.final }
}

/** 이 선택으로 받는 양 (GB, 겹치는 모델은 한 번) */
export function downloadGb(live?: string, final?: string, speaker = false): number {
  const repos = new Set<string>()
  for (const [list, id] of [[catalog.live, live], [catalog.final, final]] as const) list.find((c) => c.id === id)?.repos.forEach((r) => repos.add(r))
  if (speaker) catalog.speaker.repos.forEach((r) => repos.add(r))
  return Math.round([...repos].reduce((n, r) => n + (catalog.sizes_gb[r] ?? 0), 0) * 10) / 10
}

export const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (inTauri) {
    const { invoke } = await import('@tauri-apps/api/core')
    return invoke<T>(cmd, args)
  }
  return mock.call(cmd, args) as Promise<T>
}

export async function on<T>(event: string, cb: (payload: T) => void): Promise<() => void> {
  if (inTauri) {
    const { listen } = await import('@tauri-apps/api/event')
    return listen<T>(event, (e) => cb(e.payload))
  }
  return mock.on(event, cb as (p: unknown) => void)
}

export const installer = <T,>(...args: string[]) => call<T>('installer', { args })

// ---------------- 브라우저에서 화면만 볼 때 ----------------

const params = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
const mock = (() => {
  const listeners = new Map<string, Set<(p: unknown) => void>>()
  const emit = (event: string, payload: unknown) => listeners.get(event)?.forEach((f) => f(payload))
  const installed = params.get('installed') === '1'
  const state: AppState = {
    config: installed
      ? { data_dir: '/Users/minji/MeetingScribe', live: 'qwen3-0.6b-8bit', final: 'qwen3-1.7b-8bit', setup_done: true, hf_token: params.get('token') === '1' ? 'hf_xxx' : undefined }
      : {},
    needs_install: !installed,
    installing: false,
    paths: { support: '/Users/minji/Library/Application Support/com.meetingscribe.app', config: '…/config.json', logs: '…/logs', default_data_dir: '/Users/minji/MeetingScribe' },
    server: { state: installed ? 'running' : 'stopped', port: 8765, url: 'http://127.0.0.1:8765', external: false, error: null },
    version: '0.1.0',
    autostart: params.get('autostart') === '1',
    misplaced: params.get('misplaced') === '1',
  }
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  const plan: [Step, number, string][] = [
    ['python', 1, '앱 전용 파이썬을 준비하는 중 (처음 한 번, 1분 안팎)'],
    ['packages', 8, '받는 중: torch (70.2MiB)'],
    ['models', 14, '모델 받는 중'],
    ['check', 1, '설치를 점검하는 중'],
  ]
  async function runInstall() {
    state.installing = true
    const speed = Number(params.get('speed') || 1)
    const stopAt = params.get('stop') // 스크린샷: 이 단계에서 멈춤 (예: models:0.42)
    for (const [step, ticks, msg] of plan) {
      for (let i = 0; i <= ticks; i++) {
        const p = ticks ? i / ticks : 1
        const gb = downloadGb(state.config.live, state.config.final, !!state.config.hf_token)
        const message = step === 'models' ? `모델 받는 중 ${(gb * p).toFixed(1)} / ${gb.toFixed(1)}GB` : msg
        emit('install', { step, status: 'running', progress: step === 'python' || step === 'check' ? null : p, message })
        if (stopAt && stopAt.startsWith(step) && p >= Number(stopAt.split(':')[1] || 0)) return
        await sleep(250 / speed)
      }
      if (params.get('fail') === step) {
        emit('install', { step, status: 'error', progress: null, message: '패키지를 설치하지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.\nerror: Failed to fetch: https://pypi.org/simple/torch/' })
        emit('install', { step: 'all', status: 'error', progress: null, message: '설치하지 못했어요' })
        state.installing = false
        return
      }
      const done: Record<Step, string> = { python: '파이썬을 준비했어요', packages: '패키지 설치를 마쳤어요', models: '모델을 모두 받았어요', check: '모두 준비됐어요', all: '' }
      emit('install', { step, status: 'done', progress: 1, message: done[step] })
    }
    state.installing = false
    state.needs_install = false
    state.config.setup_done = true
    emit('install', { step: 'all', status: 'done', progress: 1, message: '설치를 마쳤어요' })
  }
  return {
    on(event: string, cb: (p: unknown) => void) {
      if (!listeners.has(event)) listeners.set(event, new Set())
      listeners.get(event)!.add(cb)
      return Promise.resolve(() => listeners.get(event)!.delete(cb))
    },
    async call(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
      await sleep(60)
      switch (cmd) {
        case 'get_state':
          return structuredClone(state)
        case 'machine_info':
          return { chip: 'Apple M3 Pro', ram_gb: Number(params.get('ram') || 18), os: 'macOS 15.1', apple_silicon: true, platform: 'macos', free_gb: 212.4 } as Machine
        case 'save_config':
          Object.assign(state.config, args?.patch)
          return state.config
        case 'free_space':
          return 212.4
        case 'pick_folder':
          return '/Users/minji/Documents/회의 노트'
        case 'install_start':
          void runInstall()
          return null
        case 'installer': {
          const a = (args?.args as string[]) || []
          await sleep(500)
          if (a[0] === 'check-hf')
            return params.get('hf') === 'terms'
              ? { ok: false, valid: true, access: false, user: 'minji', message: '토큰은 맞아요. 화자 구분 모델 페이지에서 약관에 동의해 주세요.', terms_url: catalog.speaker.terms_url }
              : { ok: true, valid: true, access: true, user: 'minji', message: 'minji 확인했어요. 화자 구분을 쓸 수 있어요.' }
          if (a[0] === 'claude')
            return { ok: true, connected: a[1] === 'connect' || params.get('claude') === '1', installed: true, message: a[1] === 'connect' ? 'Claude 데스크톱에 연결했어요. Claude 앱을 다시 켜면 회의 노트가 보여요.' : '아직 연결하지 않았어요.', path: '' }
          if (a[0] === 'doctor')
            return {
              ok: true,
              items: [
                { id: 'python', ok: true, label: '파이썬', detail: '3.12.7' },
                { id: 'live-model', ok: true, label: '실시간 받아쓰기', detail: 'moona3k/mlx-qwen3-asr-0.6b-8bit' },
                { id: 'final-model', ok: true, label: '원본 스크립트', detail: 'moona3k/mlx-qwen3-asr-1.7b-8bit' },
                { id: 'ffmpeg', ok: true, label: '오디오 변환(ffmpeg)', detail: '앱에 들어 있음' },
                { id: 'speaker', ok: state.config.hf_token ? true : null, label: '화자 구분', detail: state.config.hf_token ? 'pyannote' : '토큰이 없어요 (선택)' },
                { id: 'data', ok: true, label: '저장 위치', detail: `${state.config.data_dir || state.paths.default_data_dir} · 남은 공간 212.4GB` },
              ],
            }
          return {}
        }
        case 'server_start':
        case 'open_app':
          state.server.state = 'running'
          emit('server', state.server)
          return null
        case 'is_recording':
          return params.get('recording') === '1'
        case 'server_stop':
          if (params.get('recording') === '1') throw '녹음 중이라 지금은 바꿀 수 없어요. 녹음을 마친 뒤 다시 해 주세요.'
          state.server.state = 'stopped'
          return state.server
        case 'autostart_set':
          state.autostart = !!args?.enabled
          return state.autostart
        case 'quit':
          return { confirm: params.get('recording') === '1' && !args?.force }
        case 'log_tail':
          return '$ uv pip install -r requirements-mac.txt\nResolved 85 packages in 1.20s\nerror: Failed to fetch: https://pypi.org/simple/torch/'
        default:
          return null
      }
    },
  }
})()
