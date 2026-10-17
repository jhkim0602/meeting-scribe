import { X } from 'lucide-react'
import { useStore } from '../lib/store'

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  const dismiss = useStore((s) => s.dismissToast)
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast tone-${t.tone}`}>
          <span>{t.text}</span>
          <button className="icon-btn" onClick={() => dismiss(t.id)}><X size={13} /></button>
        </div>
      ))}
    </div>
  )
}
