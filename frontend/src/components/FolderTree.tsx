import { ChevronRight, CircleHelp, Folder, FolderOpen, FolderPlus, MoreHorizontal } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { api, type Group, type GroupsOut, type MeetingSummary } from '../lib/api'
import { useStore } from '../lib/store'
import { Floating } from './Floating'

const MAX_DEPTH = 3
const MAC = typeof navigator !== 'undefined' && /Mac/i.test(navigator.userAgent)
const OPEN_KEY = 'openFolders'
const DRAG_MEETING = 'application/x-meeting'
const DRAG_GROUP = 'application/x-group'

function loadOpen(): string[] {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')
  } catch {
    return []
  }
}

/** 폴더 작업 뒤: 목록을 새로 읽는다 (폴더는 앱 안에서만 쓰고 디스크의 회의 파일은 옮기지 않는다) */
async function after(r: Promise<GroupsOut>) {
  const st = useStore.getState()
  try {
    const out = await r
    await st.loadMeetings()
    return out
  } catch (e) {
    st.toast((e as Error).message, 'error')
    return null
  }
}

export const moveMeeting = (id: string, groupId: string | null) => after(api.moveMeeting(id, groupId))

type Menu = { rect: DOMRect; kind: 'group'; group: Group } | { rect: DOMRect; kind: 'meeting'; meeting: MeetingSummary }

/**
 * 왼쪽 목록의 폴더: 회의를 끌어다 놓거나 오른쪽 클릭으로 옮기고, 폴더는 3단계까지 넣을 수 있다.
 * 폴더 밖 회의는 rest로 받아 날짜별로 그린다 (Sidebar).
 */
