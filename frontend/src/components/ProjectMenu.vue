<script setup lang="ts">
// Menü „Projekt“: öffnen, neu, speichern unter, ETS-Import/-Export, Versionen.
import { computed, ref } from 'vue'
import { NButton, NDropdown, NInput, NModal, useDialog, type DropdownOption } from 'naive-ui'
import { api, ApiError, type ProjectFile, type VersionDiff, type VersionEntry } from '../api'
import { loadAll, save, store } from '../store'

const dialog = useDialog()
const options: DropdownOption[] = [
  { label: 'Öffnen …', key: 'open' },
  { label: 'Neues Projekt …', key: 'new' },
  { label: 'Speichern unter …', key: 'saveas' },
  { type: 'divider', key: 'd1' },
  { label: 'ETS-Projekt importieren (.knxproj) …', key: 'import' },
  { label: 'Als ETS-Projekt exportieren (.knxproj)', key: 'export' },
  { type: 'divider', key: 'd2' },
  { label: 'Version anlegen …', key: 'commit' },
  { label: 'Versionen …', key: 'versions' },
]

// --- Hilfen ------------------------------------------------------------------------------
function fail(e: unknown) {
  store.notify('error', e instanceof ApiError ? e.message : String(e))
}

async function afterSwitch() {
  store.selection = []
  store.objects = {}
  store.device = null
  store.ga = null
  await loadAll()
  document.title = `${store.project?.name} · knxlens`
}

/** Ungespeicherte Änderungen? Dann fragen: speichern, verwerfen oder abbrechen. */
function confirmLeave(): Promise<boolean | 'force'> {
  if (!store.project?.dirty) return Promise.resolve(true)
  return new Promise((resolve) => {
    dialog.warning({
      title: 'Ungespeicherte Änderungen',
      content: `„${store.project?.name}“ hat ungespeicherte Änderungen.`,
      positiveText: 'Speichern',
      negativeText: 'Verwerfen',
      closable: true,
      onPositiveClick: async () => { await save(); resolve(true) },
      onNegativeClick: () => resolve('force'),
      onClose: () => resolve(false),
      onMaskClick: () => resolve(false),
    })
  })
}

// --- Öffnen ------------------------------------------------------------------------------
const openShow = ref(false)
const files = ref<ProjectFile[]>([])
const folder = ref('')
async function showOpen() {
  const r = await api.files()
  files.value = r.files
  folder.value = r.folder
  openShow.value = true
}
async function openFile(f: ProjectFile) {
  const ok = await confirmLeave()
  if (!ok) return
  try {
    await api.openProject(f.path, ok === 'force')
    openShow.value = false
    await afterSwitch()
    store.notify('success', `${f.name} geöffnet`)
  } catch (e) { fail(e) }
}

// --- Name abfragen (neu / speichern unter) -------------------------------------------------
const nameShow = ref(false)
const nameMode = ref<'new' | 'saveas'>('new')
const nameValue = ref('')
function askName(mode: 'new' | 'saveas') {
  nameMode.value = mode
  nameValue.value = mode === 'saveas' ? `${store.project?.name ?? ''} Kopie` : ''
  nameShow.value = true
}
async function submitName() {
  const name = nameValue.value.trim()
  if (!name) return
  try {
    if (nameMode.value === 'new') {
      const ok = await confirmLeave()
      if (!ok) return
      await api.newProject(name, ok === 'force')
    } else {
      await api.saveAs(name)
    }
    nameShow.value = false
    await afterSwitch()
    store.notify('success', nameMode.value === 'new' ? `Projekt „${name}“ angelegt` : `Gespeichert als „${name}“`)
  } catch (e) { fail(e) }
}

// --- Import / Export -----------------------------------------------------------------------
const fileInput = ref<HTMLInputElement | null>(null)
async function importFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const ok = await confirmLeave()
  if (!ok) return
  store.busy = true
  try {
    await api.importProject(file, ok === 'force')
    await afterSwitch()
    store.notify('success', `${file.name} importiert`)
  } catch (err) { fail(err) } finally { store.busy = false }
}
function exportFile() {
  // Download über den Browser; ETS6 lehnt unsignierte Projekte ab (siehe README)
  window.location.href = '/api/project/export'
  store.notify('info', 'Export läuft. Hinweis: ETS6 öffnet unsignierte Projekte nicht.')
}

