<script setup lang="ts">
// Rahmen: Kopfleiste, zwei Arbeitsbereiche, Inspector, Dialoge, Tastenkürzel.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NSplit, useMessage } from 'naive-ui'
import WorkPane from './WorkPane.vue'
import Inspector from './Inspector.vue'
import ParamDialog from './ParamDialog.vue'
import CommandPalette from './CommandPalette.vue'
import { loadAll, redo, save, store, undo, type TreeKind } from '../store'

const message = useMessage()
store.notify = (type, text) => message[type](text, { duration: type === 'error' ? 5000 : 2500 })

const left = ref<InstanceType<typeof WorkPane> | null>(null)
const right = ref<InstanceType<typeof WorkPane> | null>(null)
const palette = ref(false)
const showInspector = ref(true)
const error = ref('')

async function reveal(kind: TreeKind, key: string) {
  // im Bereich anspringen, der den Reiter schon zeigt – sonst rechts umschalten
  const pane = left.value?.tab === kind ? left.value : right.value
  await pane?.reveal(kind, key)
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
      <button class="search" @click="palette = true">Suchen &amp; Befehle <kbd>⌘K</kbd></button>
      <NButton size="small" quaternary :disabled="!store.project?.undo" :title="store.project?.undo ? `Rückgängig: ${store.project.undo} (⌘Z)` : 'Nichts rückgängig zu machen'" @click="undo">↶</NButton>
      <NButton size="small" quaternary :disabled="!store.project?.redo" :title="store.project?.redo ? `Wiederholen: ${store.project.redo} (⇧⌘Z)` : 'Nichts zu wiederholen'" @click="redo">↷</NButton>
      <NButton size="small" :type="store.project?.dirty ? 'primary' : 'default'" :disabled="!store.project?.dirty" @click="save" title="Speichern (⌘S)">
        {{ store.project?.dirty ? 'Speichern' : 'Gespeichert' }}
      </NButton>
      <NButton size="small" quaternary :title="showInspector ? 'Eigenschaften ausblenden' : 'Eigenschaften einblenden'" @click="showInspector = !showInspector">☰</NButton>
    </header>
    <p v-if="error" class="error">{{ error }}</p>
    <NSplit class="main" direction="horizontal" :default-size="showInspector ? 0.76 : 1" :min="0.5" :max="showInspector ? 0.88 : 1" :disabled="!showInspector">
      <template #1>
        <NSplit direction="horizontal" :default-size="0.5" :min="0.25" :max="0.75">
          <template #1><WorkPane ref="left" initial="topology" pane-id="left" /></template>
          <template #2><WorkPane ref="right" initial="groupaddresses" pane-id="right" /></template>
        </NSplit>
      </template>
      <template #2><Inspector v-if="showInspector" /></template>
    </NSplit>
    <ParamDialog />
    <CommandPalette v-model:show="palette" @reveal="reveal" />
  </div>
</template>

<style scoped>
.shell { height: 100%; display: flex; flex-direction: column; }
.bar { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-bottom: 1px solid var(--kl-border); background: var(--kl-chrome); }
.brand { font-size: 14px; }
.proj { font-weight: 600; }
.stats { font-size: 12px; }
.spacer { flex: 1; }
.search { font: inherit; font-size: 12px; color: var(--kl-muted); background: var(--kl-bg); border: 1px solid var(--kl-border); border-radius: 6px; padding: 4px 8px 4px 10px; cursor: pointer; display: flex; gap: 10px; align-items: center; min-width: 200px; justify-content: space-between; }
kbd { font-family: var(--kl-mono); font-size: 11px; background: var(--kl-hover); border-radius: 4px; padding: 0 4px; }
.main { flex: 1; min-height: 0; }
.main :deep(.n-split-pane-1), .main :deep(.n-split-pane-2) { overflow: hidden; height: 100%; }
.main :deep(.n-split__resize-trigger) { background: var(--kl-border); }
.error { color: #d03050; padding: 8px 12px; margin: 0; }
</style>
