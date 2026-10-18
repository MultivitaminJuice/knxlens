<script setup lang="ts">
// Reiter „Download“ im Gerätefenster: Abgleich Projekt ↔ Gerät und Vorschau des Ladeablaufs.
// Lesen ist erlaubt (nur lesende Dienste); Schreiben kommt erst mit E4.
import { computed, ref, watch } from 'vue'
import { NButton, NRadioButton, NRadioGroup } from 'naive-ui'
import { api, ApiError, type DeviceCheck, type DevicePlan } from '../api'
import { store } from '../store'

const props = defineProps<{ uid: string | null }>()
const check = ref<DeviceCheck | null>(null)
const plan = ref<DevicePlan | null>(null)
const kind = ref<'all' | 'par' | 'grp'>('all')
const reading = ref(false)
const error = ref('')

async function load() {
  error.value = ''
  check.value = null
  plan.value = null
  if (!props.uid) return
  try {
    ;[check.value, plan.value] = await Promise.all([api.deviceCheck(props.uid), api.devicePlan(props.uid, kind.value)])
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}
watch(() => [props.uid, kind.value, store.version], load, { immediate: true })

async function readDevice() {
  if (!props.uid) return
  reading.value = true
  error.value = ''
  try {
    let gw = ''
    try { gw = localStorage.getItem('knxlens:gateway') ?? '' } catch { /* egal */ }
    check.value = await api.deviceRead(props.uid, gw)
    store.notify('success', 'Gerät gelesen (nur lesend) und verglichen')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    reading.value = false
  }
}

const verdict = computed(() => {
  const c = check.value
  if (!c?.read) return null
  const tables = [c.address_table, c.association_table].filter((x) => x !== null)
  const objOk = !c.object_table || c.object_table[0] === c.object_table[1]
  const ok = c.param_diff === 0 && tables.every(Boolean) && objOk
  return ok ? 'Gerät entspricht dem Projekt' : 'Gerät weicht vom Projekt ab'
})
function when(ts: number) {
  return new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}
</script>

<template>
  <div class="dl">
    <p v-if="error" class="err">{{ error }}</p>

    <section class="box">
      <header>
        <strong>Abgleich mit dem Gerät</strong>
        <NButton size="tiny" :loading="reading" @click="readDevice" title="Liest Parameter und Tabellen über den Bus – sendet keine Schreibbefehle">
          Gerät jetzt lesen
        </NButton>
      </header>
      <p v-if="check && !check.read" class="muted">Von diesem Gerät liegen noch keine gelesenen Daten vor.</p>
      <template v-else-if="check">
        <p class="verdict" :class="{ bad: verdict !== 'Gerät entspricht dem Projekt' }">{{ verdict === 'Gerät entspricht dem Projekt' ? '✓ ' : '' }}{{ verdict }}</p>
        <p class="muted small">Zuletzt gelesen {{ when(check.last_read!) }}</p>
        <table class="kv">
          <tr><td>Parameter</td><td>{{ check.param_equal }} Byte gleich<span v-if="check.param_diff" class="bad">, {{ check.param_diff }} abweichend</span></td></tr>
          <tr v-if="check.address_table !== null"><td>Adresstabelle</td><td :class="{ bad: !check.address_table }">{{ check.address_table ? 'gleich' : 'abweichend' }}</td></tr>
          <tr v-if="check.association_table !== null"><td>Assoziationstabelle</td><td :class="{ bad: !check.association_table }">{{ check.association_table ? 'gleich' : 'abweichend' }}</td></tr>
          <tr v-if="check.object_table"><td>Objekttabelle</td><td :class="{ bad: check.object_table[0] !== check.object_table[1] }">{{ check.object_table[0] }} / {{ check.object_table[1] }} Byte gleich</td></tr>
          <tr v-if="check.ets_image"><td>ETS-Ladeabbild</td><td>{{ check.ets_image[0] }} / {{ check.ets_image[1] }} Byte gleich</td></tr>
        </table>
        <details v-if="check.diffs?.length" class="diffs">
          <summary>{{ check.diffs.length }} abweichende Bytes</summary>
          <div v-for="d in check.diffs" :key="d.segment + d.offset" class="mono small">
            {{ d.segment }}+{{ d.offset.toString(16).padStart(4, '0') }}: Gerät {{ d.device.toString(2).padStart(8, '0') }}, Projekt {{ d.computed.toString(2).padStart(8, '0') }}
            <span class="muted">{{ d.params.join('; ') }}</span>
          </div>
        </details>
      </template>
    </section>

    <section class="box">
      <header>
        <strong>Ladeablauf (Vorschau)</strong>
        <NRadioGroup v-model:value="kind" size="small">
          <NRadioButton value="all">Komplett</NRadioButton>
          <NRadioButton value="par">Parameter</NRadioButton>
          <NRadioButton value="grp">Gruppenadressen</NRadioButton>
        </NRadioGroup>
      </header>
      <p v-if="plan" class="muted small">{{ plan.steps.length }} Schritte, {{ plan.total }} Byte zu schreiben. Nur Anzeige – es wird nichts gesendet.</p>
      <ol v-if="plan" class="steps">
        <li v-for="(s, i) in plan.steps" :key="i" :class="{ w: s.writes }">
          <span class="ic">{{ s.writes ? '✎' : '·' }}</span> {{ s.text }}
          <span v-if="s.size" class="muted small">{{ s.size }} Byte</span>
        </li>
      </ol>
      <NButton size="small" disabled block title="Kommt mit E4: eigener Schreibschutz, Vorschau, Bestätigung, Rücklesen">Herunterladen … (folgt mit E4)</NButton>
    </section>
  </div>
</template>

<style scoped>
.dl { height: 100%; overflow: auto; display: flex; flex-direction: column; gap: 10px; padding-bottom: 10px; }
.box { border: 1px solid var(--kl-border); border-radius: 6px; padding: 8px 10px; }
.box header { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }
.verdict { margin: 2px 0; font-weight: 600; color: #18794e; }
.verdict.bad, .bad { color: #b4530a; }
.small { font-size: 11.5px; }
.kv { font-size: 12.5px; border-collapse: collapse; }
.kv td { padding: 1px 12px 1px 0; }
.diffs { margin-top: 6px; font-size: 12px; }
.steps { margin: 6px 0 8px; padding-left: 26px; font-size: 12.5px; max-height: 280px; overflow: auto; }
.steps li { padding: 1px 0; color: var(--kl-muted); }
.steps li.w { color: var(--kl-text); }
.ic { display: inline-block; width: 14px; }
.err { color: #d03050; font-size: 12px; margin: 0; }
</style>
