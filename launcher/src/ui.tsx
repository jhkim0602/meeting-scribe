import type { ReactNode } from 'react'
import { catalog, deviceName, forThisOs, type Choice } from './api'

export const Logo = ({ size = 40 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
    <rect width="64" height="64" rx="14" fill="#2f2e2b" />
    <rect x="16" y="22" width="5" height="20" rx="2.5" fill="#fff" />
    <rect x="25" y="15" width="5" height="34" rx="2.5" fill="#fff" />
    <rect x="34" y="24" width="5" height="16" rx="2.5" fill="#fff" />
    <rect x="43" y="19" width="5" height="26" rx="2.5" fill="#e3b341" />
  </svg>
)

export function StatusIcon({ status }: { status: 'running' | 'done' | 'skip' | 'warn' | 'error' | 'todo' | null | boolean }) {
  if (status === 'running') return <span className="spin" aria-label="진행 중" />
  if (status === 'done' || status === 'skip' || status === true)
    return (
      <svg className="ic ok" viewBox="0 0 20 20" aria-label="완료"><circle cx="10" cy="10" r="9" /><path d="M6 10.2l2.6 2.6L14.2 7.4" /></svg>
    )
  if (status === 'warn' || status === null)
    return (
      <svg className="ic warn" viewBox="0 0 20 20" aria-label="확인 필요"><circle cx="10" cy="10" r="9" /><path d="M10 5.5v5.5M10 14.2v.3" /></svg>
    )
  if (status === 'error' || status === false)
    return (
      <svg className="ic err" viewBox="0 0 20 20" aria-label="실패"><circle cx="10" cy="10" r="9" /><path d="M7 7l6 6M13 7l-6 6" /></svg>
    )
  return <span className="ic todo" />
}

export const Bar = ({ value }: { value: number | null }) => (
  <div className={`bar ${value === null ? 'indeterminate' : ''}`}>
    <div style={{ width: value === null ? undefined : `${Math.round(value * 100)}%` }} />
  </div>
)

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

/** 모델 고르기 (콤보박스). 이 기기 메모리에 맞지 않는 것은 알려 준다. */
export function ModelSelect({ role, value, onChange, ram, recommended }: { role: 'live' | 'final'; value?: string; onChange: (id: string) => void; ram: number | null; recommended: string }) {
  const list: Choice[] = catalog[role].filter(forThisOs)
  const cur = list.find((c) => c.id === value) ?? list[0]
  const size = Math.round(cur.repos.reduce((n, r) => n + (catalog.sizes_gb[r] ?? 0), 0) * 10) / 10
  const tooBig = cur.min_ram_gb !== undefined && ram !== null && ram < cur.min_ram_gb
  return (
    <div className="model">
      <div className="select-wrap">
        <select value={cur.id} onChange={(e) => onChange(e.target.value)}>
          {list.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label} · {c.id === recommended ? `이 ${deviceName}에 추천` : c.tag}
            </option>
          ))}
        </select>
      </div>
      <p className="model-detail">{cur.detail}</p>
      <p className="model-meta">
        <span>받는 양 약 {size}GB</span>
        <span>메모리 약 {cur.memory_gb}GB 사용</span>
        {cur.id === recommended && <span className="tag good">이 {deviceName}에 추천</span>}
        {tooBig && <span className="tag warn">이 {deviceName}({ram}GB)에는 무거워요</span>}
      </p>
    </div>
  )
}

export const ExtLink = ({ href, children, open }: { href: string; children: ReactNode; open: (url: string) => void }) => (
  <a
    href={href}
    onClick={(e) => {
      e.preventDefault()
      open(href)
    }}
  >
    {children}
    <svg className="ext" viewBox="0 0 12 12" aria-hidden><path d="M4.5 2.5h5v5M9.5 2.5L3 9" /></svg>
  </a>
)

export function shortPath(p?: string): string {
  if (!p) return ''
  return p.replace(/^\/Users\/[^/]+/, '~').replace(/^\/home\/[^/]+/, '~').replace(/^\/root(?=\/|$)/, '~')
}
