<script setup lang="ts">
// Baum mit ETS-artiger Bedienung: Mehrfachauswahl (⌘/⇧), Tastatur, Ziehen & Ablegen
// (auch zwischen den Arbeitsbereichen), Kontextmenü, Umbenennen mit F2/Doppelklick auf den Namen.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { NDropdown, type DropdownOption } from 'naive-ui'
import { dropAction, loadObjects, select, store, type Item } from '../store'

export interface MenuEntry {
  label: string
  key: string
  run?: () => unknown
  disabled?: boolean
  divider?: boolean
  danger?: boolean
}

const props = defineProps<{
  items: Item[]
  treeId: string
  filter: string
  menu: (sel: Item[]) => MenuEntry[]
  renameable?: (item: Item) => boolean
  defaultDepth?: number
}>()
const emit = defineEmits<{ open: [Item]; rename: [Item, string]; remove: [Item[]] }>()

// --- Aufklappzustand (pro Baum im Browser gemerkt) -----------------------------------------
const storageKey = `knxlens:exp:${props.treeId}`
const expanded = ref(new Set<string>())
function loadExpanded() {
  try {
    const raw = localStorage.getItem(storageKey)
    if (raw) {
      expanded.value = new Set(JSON.parse(raw))
      return
    }
  } catch {
    /* kein Speicher verfügbar */
  }
  const out = new Set<string>()
  const walk = (items: Item[], depth: number) => {
    for (const i of items) {
      if (depth < (props.defaultDepth ?? 1) && i.children?.length) {
        out.add(i.key)
        walk(i.children, depth + 1)
      }
    }
  }
  walk(props.items, 0)
  expanded.value = out
}
function saveExpanded() {
  try {
    localStorage.setItem(storageKey, JSON.stringify([...expanded.value]))
  } catch {
    /* egal */
  }
}
onMounted(loadExpanded)
watch(() => props.items.length, (n, o) => { if (!o && n) loadExpanded() })

function childrenOf(i: Item): Item[] | undefined {
  if (i.lazy && i.uid) return store.objects[i.uid]
  return i.children
}
function hasChildren(i: Item) {
  return i.lazy || !!i.children?.length
}

async function toggle(i: Item, open?: boolean) {
  const isOpen = expanded.value.has(i.key)
  const want = open ?? !isOpen
  if (want === isOpen) return
  if (want) {
    if (i.lazy && i.uid) await loadObjects(i.uid)
    expanded.value.add(i.key)
  } else expanded.value.delete(i.key)
  expanded.value = new Set(expanded.value)
  saveExpanded()
}

// --- sichtbare Zeilen -------------------------------------------------------------------------
interface Row { item: Item; depth: number; parent: Item | null }

function matches(i: Item, q: string): boolean {
  return [i.label, i.mono, i.sub].some((s) => s?.toLowerCase().includes(q))
}
const rows = computed<Row[]>(() => {
  void store.version
  const q = props.filter.trim().toLowerCase()
  const out: Row[] = []
  const walk = (items: Item[], depth: number, parent: Item | null): boolean => {
    let any = false
    for (const i of items) {
      const kids = childrenOf(i) ?? []
      if (q) {
        const start = out.length
        out.push({ item: i, depth, parent })
        const childHit = walk(kids, depth + 1, i)
        if (!matches(i, q) && !childHit) out.length = start
        else any = true
      } else {
        out.push({ item: i, depth, parent })
        any = true
        if (expanded.value.has(i.key)) walk(kids, depth + 1, i)
      }
    }
    return any
  }
  walk(props.items, 0, null)
  return out
})

// --- Auswahl ------------------------------------------------------------------------------------
const selectedKeys = computed(() => new Set(store.selection.map((s) => s.key)))
const focus = ref<string | null>(null)
const anchor = ref<string | null>(null)
const container = ref<HTMLElement | null>(null)

