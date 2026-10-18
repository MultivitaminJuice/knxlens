<script setup lang="ts">
// Rahmen: Kopfleiste, frei anordenbare Fenster (dockview), Dialoge, Tastenkürzel.
import { computed, h, markRaw, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { NButton, NDropdown, useDialog, useMessage, useOsTheme, type DropdownOption } from 'naive-ui'
import { api as http } from '../api'
import { DockviewVue, themeGithubDark, themeGithubLight, type DockviewApi, type DockviewReadyEvent } from 'dockview-vue'
import 'dockview-vue/dist/styles/dockview.css'
import DockPane from './DockPane.vue'
import HeaderActions from './HeaderActions.vue'
import ParamDialog from './ParamDialog.vue'
import CommandPalette from './CommandPalette.vue'
import ProjectMenu from './ProjectMenu.vue'
import { loadAll, panes, redo, save, store, undo, VIEW_TITLES, type TreeKind, type ViewKind } from '../store'

const message = useMessage()
const dialog = useDialog()

async function showOrphans() {
  const list = await http.orphans()
  const d = dialog.warning({
    title: 'Verwaiste Zuordnungen',
    content: () => h('div', { class: 'orphans' }, [
      h('p', 'Diese Gruppenadressen hängen an Objekten, die es bei den aktuellen Parametern nicht gibt. Sie werden nicht ins Gerät geladen.'),
      ...list.map((dev) => h('div', [
        h('b', `${dev.pa ?? '–'} ${dev.name}`),
        h('ul', dev.orphans.map((o) => h('li', [`${o.number} ${o.text}`, h('span', { class: 'muted' }, ` → ${o.gas.join(', ')}`)]))),
      ])),
    ]),
    positiveText: 'Alle lösen',
    negativeText: 'Schließen',
    onPositiveClick: async () => {
      const r = await http.unlinkOrphans(null)
      await loadAll()
      store.notify('success', `${r.unlinked} Zuordnung(en) gelöst – ⌘Z macht es rückgängig`)
      d.destroy()
    },
  })
}
store.notify = (type, text) => message[type](text, { duration: type === 'error' ? 5000 : 2500 })

const os = useOsTheme()
const dockTheme = computed(() => (os.value === 'dark' ? themeGithubDark : themeGithubLight))
// dockview erwartet einen allgemeinen Komponententyp; DockPane hat typisierte Props (gleich zur Laufzeit)
const components = { pane: markRaw(DockPane) as never }
const headerActions = markRaw(HeaderActions) as never

const api = shallowRef<DockviewApi | null>(null)
const palette = ref(false)
const error = ref('')
const LAYOUT_KEY = 'knxlens:layout:v1'

type View = ViewKind
let counter = 0
function nextId(kind: View) {
  const used = new Set(api.value?.panels.map((p) => p.id) ?? [])
  let id: string
  do id = `${kind}-${++counter}`
  while (used.has(id))
  return id
}

function addView(kind: View, position?: Record<string, unknown>) {
  const a = api.value
  if (!a) return
  return a.addPanel({
    id: nextId(kind), component: 'pane', title: VIEW_TITLES[kind], params: { kind },
    ...(position ? { position: position as never } : {}),
    ...(kind === 'inspector' ? { initialWidth: 320 } : {}),
  })
}

// Vorlagen: schnelle Anordnungen; danach frei per Ziehen an die Ränder veränderbar
const PRESETS: Record<string, { label: string; build: () => void }> = {
  ets: {
    label: 'ETS-Standard: Topologie über GAs | Gerät über Gruppenadresse',
    build: () => {
      const t = addView('topology')!
      const d = addView('device', { referencePanel: t.id, direction: 'right' })!
      addView('groupaddresses', { referencePanel: t.id, direction: 'below' })
      addView('gadetail', { referencePanel: d.id, direction: 'below' })
      // linke Spalte schmaler, wie in der ETS
      try { api.value?.getPanel(t.id)?.group.api.setSize({ width: 420 }) } catch { /* Größe ist nur Komfort */ }
    },
  },
  classic: {
    label: 'Topologie | Gruppenadressen | Eigenschaften',
    build: () => {
      const t = addView('topology')!
      addView('groupaddresses', { referencePanel: t.id, direction: 'right' })
      addView('inspector', { direction: 'right' })
    },
  },
  stacked: {
    label: 'Topologie über Gruppenadressen | Eigenschaften',
    build: () => {
      const t = addView('topology')!
      addView('groupaddresses', { referencePanel: t.id, direction: 'below' })
      addView('inspector', { direction: 'right' })
    },
  },
  two: {
    label: 'Topologie | Gruppenadressen',
    build: () => {
      const t = addView('topology')!
      addView('groupaddresses', { referencePanel: t.id, direction: 'right' })
    },
  },
  twoStacked: {
    label: 'Topologie über Gruppenadressen',
    build: () => {
      const t = addView('topology')!
      addView('groupaddresses', { referencePanel: t.id, direction: 'below' })
    },
  },
  one: { label: 'Nur Gruppenadressen', build: () => addView('groupaddresses') },
}

function applyPreset(key: string) {
  api.value?.clear()
  PRESETS[key].build()
}

const viewMenu = computed<DropdownOption[]>(() => [
  { type: 'group', label: 'Fenster öffnen', key: 'g1', children: (Object.keys(VIEW_TITLES) as View[]).map((k) => ({ label: VIEW_TITLES[k], key: `add:${k}` })) },
  { type: 'divider', key: 'd1' },
  { type: 'group', label: 'Anordnung', key: 'g2', children: Object.entries(PRESETS).map(([k, p]) => ({ label: p.label, key: `preset:${k}` })) },
])
function onViewMenu(key: string) {
  const [kind, value] = key.split(':')
  if (kind === 'add') addView(value as View, { direction: 'right' })
  if (kind === 'preset') applyPreset(value)
}

function onReady(event: DockviewReadyEvent) {
  api.value = event.api
  let restored = false
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (raw) {
      event.api.fromJSON(JSON.parse(raw))
      restored = event.api.panels.length > 0
    }
  } catch {
    restored = false
  }
  if (!restored) applyPreset('classic')
  event.api.onDidLayoutChange(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(event.api.toJSON()))
    } catch {
      /* Layout wird dann nur nicht gemerkt */
    }
  })
}

