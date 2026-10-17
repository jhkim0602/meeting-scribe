import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  rect: DOMRect | { left: number; top: number; bottom: number; width: number; right: number; height: number }
  placement?: 'above' | 'below'
  onClose?: () => void
  children: ReactNode
  className?: string
}

/** 선택 영역/요소 근처에 떠 있는 레이어. 화면 밖으로 나가지 않게 위치를 보정한다. */
export function Floating({ rect, placement = 'below', onClose, children, className }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const w = el.offsetWidth
    const h = el.offsetHeight
    const margin = 8
    let left = rect.left + rect.width / 2 - w / 2
    left = Math.max(margin, Math.min(left, window.innerWidth - w - margin))
    let top = placement === 'above' ? rect.top - h - 8 : rect.bottom + 8
    if (placement === 'above' && top < margin) top = rect.bottom + 8
    if (placement === 'below' && top + h > window.innerHeight - margin) top = Math.max(margin, rect.top - h - 8)
    setPos({ left, top })
  }, [rect, placement])

  useEffect(() => {
    if (!onClose) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const t = setTimeout(() => document.addEventListener('mousedown', onDown), 0)
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return createPortal(
    <div ref={ref} className={`floating ${className || ''}`} style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999 }}>
      {children}
    </div>,
    document.body,
  )
}
