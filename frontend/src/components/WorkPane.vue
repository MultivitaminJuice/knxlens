<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NInput, useDialog } from 'naive-ui'
import TreeView, { type MenuEntry } from './TreeView.vue'
import { run, select, store, type Item, type TreeKind } from '../store'

const props = defineProps<{ initial: TreeKind; paneId: string }>()
const tab = ref<TreeKind>(props.initial)
const filter = ref('')
const tree = ref<InstanceType<typeof TreeView> | null>(null)
const dialog = useDialog()

const tabs: { value: TreeKind; label: string }[] = [
  { value: 'groupaddresses', label: 'Gruppenadressen' },
  { value: 'topology', label: 'Topologie' },
  { value: 'buildings', label: 'Gebäude' },
  { value: 'catalog', label: 'Katalog' },
]
const items = computed(() => store.trees[tab.value])

function confirm(title: string, content: string, ok: () => unknown) {
  dialog.warning({ title, content, positiveText: 'Löschen', negativeText: 'Abbrechen', onPositiveClick: () => { ok() } })
}

function remove(sel: Item[]) {
  const kinds = new Set(sel.map((s) => s.kind))
  if (kinds.size !== 1) return
  const kind = sel[0].kind
  if (kind === 'ga') {
    const addrs = sel.map((s) => s.address!)
    const used = sel.reduce((n, s) => n + (Number(s.badge) || 0), 0)
    confirm(`${addrs.length} Gruppenadresse(n) löschen?`,
      used ? `Dabei werden ${used} Verknüpfung(en) gelöst.` : 'Sie sind mit keinem Objekt verknüpft.',
      () => run('ga_delete', { addresses: addrs }, 'Gelöscht'))
  } else if (kind === 'range' && sel.length === 1) {
    const n = (sel[0].children ?? []).length
    confirm(`„${sel[0].label}“ löschen?`, n ? 'Alle enthaltenen Gruppen und Gruppenadressen werden mit gelöscht, ihre Verknüpfungen gelöst.' : 'Die Gruppe ist leer.',
      () => run('range_delete', { uid: sel[0].rangeUid, force: true }, 'Gelöscht'))
  } else if (kind === 'device') {
    confirm(`${sel.length} Gerät(e) löschen?`, 'Parameter und Verknüpfungen dieser Geräte gehen verloren.',
      () => run('device_delete', { uids: sel.map((s) => s.uid) }, 'Gelöscht'))
  } else if (kind === 'link') {
    for (const s of sel) run('unlink', { device_uid: s.uid, ref: s.ref, address: s.address }, 'Verknüpfung gelöst')
  }
}

function rename(i: Item, name: string) {
  if (i.kind === 'range') run('range_rename', { uid: i.rangeUid, name }, 'Umbenannt')
  else if (i.kind === 'ga') run('ga_update', { address: i.address, name }, 'Umbenannt')
  else if (i.kind === 'device') run('device_update', { uid: i.uid, name }, 'Umbenannt')
}
const renameable = (i: Item) => ['range', 'ga', 'device'].includes(i.kind)

function open(i: Item) {
  if (i.kind === 'device' || i.kind === 'object') store.paramsFor = i.uid ?? null
}

async function addGa(range: Item) {
  const addr = (await run('ga_add', { range_uid: range.rangeUid, name: '' }, 'Gruppenadresse angelegt')) as string | undefined
  if (addr) setTimeout(async () => {
    if (await tree.value?.reveal(`ga:${addr}`)) tree.value?.startRename(store.selection[0])
  }, 50)
}
async function addRange(parent: Item | null) {
  const uid = (await run('range_add', { parent_uid: parent?.rangeUid ?? null }, parent ? 'Mittelgruppe angelegt' : 'Hauptgruppe angelegt')) as string | undefined
  if (uid) setTimeout(async () => {
    if (await tree.value?.reveal(uid)) tree.value?.startRename(store.selection[0])
  }, 50)
}

function paste(line: Item) {
  if (store.clipboard?.kind === 'devices')
    run('device_copy', { uids: store.clipboard.uids, area: line.area, line: line.line }, 'Eingefügt')
}