async function reveal(kind: TreeKind, key: string) {
  // passendes offenes Fenster nehmen, sonst eines öffnen
  let entry = [...panes.entries()].find(([, h]) => h.kind === kind)
  if (!entry) {
    addView(kind, { direction: 'right' })
    await new Promise((r) => setTimeout(r, 60))
    entry = [...panes.entries()].find(([, h]) => h.kind === kind)
  }
  if (!entry) return
  api.value?.getPanel(entry[0])?.api.setActive()
  await entry[1].reveal(key)
}

function onKey(e: KeyboardEvent) {
  const mod = e.metaKey || e.ctrlKey
  const typing = (e.target as HTMLElement)?.closest('input, textarea, [contenteditable]')
  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return }
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); palette.value = true; return }
  if (typing) return
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); (e.shiftKey ? redo : undo)() }
}
function onLeave(e: BeforeUnloadEvent) {
  if (store.project?.dirty) e.preventDefault()
}
store.reveal = reveal
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', onLeave)
  try {
    await loadAll()
    document.title = `${store.project?.name} · knxlens`
  } catch (e) {
    error.value = String(e)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', onLeave)
})
</script>

<template>
  <div class="shell">
    <header class="bar">
      <strong class="brand">knxlens</strong>
      <span v-if="store.project" class="proj">{{ store.project.name }}</span>
      <span v-if="store.project" class="muted stats">{{ store.project.devices }} Geräte · {{ store.project.group_addresses }} GAs · {{ store.project.links }} Verknüpfungen</span>
      <span class="spacer" />
      <button v-if="store.project?.orphans" class="orph" title="Gruppenadressen an Objekten, die es bei den aktuellen Parametern nicht gibt" @click="showOrphans">
        ⚠ {{ store.project.orphans }} verwaist
      </button>
      <button class="search" @click="palette = true">Suchen &amp; Befehle <kbd>⌘K</kbd></button>
      <ProjectMenu />
      <NDropdown trigger="click" :options="viewMenu" size="small" @select="onViewMenu">
        <NButton size="small" quaternary>Ansicht ▾</NButton>
      </NDropdown>
      <NButton size="small" quaternary :disabled="!store.project?.undo" :title="store.project?.undo ? `Rückgängig: ${store.project.undo} (⌘Z)` : 'Nichts rückgängig zu machen'" @click="undo">↶</NButton>
      <NButton size="small" quaternary :disabled="!store.project?.redo" :title="store.project?.redo ? `Wiederholen: ${store.project.redo} (⇧⌘Z)` : 'Nichts zu wiederholen'" @click="redo">↷</NButton>
      <NButton size="small" :type="store.project?.dirty ? 'primary' : 'default'" :disabled="!store.project?.dirty" @click="save" title="Speichern (⌘S)">
        {{ store.project?.dirty ? 'Speichern' : 'Gespeichert' }}
      </NButton>
    </header>
    <p v-if="error" class="error">{{ error }}</p>
    <!-- Container gibt die Größe vor: dockview hat keine einzelne Wurzel, Scoped-Styles greifen dort nicht -->
    <div class="dock">
      <DockviewVue style="height: 100%; width: 100%" :theme="dockTheme" :components="components"
                   :right-header-actions-component="headerActions" @ready="onReady" />
    </div>
    <ParamDialog />
    <CommandPalette v-model:show="palette" @reveal="reveal" />
  </div>
