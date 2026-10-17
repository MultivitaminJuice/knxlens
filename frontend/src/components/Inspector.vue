<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NSelect, NSwitch, NTag } from 'naive-ui'
import { run, store } from '../store'

const first = computed(() => store.selection[0] ?? null)
const many = computed(() => store.selection.length > 1)

// Eingaben erst beim Bestätigen schicken (Enter/Verlassen)
const form = ref<Record<string, string>>({})
watch([() => store.ga, () => store.device, first], () => {
  form.value = {
    gaName: store.ga?.name ?? '', gaAddr: store.ga?.address ?? '', gaDesc: store.ga?.description ?? '',
    devName: store.device?.name ?? '', devPa: store.device?.pa ?? '', devDesc: store.device?.description ?? '',
    rangeName: first.value?.kind === 'range' ? first.value.name ?? first.value.label : '',
    topoName: ['area', 'line', 'space'].includes(first.value?.kind ?? '') ? first.value!.name ?? '' : '',
  }
}, { immediate: true })

function commit(field: string) {
  const v = form.value[field]?.trim() ?? ''
  const ga = store.ga
  const d = store.device
  if (field === 'gaName' && ga && v !== ga.name) run('ga_update', { address: ga.address, name: v }, 'Umbenannt')
  if (field === 'gaDesc' && ga && v !== ga.description) run('ga_update', { address: ga.address, description: v }, 'Beschreibung gespeichert')
  if (field === 'gaAddr' && ga && v && v !== ga.address) run('ga_readdress', { old: ga.address, new: v }, `Adresse geändert auf ${v}`)
  if (field === 'devName' && d && v !== d.name) run('device_update', { uid: d.uid, name: v }, 'Umbenannt')
  if (field === 'devDesc' && d && v !== d.description) run('device_update', { uid: d.uid, description: v }, 'Beschreibung gespeichert')
  if (field === 'devPa' && d && v && v !== d.pa) run('device_set_pa', { uid: d.uid, pa: v }, `Physikalische Adresse ${v}`)
  if (field === 'rangeName' && first.value?.kind === 'range' && v) run('range_rename', { uid: first.value.rangeUid, name: v }, 'Umbenannt')
  const i = first.value
  if (field === 'topoName' && i && v !== (i.name ?? '')) {
    if (i.kind === 'area') run('area_rename', { area: i.area, name: v }, 'Umbenannt')
    if (i.kind === 'line') run('line_update', { area: i.area, line: i.line, name: v }, 'Umbenannt')
    if (i.kind === 'space' && v) run('space_update', { uid: i.spaceUid, name: v }, 'Umbenannt')
  }
}
const mediaOptions = computed(() => Object.entries(store.project?.media ?? {}).map(([value, label]) => ({ value, label })))
function setMedium(medium: string) {
  const i = first.value
  if (i?.kind === 'line' && medium !== i.medium) run('line_update', { area: i.area, line: i.line, medium }, 'Medium geändert')
}
const spaceDevices = computed(() => (first.value?.children ?? []).filter((c) => c.kind === 'device').length)

const DPTS = [
  ['DPST-1-1', '1.001 Schalten'], ['DPST-1-2', '1.002 Boolesch'], ['DPST-1-3', '1.003 Freigeben'],
  ['DPST-1-8', '1.008 Auf/Ab'], ['DPST-1-9', '1.009 Öffnen/Schließen'], ['DPST-1-10', '1.010 Start/Stop'],
  ['DPST-1-17', '1.017 Trigger'], ['DPST-1-18', '1.018 Belegung'], ['DPST-1-24', '1.024 Tag/Nacht'],
  ['DPST-3-7', '3.007 Dimmen'], ['DPST-3-8', '3.008 Jalousie'], ['DPST-5-1', '5.001 Prozent'],
  ['DPST-5-10', '5.010 Zähler 0…255'], ['DPST-7-1', '7.001 Impulse'], ['DPST-9-1', '9.001 Temperatur'],
  ['DPST-9-4', '9.004 Helligkeit (Lux)'], ['DPST-9-7', '9.007 Luftfeuchte'], ['DPST-10-1', '10.001 Uhrzeit'],
  ['DPST-11-1', '11.001 Datum'], ['DPST-13-10', '13.010 Wirkenergie (Wh)'], ['DPST-14-56', '14.056 Leistung (W)'],
  ['DPST-16-1', '16.001 Text'], ['DPST-17-1', '17.001 Szene'], ['DPST-18-1', '18.001 Szenensteuerung'],
  ['DPST-19-1', '19.001 Datum/Uhrzeit'], ['DPST-20-102', '20.102 HVAC-Modus'], ['DPST-232-600', '232.600 RGB'],
].map(([value, label]) => ({ value, label }))
const dptOptions = computed(() => {
  const cur = store.ga?.dpt
  return cur && !DPTS.some((d) => d.value === cur) ? [{ value: cur, label: cur }, ...DPTS] : DPTS
})