// --- Versionen -----------------------------------------------------------------------------
const commitShow = ref(false)
const comment = ref('')
async function commit() {
  try {
    await api.addVersion(comment.value.trim())
    commitShow.value = false
    comment.value = ''
    await loadAll()
    store.notify('success', 'Gespeichert und Version angelegt')
  } catch (e) { fail(e) }
}

const versionsShow = ref(false)
const versions = ref<VersionEntry[]>([])
const diff = ref<VersionDiff | null>(null)
const diffFor = ref<number | null>(null)
async function showVersions() {
  versions.value = await api.versions()
  diff.value = null
  diffFor.value = null
  versionsShow.value = true
}
async function showDiff(v: VersionEntry) {
  diffFor.value = v.id
  diff.value = await api.versionDiff(v.id)
}
function restore(v: VersionEntry) {
  dialog.warning({
    title: `Version vom ${when(v.ts)} zurückholen?`,
    content: 'Der aktuelle Arbeitsstand wird ersetzt. Das lässt sich mit ⌘Z rückgängig machen; gespeichert wird erst, wenn du speicherst.',
    positiveText: 'Zurückholen',
    negativeText: 'Abbrechen',
    onPositiveClick: async () => {
      try {
        await api.restoreVersion(v.id)
        versionsShow.value = false
        await afterSwitch()
        store.notify('success', 'Version zurückgeholt – ⌘Z macht es rückgängig')
      } catch (e) { fail(e) }
    },
  })
}
const diffEmpty = computed(() => {
  const d = diff.value
  return !!d && !d.devices_added.length && !d.devices_removed.length && !d.devices_changed.length && !d.gas_added.length
    && !d.gas_removed.length && !d.gas_renamed.length && !d.lines.added.length && !d.lines.removed.length && !d.spaces_changed.length
})