</template>

<style scoped>
.shell { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.bar { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-bottom: 1px solid var(--kl-border); background: var(--kl-chrome); min-width: 0; }
.bar > * { flex: none; }
.brand { font-size: 14px; }
.proj { font-weight: 600; flex: 0 1 auto !important; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stats { font-size: 12px; flex: 0 1 auto !important; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 900px) { .stats { display: none; } }
.spacer { flex: 1 1 0 !important; }
.orph { font: inherit; font-size: 12px; border: 1px solid #f0c9a4; background: #fdf0e6; color: #8a4b0f; border-radius: 6px; padding: 3px 8px; cursor: pointer; }
.search { font: inherit; font-size: 12px; color: var(--kl-muted); background: var(--kl-bg); border: 1px solid var(--kl-border); border-radius: 6px; padding: 4px 8px 4px 10px; cursor: pointer; display: flex; gap: 10px; align-items: center; min-width: 0; width: 220px; flex: 0 1 220px !important; justify-content: space-between; }
kbd { font-family: var(--kl-mono); font-size: 11px; background: var(--kl-hover); border-radius: 4px; padding: 0 4px; }
.dock { flex: 1; min-height: 0; position: relative; }
.error { color: #d03050; padding: 8px 12px; margin: 0; }
/* dockview an die eigenen Farben angleichen */
.dock :deep(.dv-tab) { font-size: 12.5px; }
.dock :deep(.dv-groupview) { background: var(--kl-bg); }
/* Ablagezonen beim Ziehen deutlich zeigen (im Theme fast unsichtbar) */
.dock :deep(.dv-shell) {
  --dv-drag-over-background-color: rgb(37 99 235 / 22%);
  --dv-drag-over-border-color: #2563eb;
  --dv-drag-over-border: 2px solid #2563eb;
}
.dock :deep(.dv-drop-target-selection) { border: 2px solid #2563eb; border-radius: 4px; box-sizing: border-box; }
</style>