export function FolderTree({ meetings, renderItem, rest }: { meetings: MeetingSummary[]; renderItem: (m: MeetingSummary, extra: ItemExtra) => ReactNode; rest: (extra: (m: MeetingSummary) => ItemExtra) => ReactNode }) {
  const groups = useStore((s) => s.groups)
  const [open, setOpen] = useState<string[]>(loadOpen)
  const [editing, setEditing] = useState<string | null>(null) // 이름 바꾸는 폴더 id, 'new:<parent|root>'는 새 폴더
  const [menu, setMenu] = useState<Menu | null>(null)
  const [help, setHelp] = useState<DOMRect | null>(null)
  const [dragging, setDragging] = useState(false)
  const [over, setOver] = useState<string | null>(null) // 끌어다 놓을 곳 (폴더 id, 'root')

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify(open))
    } catch {
      /* 개인 정보 보호 모드 등 */
    }
  }, [open])

  const byId = useMemo(() => new Map(groups.map((g) => [g.id, g])), [groups])
  const children = useMemo(() => {
    const out = new Map<string | null, Group[]>()
    for (const g of groups) {
      const p = g.parent_id && byId.has(g.parent_id) ? g.parent_id : null
      out.set(p, [...(out.get(p) || []), g])
    }
    return out
  }, [groups, byId])
  const inGroup = useMemo(() => {
    const out = new Map<string, MeetingSummary[]>()
    for (const m of meetings) if (m.group_id && byId.has(m.group_id)) out.set(m.group_id, [...(out.get(m.group_id) || []), m])
    return out
  }, [meetings, byId])
  const depth = (id: string | null): number => {
    let d = 0
    for (let g = id ? byId.get(id) : undefined; g && d < 10; g = g.parent_id ? byId.get(g.parent_id) : undefined) d++
    return d
  }
  const total = (id: string): number => (inGroup.get(id)?.length || 0) + (children.get(id) || []).reduce((n, c) => n + total(c.id), 0)
  const toggle = (id: string, on?: boolean) => setOpen((o) => (on ?? !o.includes(id) ? [...new Set([...o, id])] : o.filter((x) => x !== id)))

  const create = async (name: string, parent: string | null) => {
    setEditing(null)
    if (!name.trim()) return
    const out = await after(api.createGroup(name, parent))
    if (out && parent) toggle(parent, true)
  }
  const rename = async (g: Group, name: string) => {
    setEditing(null)
    if (name.trim() && name.trim() !== g.name) await after(api.patchGroup(g.id, { name }))
  }
  const remove = async (g: Group) => {
    const n = total(g.id)
    const msg = `'${g.name}' 폴더를 지울까요?\n${n ? `안의 회의 ${n}개는 지우지 않고 한 칸 위로 옮겨요.` : '빈 폴더예요.'}`
    if (window.confirm(msg)) await after(api.deleteGroup(g.id))
  }

  // ---- 끌어다 놓기 ----
  const dragProps = (kind: string, id: string) => ({
    draggable: true,
    onDragStart: (e: DragEvent) => {
      e.dataTransfer.setData(kind, id)
      e.dataTransfer.effectAllowed = 'move'
      setDragging(true)
    },
    onDragEnd: () => {
      setDragging(false)
      setOver(null)
    },
  })
  const dropProps = (target: string | null) => ({
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_MEETING) && !e.dataTransfer.types.includes(DRAG_GROUP)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setOver(target ?? 'root')
    },
    onDragLeave: () => setOver((o) => (o === (target ?? 'root') ? null : o)),
    onDrop: async (e: DragEvent) => {
      e.preventDefault()
      setOver(null)
      setDragging(false)
      const mid = e.dataTransfer.getData(DRAG_MEETING)
      const gid = e.dataTransfer.getData(DRAG_GROUP)
      if (mid) await moveMeeting(mid, target)
      else if (gid && gid !== target) await after(api.patchGroup(gid, { parent_id: target ?? '' }))
      if (target) toggle(target, true)
    },
  })
  const itemExtra = (m: MeetingSummary): ItemExtra => ({
    drag: dragProps(DRAG_MEETING, m.id),
    onContextMenu: (e) => {
      e.preventDefault()
      setMenu({ rect: new DOMRect(e.clientX, e.clientY, 0, 0), kind: 'meeting', meeting: m })
    },
  })

  const row = (g: Group): ReactNode => {
    const isOpen = open.includes(g.id)
    const kids = children.get(g.id) || []
    const items = inGroup.get(g.id) || []
    const d = depth(g.id)
    return (
      <div key={g.id} className="folder" style={{ ['--depth' as string]: d - 1 }}>
        <div
          className={`side-item folder-row ${over === g.id ? 'drop' : ''}`}
          {...dragProps(DRAG_GROUP, g.id)}
          {...dropProps(g.id)}
          onClick={() => editing !== g.id && toggle(g.id)}
          onDoubleClick={(e) => {
            e.preventDefault()
            setEditing(g.id)
          }}
          onContextMenu={(e) => {
            e.preventDefault()
            setMenu({ rect: new DOMRect(e.clientX, e.clientY, 0, 0), kind: 'group', group: g })
          }}
          title="두 번 눌러 이름 바꾸기 · 회의를 끌어다 놓아 넣기"
        >
          <ChevronRight size={13} className={`chev ${isOpen ? 'open' : ''}`} />
          {isOpen ? <FolderOpen size={15} className="side-icon" /> : <Folder size={15} className="side-icon" />}
          {editing === g.id ? (
            <NameInput initial={g.name} onDone={(v) => (v === null ? setEditing(null) : rename(g, v))} />
          ) : (
            <span className="side-title">{g.name}</span>
          )}
          <span className="side-meta">{total(g.id) || ''}</span>
          <button
            className="icon-btn folder-more"
            title="폴더 메뉴"
            onClick={(e) => {
              e.stopPropagation()
              setMenu({ rect: e.currentTarget.getBoundingClientRect(), kind: 'group', group: g })
            }}
          >
            <MoreHorizontal size={14} />
          </button>
        </div>
        {isOpen && (
          <div className="folder-body">
            {editing === `new:${g.id}` && <NewFolder onDone={(v) => (v === null ? setEditing(null) : create(v, g.id))} />}
            {kids.map(row)}
            {items.map((m) => renderItem(m, itemExtra(m)))}
            {!kids.length && !items.length && editing !== `new:${g.id}` && <p className="folder-empty">회의를 여기로 끌어다 놓으세요</p>}
          </div>
        )}
      </div>
    )
  }

  const flat = useMemo(() => {
    // '폴더로 옮기기' 메뉴: 폴더를 위에서부터 들여 써서 보여 준다
    const out: { g: Group; d: number }[] = []
    const walk = (p: string | null, d: number) => (children.get(p) || []).forEach((g) => (out.push({ g, d }), walk(g.id, d + 1)))
    walk(null, 0)
    return out
  }, [children])

  return (
    <>
      <div className="side-group folder-head">
        <span>폴더</span>
        <span className="spacer" />
        <button className="icon-btn" title="폴더 도움말" onClick={(e) => setHelp(e.currentTarget.getBoundingClientRect())}><CircleHelp size={13} /></button>
        <button className="icon-btn" title="새 폴더" onClick={() => setEditing('new:root')}><FolderPlus size={14} /></button>
      </div>
      {editing === 'new:root' && <NewFolder onDone={(v) => (v === null ? setEditing(null) : create(v, null))} />}
      {(children.get(null) || []).map(row)}
      {!groups.length && editing !== 'new:root' && (
        <button className="folder-hint" onClick={() => setEditing('new:root')}>+ 폴더를 만들어 회의를 정리해 보세요</button>
      )}
      <div className={`root-drop ${dragging ? 'show' : ''} ${over === 'root' ? 'drop' : ''}`} {...dropProps(null)}>
        폴더 밖으로 꺼내려면 여기에 놓으세요
      </div>
      {rest(itemExtra)}

      {menu && (
        <Floating rect={menu.rect} onClose={() => setMenu(null)} className="menu folder-menu">
          {menu.kind === 'group' ? (
            <>
              <div className="menu-label">{menu.group.name}</div>
              {depth(menu.group.id) < MAX_DEPTH && (
                <button className="menu-item" onClick={() => { toggle(menu.group.id, true); setEditing(`new:${menu.group.id}`); setMenu(null) }}>안에 새 폴더</button>
              )}
              <button className="menu-item" onClick={() => { setEditing(menu.group.id); setMenu(null) }}>이름 바꾸기</button>
              {menu.group.parent_id && (
                <button className="menu-item" onClick={() => { after(api.patchGroup(menu.group.id, { parent_id: '' })); setMenu(null) }}>맨 바깥으로 꺼내기</button>
              )}
              <div className="menu-sep" />
              <button className="menu-item danger" onClick={() => { remove(menu.group); setMenu(null) }}>폴더 지우기 (회의는 남아요)</button>
            </>
          ) : (
            <>
              <div className="menu-label">폴더로 옮기기</div>
              {flat.length === 0 && <p className="menu-empty">아직 폴더가 없어요. 왼쪽 위 '폴더'의 + 로 만들어요.</p>}
              {flat.map(({ g, d }) => (
                <button
                  key={g.id}
                  className={`menu-item ${menu.meeting.group_id === g.id ? 'on' : ''}`}
                  style={{ paddingLeft: 9 + d * 14 }}
                  disabled={menu.meeting.group_id === g.id}
                  onClick={() => { moveMeeting(menu.meeting.id, g.id); toggle(g.id, true); setMenu(null) }}
                >
                  <Folder size={13} />{g.name}
                </button>
              ))}
              {menu.meeting.group_id && (
                <>
                  <div className="menu-sep" />
                  <button className="menu-item" onClick={() => { moveMeeting(menu.meeting.id, null); setMenu(null) }}>폴더 밖으로 꺼내기</button>
                </>
              )}
            </>
          )}
        </Floating>
      )}
      {help && (
        <Floating rect={help} onClose={() => setHelp(null)} className="menu folder-help">
          <div className="menu-label">폴더로 회의 정리하기</div>
          <ul>
            <li>회의를 폴더로 <b>끌어다 놓거나</b>, 회의를 <b>{MAC ? '두 손가락으로 클릭(또는 control+클릭)' : '오른쪽 클릭'}</b>해서 옮겨요.</li>
            <li>폴더 이름은 <b>두 번 눌러</b> 바꿔요. 폴더를 다른 폴더로 끌어 넣을 수 있어요 ({MAX_DEPTH}단계까지).</li>
            <li>폴더를 지워도 <b>회의는 지워지지 않고</b> 한 칸 위로 나와요.</li>
            <li>폴더는 <b>앱 안에서만</b> 정리돼요. {MAC ? 'Finder' : '파일 탐색기'}의 저장 폴더는 그대로라(<code>meetings/날짜_제목</code>) 파일이 옮겨지지 않아요. 회의 파일은 회의 화면의 '폴더 열기'로 바로 열 수 있어요.</li>
            <li>같은 곳에 같은 이름(대소문자만 다른 이름 포함)의 폴더는 만들 수 없어요.</li>
          </ul>
        </Floating>
      )}
    </>
  )
}

export type ItemExtra = {
  drag: { draggable: boolean; onDragStart: (e: DragEvent) => void; onDragEnd: () => void }
  onContextMenu: (e: React.MouseEvent) => void
}

function NameInput({ initial, onDone }: { initial: string; onDone: (v: string | null) => void }) {
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (v: string | null) => {
    if (done.current) return
    done.current = true
    onDone(v)
  }
  return (
    <input
      ref={ref}
      className="folder-input"
      defaultValue={initial}
      maxLength={60}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !e.nativeEvent.isComposing) finish(e.currentTarget.value)
        if (e.key === 'Escape') finish(null)
      }}
      onBlur={(e) => finish(e.currentTarget.value.trim() ? e.currentTarget.value : null)}
    />
  )
}

function NewFolder({ onDone }: { onDone: (v: string | null) => void }) {
  return (
    <div className="side-item folder-row new">
      <Folder size={15} className="side-icon" />
      <NameInput initial="새 폴더" onDone={onDone} />
    </div>
  )
}