function when(ts: number) {
  return new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function onSelect(key: string) {
  if (key === 'open') showOpen()
  if (key === 'new') askName('new')
  if (key === 'saveas') askName('saveas')
  if (key === 'import') fileInput.value?.click()
  if (key === 'export') exportFile()
  if (key === 'commit') { comment.value = ''; commitShow.value = true }
  if (key === 'versions') showVersions()
}
defineExpose({ showOpen, showVersions })
</script>

<template>
  <NDropdown trigger="click" :options="options" size="small" @select="onSelect">
    <NButton size="small" quaternary>Projekt ▾</NButton>
  </NDropdown>
  <input ref="fileInput" type="file" accept=".knxproj" hidden @change="importFile" />

  <NModal v-model:show="openShow" preset="card" title="Projekt öffnen" class="pdlg" :bordered="false">
    <p class="muted small">Projekte im Ordner {{ folder }} und zuletzt geöffnete.</p>
    <div class="list">
      <button v-for="f in files" :key="f.path" class="item" :class="{ cur: f.current }" :disabled="f.current" @click="openFile(f)">
        <span class="nm">{{ f.name }}<span v-if="f.current" class="muted"> (geöffnet)</span></span>
        <span class="muted small">{{ when(f.mtime) }} · {{ f.folder }}</span>
      </button>
      <p v-if="!files.length" class="muted">Keine Projekte gefunden.</p>
    </div>
  </NModal>

  <NModal v-model:show="nameShow" preset="card" :title="nameMode === 'new' ? 'Neues Projekt' : 'Speichern unter'" class="pdlg small" :bordered="false">
    <NInput v-model:value="nameValue" placeholder="Name" autofocus @keyup.enter="submitName" />
    <p class="muted small">Die Datei wird im Ordner des aktuellen Projekts angelegt{{ nameMode === 'new' ? ', mit Bereich 1 und Linie 1.1' : '' }}.</p>
    <template #action>
      <div class="acts"><NButton @click="nameShow = false">Abbrechen</NButton><NButton type="primary" :disabled="!nameValue.trim()" @click="submitName">{{ nameMode === 'new' ? 'Anlegen' : 'Speichern' }}</NButton></div>
    </template>
  </NModal>

  <NModal v-model:show="commitShow" preset="card" title="Version anlegen" class="pdlg small" :bordered="false">
    <NInput v-model:value="comment" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" placeholder="Was wurde geändert? (z. B. „Garage: BWM neu verknüpft“)" />
    <p class="muted small">Speichert das Projekt und legt eine Momentaufnahme mit diesem Kommentar an. Versionen lassen sich vergleichen und zurückholen.</p>
    <template #action>
      <div class="acts"><NButton @click="commitShow = false">Abbrechen</NButton><NButton type="primary" @click="commit">Version anlegen</NButton></div>
    </template>
  </NModal>

  <NModal v-model:show="versionsShow" preset="card" title="Versionen" class="pdlg wide" :bordered="false">
    <div class="vgrid">
      <div class="list">
        <div v-for="v in versions" :key="v.id" class="item ver" :class="{ cur: diffFor === v.id }">
          <div class="nm">{{ v.comment || 'ohne Kommentar' }}</div>
          <div class="muted small">{{ when(v.ts) }} · {{ v.stats.devices }} Geräte · {{ v.stats.group_addresses }} GAs · {{ v.stats.links }} Verknüpfungen</div>
          <div class="vacts">
            <NButton size="tiny" @click="showDiff(v)">Unterschiede zu jetzt</NButton>
            <NButton size="tiny" @click="restore(v)">Zurückholen</NButton>
          </div>
        </div>
        <p v-if="!versions.length" class="muted">Noch keine Versionen. „Projekt → Version anlegen“ speichert eine.</p>
      </div>
      <div class="diff">
        <p v-if="!diff" class="muted">Version wählen, um sie mit dem aktuellen Stand zu vergleichen.</p>
        <template v-else>
          <p v-if="diffEmpty" class="muted">Keine Unterschiede.</p>
          <template v-for="[label, list] in ([['Geräte neu', diff.devices_added], ['Geräte entfernt', diff.devices_removed], ['Geräte geändert', diff.devices_changed], ['GAs neu', diff.gas_added], ['GAs entfernt', diff.gas_removed], ['GAs umbenannt', diff.gas_renamed], ['Linien neu', diff.lines.added], ['Linien entfernt', diff.lines.removed], ['Gebäude geändert', diff.spaces_changed]] as [string, string[]][])" :key="label">
            <template v-if="list.length">
              <h4>{{ label }} ({{ list.length }})</h4>
              <ul><li v-for="x in list.slice(0, 50)" :key="x">{{ x }}</li><li v-if="list.length > 50" class="muted">… und {{ list.length - 50 }} weitere</li></ul>
            </template>
          </template>
        </template>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
:global(.pdlg) { width: min(640px, 94vw); }
:global(.pdlg.small) { width: min(460px, 94vw); }
:global(.pdlg.wide) { width: min(1000px, 96vw); }
.list { display: flex; flex-direction: column; gap: 4px; max-height: 60vh; overflow: auto; }
.item { font: inherit; text-align: left; border: 1px solid var(--kl-border); background: var(--kl-bg); color: var(--kl-text); border-radius: 6px; padding: 8px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.item:hover:not(:disabled) { background: var(--kl-hover); }
.item.cur { border-color: var(--kl-accent); }
.item:disabled { cursor: default; opacity: 0.7; }
.ver { cursor: default; }
.nm { font-weight: 600; }
.small { font-size: 12px; }
.acts { display: flex; gap: 8px; justify-content: flex-end; }
.vacts { display: flex; gap: 6px; margin-top: 4px; }
.vgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.diff { max-height: 60vh; overflow: auto; font-size: 13px; }
.diff h4 { margin: 10px 0 4px; font-size: 13px; }
.diff ul { margin: 0; padding-left: 18px; }
@media (max-width: 700px) { .vgrid { grid-template-columns: 1fr; } }
</style>
