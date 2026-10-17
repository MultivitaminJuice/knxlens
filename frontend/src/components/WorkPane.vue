<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NInput, useDialog } from 'naive-ui'
import TreeView, { type MenuEntry } from './TreeView.vue'
import { api, ApiError } from '../api'
import { loadAll, panes, run, store, type Item, type TreeKind } from '../store'

// Ein Fenster zeigt genau eine Ansicht; Reiter, Schließen und Anordnen übernimmt dockview.
const props = defineProps<{ kind: TreeKind; paneId: string }>()
const tab = computed(() => props.kind)
const filter = ref('')
const tree = ref<InstanceType<typeof TreeView> | null>(null)
const dialog = useDialog()

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
  } else if (kind === 'device' && tab.value === 'buildings') {
    // im Gebäudebaum heißt Löschen: nur die Zuordnung lösen, das Gerät bleibt im Projekt
    run('space_unassign', { device_uids: sel.map((s) => s.uid) }, 'Zuordnung gelöst')
  } else if (kind === 'space') {
    const n = sel.reduce((c, s) => c + (s.children ?? []).length, 0)
    confirm(sel.length === 1 ? `„${sel[0].label}“ löschen?` : `${sel.length} Gebäudeteile löschen?`,
      n ? 'Enthaltene Gebäudeteile werden mit gelöscht. Die Geräte bleiben im Projekt, nur ihre Zuordnung geht verloren.' : 'Der Gebäudeteil ist leer.',
      () => run('space_delete', { uids: sel.map((s) => s.spaceUid) }, 'Gelöscht'))
  } else if (kind === 'area' && sel.length === 1) {
    const a = sel[0]
    const devs = (a.children ?? []).reduce((c, l) => c + (l.children ?? []).length, 0)
    confirm(`Bereich ${a.mono} löschen?`, devs ? `Alle Linien und ${devs} Gerät(e) darin werden mit gelöscht.` : 'Der Bereich enthält keine Geräte.',
      () => run('area_delete', { area: a.area, force: true }, 'Gelöscht'))
  } else if (kind === 'line' && sel.length === 1) {
    const l = sel[0]
    const devs = (l.children ?? []).length
    confirm(`Linie ${l.mono} löschen?`, devs ? `${devs} Gerät(e) auf dieser Linie werden mit gelöscht.` : 'Die Linie enthält keine Geräte.',
      () => run('line_delete', { area: l.area, line: l.line, force: true }, 'Gelöscht'))
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
  else if (i.kind === 'space') run('space_update', { uid: i.spaceUid, name }, 'Umbenannt')
  else if (i.kind === 'area') run('area_rename', { area: i.area, name }, 'Umbenannt')
  else if (i.kind === 'line') run('line_update', { area: i.area, line: i.line, name }, 'Umbenannt')
}
const renameable = (i: Item) => ['range', 'ga', 'device', 'space', 'area', 'line'].includes(i.kind)

// neu angelegtes Element zeigen und gleich zum Benennen öffnen
function revealAndRename(key: string) {
  setTimeout(async () => {
    if (await tree.value?.reveal(key)) tree.value?.startRename(store.selection[0])
  }, 50)
}
async function addArea() {
  const a = (await run('area_add', {}, 'Bereich angelegt')) as number | undefined
  if (a !== undefined) revealAndRename(`a${a}`)
}
async function addLine(area: Item) {
  const n = (await run('line_add', { area: area.area }, 'Linie angelegt')) as number | undefined
  if (n !== undefined) revealAndRename(`l${area.area}.${n}`)
}
async function addSpace(parent: Item | null, type: string) {
  const uid = (await run('space_add', { parent_uid: parent?.spaceUid ?? null, type }, `${typeName(type)} angelegt`)) as string | undefined
  if (uid) revealAndRename(uid)
}
const typeName = (t: string) => store.project?.space_types[t] ?? t

