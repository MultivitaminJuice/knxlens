<script setup lang="ts">
// Fenster „Gerät“ wie in der ETS: Reiter Kommunikationsobjekte (Tabelle) und Parameter.
// Zeigt das zuletzt gewählte Gerät; die Auswahl einer GA ändert es nicht.
import { computed, ref, watch } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import ParamEditor from './ParamEditor.vue'
import { api, type DeviceDetail, type DeviceObject } from '../api'
import { dropAction, run, select, store, type Item } from '../store'

const uid = ref<string | null>(null)
const detail = ref<DeviceDetail | null>(null)
const tab = ref<'objects' | 'params'>('objects')
const filter = ref('')

watch(() => store.selection, (sel) => {
  const first = sel[0]
  if (first?.uid && ['device', 'object', 'link'].includes(first.kind)) uid.value = first.uid
}, { immediate: true })

async function load() {
  if (!uid.value) { detail.value = null; return }
  try { detail.value = await api.device(uid.value) } catch { detail.value = null; uid.value = null }
}
watch(uid, load, { immediate: true })
watch(() => store.version, load)

const objects = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const list = detail.value?.objects ?? []
  return q ? list.filter((o) => [String(o.number), o.text, o.function, ...o.gas.map((g) => `${g.address} ${g.name}`)]
    .some((x) => x.toLowerCase().includes(q))) : list
})

function asItem(o: DeviceObject): Item {
  return { key: `${uid.value}#${o.ref}`, kind: 'object', label: o.text, uid: uid.value!, ref: o.ref, number: o.number }
}
const selectedRef = computed(() => (store.selection[0]?.kind === 'object' && store.selection[0].uid === uid.value ? store.selection[0].ref : null))

// GA aus dem GA-Baum auf eine Zeile ziehen = verknüpfen (⌥ = als sendende GA)
const dropRef = ref<string | null>(null)
function dragover(e: DragEvent, o: DeviceObject) {
  if (!store.drag || !dropAction(asItem(o), store.drag, e.altKey)) return
  e.preventDefault()
  dropRef.value = o.ref
}
async function drop(e: DragEvent, o: DeviceObject) {
  e.preventDefault()
  dropRef.value = null
  const items = store.drag
  store.drag = null
  if (items) await dropAction(asItem(o), items, e.altKey)?.apply()
}
function showGa(o: DeviceObject, address: string) {
  select([{ key: `${uid.value}#${o.ref}@${address}`, kind: 'link', label: address, uid: uid.value!, ref: o.ref, address }])
}
function unlink(o: DeviceObject, address: string) {
  run('unlink', { device_uid: uid.value, ref: o.ref, address }, 'Verknüpfung gelöst')
}
</script>

<template>
  <section class="pane">
    <header v-if="detail" class="head">
      <span class="mono">{{ detail.pa ?? '–' }}</span>
      <strong>{{ detail.name || detail.product }}</strong>
      <span class="muted">{{ detail.name ? detail.product : '' }}</span>
    </header>
    <p v-else class="muted empty">Gerät in der Topologie oder im Gebäude wählen.</p>
    <NTabs v-if="detail" v-model:value="tab" type="line" size="small" class="tabs" pane-class="tabpane" animated>
      <NTabPane name="objects" :tab="`Kommunikationsobjekte ${detail.objects.length}`">
        <div class="tools"><input v-model="filter" class="filter" placeholder="Filtern …" /></div>
        <div class="table">
          <table>
            <thead><tr><th>Nr.</th><th>Name</th><th>Objektfunktion</th><th>Verbunden mit</th><th>Datentyp</th><th>Länge</th><th>K L S Ü A</th><th>Priorität</th></tr></thead>
            <tbody>
              <tr v-for="o in objects" :key="o.ref" :class="{ inactive: !o.active, sel: selectedRef === o.ref, drop: dropRef === o.ref }"
                  :title="o.active ? '' : 'Objekt ist laut Parametern inaktiv'"
                  @click="select([asItem(o)])" @dragover="dragover($event, o)" @dragleave="dropRef === o.ref && (dropRef = null)" @drop="drop($event, o)">
                <td class="mono">{{ o.number }}</td>
                <td>{{ o.text }}</td>
                <td>{{ o.function }}</td>
                <td class="gas" :title="o.gas.map((g, i) => `${g.address} ${g.name}${i === 0 ? ' (sendend)' : ''}`).join('\n')">
                  <span v-for="(g, i) in o.gas" :key="g.address" class="ga" :class="{ send: i === 0 }" @click.stop="showGa(o, g.address)">
                    {{ g.name || g.address }}<button class="x" title="Verknüpfung lösen" @click.stop="unlink(o, g.address)">×</button>
                  </span>
                </td>
                <td class="mono">{{ o.dpt }}</td>
                <td>{{ o.size }}</td>
                <td class="mono">{{ o.flags.replace(/-/g, '·').split('').join(' ') }}</td>
                <td>{{ o.priority }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="hint">GA aus dem Gruppenadressen-Fenster auf ein Objekt ziehen = verknüpfen (⌥ = als sendende GA)</footer>
      </NTabPane>
      <NTabPane name="params" tab="Parameter">
        <ParamEditor :uid="uid" embedded />
      </NTabPane>
    </NTabs>
  </section>
</template>

<style scoped>
.pane { height: 100%; display: flex; flex-direction: column; min-width: 0; background: var(--kl-bg); }
.head { display: flex; gap: 8px; align-items: baseline; padding: 6px 10px 0; font-size: 13px; white-space: nowrap; overflow: hidden; }
.empty { padding: 12px; }
.tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 8px; }
.tabs :deep(.n-tabs-pane-wrapper) { flex: 1; min-height: 0; }
.tabs :deep(.tabpane) { height: 100%; display: flex; flex-direction: column; padding-top: 6px; }
.tools { padding-bottom: 6px; }
.filter { font: inherit; font-size: 12px; width: 220px; padding: 3px 8px; border: 1px solid var(--kl-border); border-radius: 4px; background: var(--kl-bg); color: var(--kl-text); }
.table { flex: 1; min-height: 0; overflow: auto; border-top: 1px solid var(--kl-border); }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { position: sticky; top: 0; background: var(--kl-chrome); text-align: left; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid var(--kl-border); white-space: nowrap; }
td { padding: 3px 8px; border-bottom: 1px solid var(--kl-border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 260px; }
tr { cursor: default; }
tr:hover td { background: var(--kl-hover); }
tr.sel td { background: var(--kl-sel); }
tr.drop td { background: var(--kl-accent-soft); box-shadow: inset 0 0 0 1px var(--kl-accent); }
tr.inactive td { color: var(--kl-muted); font-style: italic; }
.gas { max-width: 380px; }
.ga { cursor: pointer; }
.ga:hover { text-decoration: underline; }
.ga + .ga::before { content: ', '; text-decoration: none; display: inline-block; white-space: pre; }
.ga.send { font-weight: 600; }
.x { border: 0; background: none; color: var(--kl-muted); cursor: pointer; padding: 0 2px; font-size: 13px; visibility: hidden; }
tr:hover .x { visibility: visible; }
.x:hover { color: #d03050; }
.hint { font-size: 11px; color: var(--kl-muted); padding: 4px 2px; }
:deep(.body.embedded) { height: 100%; }
</style>