function click(e: MouseEvent, row: Row) {
  container.value?.focus()
  const key = row.item.key
  focus.value = key
  if (e.metaKey || e.ctrlKey) {
    const sel = store.selection.filter((s) => s.key !== key)
    select(selectedKeys.value.has(key) ? sel : [...sel, row.item])
    anchor.value = key
  } else if (e.shiftKey && anchor.value) {
    const keys = rows.value.map((r) => r.item.key)
    const [a, b] = [keys.indexOf(anchor.value), keys.indexOf(key)].sort((x, y) => x - y)
    select(rows.value.slice(a, b + 1).map((r) => r.item))
  } else {
    anchor.value = key
    select([row.item])
  }
}

function moveFocus(delta: number, extend: boolean) {
  const keys = rows.value.map((r) => r.item.key)
  const idx = Math.max(0, Math.min(keys.length - 1, keys.indexOf(focus.value ?? keys[0]) + delta))
  const row = rows.value[idx]
  if (!row) return
  focus.value = row.item.key
  if (extend && anchor.value) {
    const [a, b] = [keys.indexOf(anchor.value), idx].sort((x, y) => x - y)
    select(rows.value.slice(a, b + 1).map((r) => r.item))
  } else {
    anchor.value = row.item.key
    select([row.item])
  }
  nextTick(() => container.value?.querySelector('.row.focus')?.scrollIntoView({ block: 'nearest' }))
}

async function keydown(e: KeyboardEvent) {
  if (renaming.value) return
  const row = rows.value.find((r) => r.item.key === focus.value)
  switch (e.key) {
    case 'ArrowDown': e.preventDefault(); moveFocus(1, e.shiftKey); break
    case 'ArrowUp': e.preventDefault(); moveFocus(-1, e.shiftKey); break
    case 'ArrowRight':
      e.preventDefault()
      if (row && hasChildren(row.item) && !expanded.value.has(row.item.key)) await toggle(row.item, true)
      else moveFocus(1, false)
      break
    case 'ArrowLeft':
      e.preventDefault()
      if (row && expanded.value.has(row.item.key)) toggle(row.item, false)
      else if (row?.parent) { focus.value = row.parent.key; select([row.parent]) }
      break
    case 'Enter': if (row) { e.preventDefault(); emit('open', row.item) } break
    case 'F2': if (row) { e.preventDefault(); startRename(row.item) } break
    case 'Delete':
    case 'Backspace':
      if (store.selection.length) { e.preventDefault(); emit('remove', store.selection) }
      break
    case 'a':
      if (e.metaKey || e.ctrlKey) { e.preventDefault(); select(rows.value.map((r) => r.item)) }
      break
  }
}

// --- Umbenennen ---------------------------------------------------------------------------------
const renaming = ref<string | null>(null)
const renameText = ref('')
function startRename(i: Item) {
  if (!props.renameable?.(i)) return
  renaming.value = i.key
  renameText.value = i.name ?? i.label
  nextTick(() => {
    const el = container.value?.querySelector<HTMLInputElement>('input.rename')
    el?.focus()
    el?.select()
  })
}
function commitRename(i: Item) {
  if (renaming.value !== i.key) return
  renaming.value = null
  if (renameText.value.trim() !== (i.name ?? i.label)) emit('rename', i, renameText.value.trim())
  container.value?.focus()
}
defineExpose({ startRename, reveal })

// --- Kontextmenü ----------------------------------------------------------------------------------
const menuShow = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const menuEntries = ref<MenuEntry[]>([])
function contextmenu(e: MouseEvent, row: Row) {
  e.preventDefault()
  if (!selectedKeys.value.has(row.item.key)) {
    anchor.value = row.item.key
    select([row.item])
  }
  focus.value = row.item.key
  const sel = selectedKeys.value.has(row.item.key) ? store.selection : [row.item]
  menuEntries.value = props.menu(sel)
  if (!menuEntries.value.length) return
  menuX.value = e.clientX
  menuY.value = e.clientY
  menuShow.value = true
}
const menuOptions = computed<DropdownOption[]>(() =>
  menuEntries.value.map((m) =>
    m.divider ? { type: 'divider', key: m.key } : { label: m.label, key: m.key, disabled: m.disabled, props: m.danger ? { style: 'color:#d03050' } : undefined },
  ),
)
function menuSelect(key: string) {
  menuShow.value = false
  menuEntries.value.find((m) => m.key === key)?.run?.()
}