// Produktdateien (.knxprod) ins Projekt laden
const fileInput = ref<HTMLInputElement | null>(null)
async function uploadProducts(e: Event) {
  const files = [...((e.target as HTMLInputElement).files ?? [])]
  ;(e.target as HTMLInputElement).value = ''
  for (const file of files) {
    store.busy = true
    try {
      const r = await api.addProducts(file)
      const fresh = r.products.filter((p) => p.new)
      store.notify('success', fresh.length
        ? `${file.name}: ${fresh.length} neue(s) Produkt(e) – ${fresh.slice(0, 3).map((p) => p.name).join(', ')}${fresh.length > 3 ? ' …' : ''}`
        : `${file.name}: Produkte waren schon im Projekt`)
    } catch (err) {
      store.notify('error', err instanceof ApiError ? err.message : String(err))
    } finally {
      store.busy = false
    }
  }
  await loadAll()
}

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
  if (one?.kind === 'area') {
    m.push({ key: 'addl', label: 'Neue Linie', run: () => addLine(one) })
  }
  if (one?.kind === 'line') {
    const media = store.project?.media ?? {}
    m.push({ key: 'medium', label: `Medium: ${media[one.medium ?? ''] ?? one.medium}`, children: Object.entries(media).map(([id, label]) => ({
      key: `med:${id}`, label, disabled: id === one.medium, run: () => run('line_update', { area: one.area, line: one.line, medium: id }, 'Medium geändert'),
    })) })
  }
  if (one?.kind === 'space' && one.childTypes?.length) {
    m.push({ key: 'adds', label: 'Neu', children: one.childTypes.map((t) => ({ key: `new:${t}`, label: typeName(t), run: () => addSpace(one, t) })) })
  }
  if (kind === 'device' && tab.value === 'buildings') {
    m.push({ key: 'unassign', label: 'Zuordnung lösen', run: () => remove(sel) })
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
  const multi = ['ga', 'device', 'link', 'space'].includes(kind ?? '')
  if (kind && (multi || (['range', 'area', 'line'].includes(kind) && one)) && !(kind === 'device' && tab.value === 'buildings')) {
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

async function reveal(key: string) {
  filter.value = ''
  await new Promise((r) => setTimeout(r, 0))
  return tree.value?.reveal(key)
}
onMounted(() => panes.set(props.paneId, { kind: props.kind, reveal }))
onBeforeUnmount(() => panes.delete(props.paneId))

const hint = computed(() => ({
  groupaddresses: 'GA auf ein Objekt ziehen = verknüpfen · auf eine Mittelgruppe = verschieben (⌥ kopieren)',
  topology: 'Gerät aufklappen zeigt Objekte · Gerät auf Linie ziehen = verschieben (⌥ kopieren) · Doppelklick = Parameter',
  buildings: 'Gerät aus der Topologie auf einen Raum ziehen = zuordnen · Teile per Ziehen umhängen · Rechtsklick = Neu',
  catalog: 'Produkt auf eine Linie in der Topologie ziehen = Gerät einfügen · „+ Produktdatei“ lädt eine .knxprod',
})[tab.value])
</script>

<template>
  <section class="pane" @keydown="keydown" @focusin="() => {}">
    <div class="tools">
      <NInput v-model:value="filter" size="small" clearable placeholder="Filtern …" class="filter" />
      <NButton v-if="tab === 'groupaddresses'" size="small" @click="addRange(null)" title="Neue Hauptgruppe">+ Hauptgruppe</NButton>
      <NButton v-if="tab === 'topology'" size="small" @click="addArea" title="Neuer Bereich mit Bereichslinie">+ Bereich</NButton>
      <NButton v-for="t in tab === 'buildings' ? store.project?.space_root ?? [] : []" :key="t" size="small" @click="addSpace(null, t)">+ {{ typeName(t) }}</NButton>
      <template v-if="tab === 'catalog'">
        <NButton size="small" :loading="store.busy" @click="fileInput?.click()" title=".knxprod-Datei des Herstellers ins Projekt laden">+ Produktdatei</NButton>
        <input ref="fileInput" type="file" accept=".knxprod" multiple hidden @change="uploadProducts" />
      </template>
    </div>
    <TreeView
      ref="tree"
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
.tools { display: flex; gap: 6px; padding: 6px 8px; }
.filter { flex: 1; }
.body { flex: 1; min-height: 0; }
.hint { font-size: 11px; color: var(--kl-muted); padding: 4px 10px; border-top: 1px solid var(--kl-border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