function menu(sel: Item[]): MenuEntry[] {
  const one = sel.length === 1 ? sel[0] : null
  const kind = new Set(sel.map((s) => s.kind)).size === 1 ? sel[0].kind : null
  const m: MenuEntry[] = []
  if (one?.kind === 'range' && one.level === 'main') {
    m.push({ key: 'addm', label: 'Neue Mittelgruppe', run: () => addRange(one) })
  }
  if (one?.kind === 'range' && one.level === 'middle') {
    m.push({ key: 'addga', label: 'Neue Gruppenadresse', run: () => addGa(one) })
  }
  if (one?.kind === 'device') {
    m.push({ key: 'params', label: 'Parameter bearbeiten …', run: () => (store.paramsFor = one.uid!) })
  }
  if (one && renameable(one)) m.push({ key: 'ren', label: 'Umbenennen  (F2)', run: () => tree.value?.startRename(one) })
  if (kind === 'device') {
    m.push({ key: 'copy', label: `Kopieren  (⌘C)`, run: () => (store.clipboard = { kind: 'devices', uids: sel.map((s) => s.uid!) }) })
  }
  if (one?.kind === 'line') {
    m.push({ key: 'paste', label: store.clipboard ? `${store.clipboard.uids.length} Gerät(e) einfügen  (⌘V)` : 'Einfügen', disabled: !store.clipboard, run: () => paste(one) })
  }
  if (one?.kind === 'link') {
    m.push({ key: 'send', label: 'Als sendende GA', disabled: one.badge === 'sendend', run: () => run('set_sending', { device_uid: one.uid, ref: one.ref, address: one.address }, 'Sendende GA gesetzt') })
  }
  if (kind && ['ga', 'range', 'device', 'link'].includes(kind)) {
    m.push({ key: 'div', label: '', divider: true })
    m.push({ key: 'del', label: kind === 'link' ? 'Verknüpfung lösen  (⌫)' : 'Löschen  (⌫)', danger: true, run: () => remove(sel) })
  }
  return m
}

function keydown(e: KeyboardEvent) {
  if (!(e.metaKey || e.ctrlKey)) return
  if (e.key === 'c' && store.selection.length && store.selection.every((s) => s.kind === 'device')) {
    store.clipboard = { kind: 'devices', uids: store.selection.map((s) => s.uid!) }
    store.notify('info', `${store.clipboard.uids.length} Gerät(e) kopiert – auf einer Linie ⌘V`)
    e.preventDefault()
  }
  if (e.key === 'v' && store.selection.length === 1 && store.selection[0].kind === 'line') {
    paste(store.selection[0])
    e.preventDefault()
  }
}

async function reveal(kind: TreeKind, key: string) {
  tab.value = kind
  filter.value = ''
  await new Promise((r) => setTimeout(r, 0))
  return tree.value?.reveal(key)
}
defineExpose({ reveal, tab })

const hint = computed(() => ({
  groupaddresses: 'GA auf ein Objekt ziehen = verknüpfen · auf eine Mittelgruppe = verschieben (⌥ kopieren)',
  topology: 'Gerät aufklappen zeigt Objekte · Gerät auf Linie ziehen = verschieben (⌥ kopieren) · Doppelklick = Parameter',
  buildings: 'Gebäudestruktur – Bearbeiten folgt',
  catalog: 'Produkt auf eine Linie in der Topologie ziehen = Gerät einfügen',
})[tab.value])
</script>

<template>
  <section class="pane" @keydown="keydown" @focusin="() => {}">
    <nav class="tabs">
      <button v-for="t in tabs" :key="t.value" :class="{ on: tab === t.value }" @click="tab = t.value; select([])">{{ t.label }}</button>
    </nav>
    <div class="tools">
      <NInput v-model:value="filter" size="small" clearable placeholder="Filtern …" class="filter" />
      <NButton v-if="tab === 'groupaddresses'" size="small" @click="addRange(null)" title="Neue Hauptgruppe">+ Hauptgruppe</NButton>
    </div>
    <TreeView
      ref="tree"
      :key="tab"
      :items="items"
      :tree-id="`${paneId}-${tab}`"
      :filter="filter"
      :menu="menu"
      :renameable="renameable"
      :default-depth="tab === 'topology' ? 2 : 1"
      class="body"
      @open="open"
      @rename="rename"
      @remove="remove"
    />
    <footer class="hint">{{ hint }}</footer>
  </section>
</template>

<style scoped>
.pane { height: 100%; display: flex; flex-direction: column; min-width: 0; background: var(--kl-bg); }
.tabs { display: flex; gap: 2px; padding: 6px 8px 0; border-bottom: 1px solid var(--kl-border); }
.tabs button { font: inherit; font-size: 13px; padding: 6px 10px; border: 0; background: none; color: var(--kl-muted); border-bottom: 2px solid transparent; cursor: pointer; }
.tabs button.on { color: var(--kl-text); border-bottom-color: var(--kl-accent); font-weight: 600; }
.tools { display: flex; gap: 6px; padding: 6px 8px; }
.filter { flex: 1; }
.body { flex: 1; min-height: 0; }
.hint { font-size: 11px; color: var(--kl-muted); padding: 4px 10px; border-top: 1px solid var(--kl-border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