// --- Ziehen & Ablegen -------------------------------------------------------------------------
const dropKey = ref<string | null>(null)
const dropLabel = ref('')
let hoverTimer: number | undefined
function dragstart(e: DragEvent, row: Row) {
  const items = selectedKeys.value.has(row.item.key) ? store.selection : [row.item]
  if (!selectedKeys.value.has(row.item.key)) select([row.item])
  store.drag = items
  e.dataTransfer?.setData('text/plain', items.map((i) => `${i.mono ?? ''} ${i.label}`).join('\n'))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copyMove'
}
function dragover(e: DragEvent, row: Row) {
  if (!store.drag) return
  const action = dropAction(row.item, store.drag, e.altKey)
  if (action) {
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = e.altKey ? 'copy' : 'move'
    if (dropKey.value !== row.item.key) {
      clearTimeout(hoverTimer)
      if (hasChildren(row.item) && !expanded.value.has(row.item.key))
        hoverTimer = window.setTimeout(() => toggle(row.item, true), 700)
    }
    dropKey.value = row.item.key
    dropLabel.value = action.label
  } else if (dropKey.value === row.item.key) {
    dropKey.value = null
  } else if (hasChildren(row.item) && !expanded.value.has(row.item.key)) {
    // auch über nicht passenden Knoten aufklappen, um tiefer zu zielen
    clearTimeout(hoverTimer)
    hoverTimer = window.setTimeout(() => toggle(row.item, true), 700)
  }
}
async function drop(e: DragEvent, row: Row) {
  e.preventDefault()
  clearTimeout(hoverTimer)
  const items = store.drag
  dropKey.value = null
  store.drag = null
  if (!items) return
  await dropAction(row.item, items, e.altKey)?.apply()
}
function dragend() {
  store.drag = null
  dropKey.value = null
  clearTimeout(hoverTimer)
}

// --- Von außen anspringen (Befehlspalette) ----------------------------------------------------------
async function reveal(key: string): Promise<boolean> {
  const path: Item[] = []
  const find = (items: Item[]): boolean => {
    for (const i of items) {
      path.push(i)
      if (i.key === key) return true
      if (find(childrenOf(i) ?? [])) return true
      path.pop()
    }
    return false
  }
  if (!find(props.items)) return false
  for (const p of path.slice(0, -1)) await toggle(p, true)
  const target = path[path.length - 1]
  focus.value = target.key
  anchor.value = target.key
  await select([target])
  await nextTick()
  container.value?.querySelector('.row.focus')?.scrollIntoView({ block: 'center' })
  container.value?.focus()
  return true
}
</script>