function setDpt(v: string | null) {
  if (store.ga) run('ga_update', { address: store.ga.address, dpt: v ?? '' }, 'DPT gesetzt')
}
function setCentral(v: boolean) {
  if (store.ga) run('ga_update', { address: store.ga.address, central: v }, 'Gespeichert')
}

const changedParams = computed(() => store.device?.params.reduce((n, g) => n + g.items.filter((i) => i.changed).length, 0) ?? 0)
const objectLinks = computed(() => {
  const f = first.value
  if (!store.device || !f || !['object', 'link'].includes(f.kind)) return null
  return store.device.objects.find((o) => o.ref === f.ref) ?? null
})
</script>

<template>
  <aside class="insp">
    <template v-if="many">
      <h3>{{ store.selection.length }} Elemente ausgewählt</h3>
      <p class="muted">Ziehen verschiebt alle gemeinsam · ⌫ löscht · ⌥ beim Ablegen kopiert</p>
    </template>

    <!-- Gruppenadresse (auch bei Auswahl einer Verknüpfung) -->
    <template v-else-if="store.ga && (first?.kind === 'ga' || first?.kind === 'link')">
      <h3><span class="mono">{{ store.ga.address }}</span> Gruppenadresse</h3>
      <label>Name<NInput v-model:value="form.gaName" size="small" @blur="commit('gaName')" @keyup.enter="commit('gaName')" /></label>
      <label>Adresse<NInput v-model:value="form.gaAddr" size="small" class="mono" @blur="commit('gaAddr')" @keyup.enter="commit('gaAddr')" /></label>
      <label>Datenpunkttyp
        <NSelect :value="store.ga.dpt || null" :options="dptOptions" size="small" filterable tag clearable placeholder="–"
                 @update:value="setDpt" />
      </label>
      <label>Beschreibung<NInput v-model:value="form.gaDesc" size="small" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" @blur="commit('gaDesc')" /></label>
      <label class="row">Zentral <NSwitch :value="store.ga.central" size="small" @update:value="setCentral" /></label>
      <h4>Verknüpft mit ({{ store.ga.users.length }})</h4>
      <p v-if="!store.ga.users.length" class="muted">Noch mit keinem Objekt verknüpft – GA auf ein Objekt in der Topologie ziehen.</p>
      <div v-for="u in store.ga.users" :key="u.uid + u.ref" class="user">
        <div><span class="mono">{{ u.pa }}</span> {{ u.device }}</div>
        <div class="muted">{{ u.number }} · {{ u.object }} <NTag v-if="u.sending" size="tiny" type="info">sendend</NTag></div>
        <div class="acts">
          <NButton v-if="!u.sending" size="tiny" quaternary @click="run('set_sending', { device_uid: u.uid, ref: u.ref, address: store.ga!.address }, 'Sendende GA gesetzt')">als sendend</NButton>
          <NButton size="tiny" quaternary type="error" @click="run('unlink', { device_uid: u.uid, ref: u.ref, address: store.ga!.address }, 'Verknüpfung gelöst')">lösen</NButton>
        </div>
      </div>
    </template>

    <!-- Gruppe -->
    <template v-else-if="first?.kind === 'range'">
      <h3><span class="mono">{{ first.mono }}</span> {{ first.level === 'main' ? 'Hauptgruppe' : 'Mittelgruppe' }}</h3>
      <label>Name<NInput v-model:value="form.rangeName" size="small" @blur="commit('rangeName')" @keyup.enter="commit('rangeName')" /></label>
      <p class="muted">{{ (first.children ?? []).length }} Einträge · Rechtsklick für neue {{ first.level === 'main' ? 'Mittelgruppe' : 'Gruppenadresse' }}</p>
    </template>

    <!-- Gerät / Objekt -->
    <template v-else-if="store.device && first">
      <h3><span class="mono">{{ store.device.pa || '–' }}</span> Gerät</h3>
      <p class="muted">{{ store.device.product }}</p>
      <label>Name<NInput v-model:value="form.devName" size="small" :placeholder="store.device.product" @blur="commit('devName')" @keyup.enter="commit('devName')" /></label>
      <label>Physikalische Adresse<NInput v-model:value="form.devPa" size="small" class="mono" @blur="commit('devPa')" @keyup.enter="commit('devPa')" /></label>
      <label>Beschreibung<NInput v-model:value="form.devDesc" size="small" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" @blur="commit('devDesc')" /></label>
      <NButton size="small" type="primary" block :disabled="!store.device.has_products" @click="store.paramsFor = store.device!.uid">
        Parameter bearbeiten ({{ changedParams }} geändert)
      </NButton>
      <template v-if="objectLinks">
        <h4>Objekt {{ objectLinks.number }}: {{ objectLinks.text }} – {{ objectLinks.function }}</h4>
        <p class="muted mono">{{ objectLinks.dpt }} · {{ objectLinks.size }} · {{ objectLinks.flags }}</p>
        <p v-if="!objectLinks.active" class="warn">Objekt ist laut Parametern inaktiv.</p>
        <div v-for="(g, i) in objectLinks.gas" :key="g.address" class="user">
          <div><span class="mono">{{ g.address }}</span> {{ g.name }} <NTag v-if="i === 0" size="tiny" type="info">sendend</NTag></div>
          <div class="acts">
            <NButton v-if="i > 0" size="tiny" quaternary @click="run('set_sending', { device_uid: store.device!.uid, ref: objectLinks!.ref, address: g.address }, 'Sendende GA gesetzt')">als sendend</NButton>
            <NButton size="tiny" quaternary type="error" @click="run('unlink', { device_uid: store.device!.uid, ref: objectLinks!.ref, address: g.address }, 'Verknüpfung gelöst')">lösen</NButton>
          </div>
        </div>
        <p v-if="!objectLinks.gas.length" class="muted">Keine GA verknüpft – GA aus dem anderen Arbeitsbereich hierher ziehen.</p>
      </template>
      <p v-else class="muted small">{{ store.device.objects.length }} aktive Objekte · Gerät im Baum aufklappen, um Objekte zu verknüpfen.</p>
    </template>

    <template v-else-if="first?.kind === 'area'">
      <h3><span class="mono">{{ first.mono }}</span> Bereich</h3>
      <label>Name<NInput v-model:value="form.topoName" size="small" placeholder="Bereich" @blur="commit('topoName')" @keyup.enter="commit('topoName')" /></label>
      <p class="muted">{{ first.children?.length ?? 0 }} Linie(n) · Rechtsklick → Neue Linie</p>
    </template>

    <template v-else-if="first?.kind === 'space'">
      <h3>{{ first.sub }}</h3>
      <label>Name<NInput v-model:value="form.topoName" size="small" @blur="commit('topoName')" @keyup.enter="commit('topoName')" /></label>
      <p class="muted">{{ spaceDevices }} Gerät(e) zugeordnet.
        <template v-if="first.holdsDevices">Geräte aus der Topologie hierher ziehen, um sie zuzuordnen.</template>
        <template v-else>Geräte gehören in Räume, Etagen oder Verteiler darunter.</template>
      </p>
    </template>

    <template v-else-if="first?.kind === 'line'">
      <h3><span class="mono">{{ first.mono }}</span> {{ first.line === 0 ? 'Bereichslinie' : 'Linie' }}</h3>
      <label>Name<NInput v-model:value="form.topoName" size="small" :placeholder="first.line === 0 ? 'Bereichslinie' : 'Linie'" @blur="commit('topoName')" @keyup.enter="commit('topoName')" /></label>
      <label>Medium<NSelect :value="first.medium" :options="mediaOptions" size="small" @update:value="setMedium" /></label>
      <p class="muted">Produkt aus dem Katalog hierher ziehen = Gerät einfügen. Geräte mit ⌘C kopieren, Linie wählen, ⌘V.</p>
    </template>

    <template v-else-if="first?.kind === 'product' && first.product">
      <h3>{{ first.product.name }}</h3>
      <p class="muted">{{ first.product.order }} · {{ first.product.application }}</p>
      <p class="muted">Auf eine Linie in der Topologie ziehen, um es einzufügen.</p>
    </template>

    <template v-else>
      <h3>Eigenschaften</h3>
      <p class="muted">Element wählen, um es hier zu bearbeiten.</p>
      <dl class="keys">
        <dt>⌘K</dt><dd>Suchen &amp; Befehle</dd>
        <dt>F2</dt><dd>Umbenennen</dd>
        <dt>⌫</dt><dd>Löschen / Verknüpfung lösen</dd>
        <dt>⌘Z / ⇧⌘Z</dt><dd>Rückgängig / Wiederholen</dd>
        <dt>⌘S</dt><dd>Speichern</dd>
        <dt>Ziehen</dt><dd>verknüpfen / verschieben, mit ⌥ kopieren</dd>
        <dt>Rechtsklick</dt><dd>alle Befehle zum Element</dd>
      </dl>
    </template>
  </aside>
</template>

<style scoped>
.insp { height: 100%; overflow: auto; padding: 12px 14px 24px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
h3 { margin: 0; font-size: 15px; }
h4 { margin: 8px 0 0; font-size: 13px; }
label { display: flex; flex-direction: column; gap: 4px; color: var(--kl-muted); font-size: 12px; }
label.row { flex-direction: row; align-items: center; justify-content: space-between; }
p { margin: 0; }
.user { border: 1px solid var(--kl-border); border-radius: 6px; padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
.acts { display: flex; gap: 4px; justify-content: flex-end; }
.warn { color: #b45309; }
.small { font-size: 12px; }
.keys { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 4px 0 0; font-size: 12px; }
.keys dt { font-family: var(--kl-mono); color: var(--kl-text); }
.keys dd { margin: 0; color: var(--kl-muted); }
</style>