<template>
  <div ref="container" class="tree" tabindex="0" @keydown="keydown">
    <div
      v-for="row in rows"
      :key="row.item.key"
      class="row"
      :class="{
        sel: selectedKeys.has(row.item.key),
        focus: focus === row.item.key,
        drop: dropKey === row.item.key,
        warn: row.item.warn,
      }"
      :style="{ paddingLeft: `${6 + row.depth * 16}px` }"
      :title="dropKey === row.item.key ? dropLabel : undefined"
      draggable="true"
      @click="click($event, row)"
      @dblclick="emit('open', row.item)"
      @contextmenu="contextmenu($event, row)"
      @dragstart="dragstart($event, row)"
      @dragover="dragover($event, row)"
      @dragleave="dropKey === row.item.key && (dropKey = null)"
      @drop="drop($event, row)"
      @dragend="dragend"
    >
      <span class="twisty" @click.stop="toggle(row.item)">{{ hasChildren(row.item) ? (expanded.has(row.item.key) || filter ? '▾' : '▸') : '' }}</span>
      <span class="kind" :data-kind="row.item.kind" />
      <span v-if="row.item.mono" class="mono addr">{{ row.item.mono }}</span>
      <input
        v-if="renaming === row.item.key"
        v-model="renameText"
        class="rename"
        @click.stop
        @keydown.enter.stop="commitRename(row.item)"
        @keydown.esc.stop="renaming = null"
        @blur="commitRename(row.item)"
      />
      <span v-else class="label">{{ row.item.label }}</span>
      <span v-if="row.item.sub" class="sub">{{ row.item.sub }}</span>
      <span v-if="row.item.badge" class="badge" :class="{ warnbadge: row.item.warn }">{{ row.item.badge }}</span>
    </div>
    <p v-if="!rows.length" class="empty">{{ filter ? 'Nichts gefunden' : 'Leer' }}</p>
    <div v-if="dropKey" class="drophint">{{ dropLabel }}<span> · ⌥ = {{ store.drag?.[0]?.kind === 'ga' ? 'kopieren/als sendend' : 'kopieren' }}</span></div>
    <NDropdown
      trigger="manual"
      placement="bottom-start"
      :show="menuShow"
      :x="menuX"
      :y="menuY"
      :options="menuOptions"
      size="small"
      @select="menuSelect"
      @clickoutside="menuShow = false"
    />
  </div>
</template>

<style scoped>
.tree { position: relative; height: 100%; overflow: auto; outline: none; font-size: 13px; user-select: none; padding: 2px 0 24px; }
.row { display: flex; align-items: center; gap: 6px; height: 24px; padding-right: 8px; white-space: nowrap; cursor: default; border-radius: 4px; margin: 0 4px; }
.row:hover { background: var(--kl-hover); }
.row.sel { background: var(--kl-sel); }
.tree:focus .row.focus { box-shadow: inset 0 0 0 1px var(--kl-accent); }
.row.drop { background: var(--kl-accent-soft); box-shadow: inset 0 0 0 1px var(--kl-accent); }
.twisty { width: 12px; flex: none; color: var(--kl-muted); text-align: center; cursor: pointer; }
.kind { width: 8px; height: 8px; border-radius: 2px; flex: none; background: var(--kl-muted); opacity: 0.6; }
.kind[data-kind='ga'] { background: #2f7d6d; opacity: 1; border-radius: 50%; }
.kind[data-kind='link'] { background: #2f7d6d; opacity: 0.7; border-radius: 50%; }
.kind[data-kind='range'], .kind[data-kind='folder'], .kind[data-kind='space'] { background: #b08a2e; opacity: 0.9; }
.kind[data-kind='device'], .kind[data-kind='product'] { background: #3b6fb6; opacity: 1; }
.kind[data-kind='object'] { background: #7a5bb5; opacity: 0.9; }
.kind[data-kind='line'], .kind[data-kind='area'] { background: #666; }
.addr { color: var(--kl-text); opacity: 0.85; flex: none; }
.label { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.sub { color: var(--kl-muted); font-size: 12px; flex: none; }
.badge { margin-left: auto; font-size: 11px; color: var(--kl-muted); background: var(--kl-hover); border-radius: 8px; padding: 0 6px; flex: none; }
.badge.warnbadge { color: #b45309; }
.row.warn .label { color: var(--kl-muted); }
.rename { font: inherit; padding: 1px 4px; border: 1px solid var(--kl-accent); border-radius: 3px; background: var(--kl-bg); color: var(--kl-text); min-width: 160px; }
.empty { color: var(--kl-muted); padding: 12px; }
.drophint { position: sticky; bottom: 0; margin: 6px; padding: 4px 8px; font-size: 12px; background: var(--kl-accent); color: #fff; border-radius: 4px; }
.drophint span { opacity: 0.8; }
</style>
