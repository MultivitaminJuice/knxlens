<script setup lang="ts">
// Gruppenmonitor: zeigt mitgehörte Gruppentelegramme. Rein lesend – der Kern sendet nichts.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NInput, NSwitch } from 'naive-ui'
import { api, ApiError, type MonitorRow } from '../api'
import { store } from '../store'

const GW_KEY = 'knxlens:gateway'
const gateway = ref('')
const state = ref('getrennt')
const own = ref('')
const error = ref('')
const rows = ref<MonitorRow[]>([])
const filter = ref('')
const paused = ref(false)
const busy = ref(false)
let lastId = 0
let timer: number | undefined
const LIMIT = 5000

async function poll() {
  try {
    const r = await api.monitor(lastId)
    state.value = r.state
    own.value = r.own
    error.value = r.error
    if (!gateway.value && r.gateway) gateway.value = r.gateway
    if (r.rows.length) {
      lastId = r.rows[r.rows.length - 1].id
      if (!paused.value) {
        rows.value = rows.value.concat(r.rows).slice(-LIMIT)
      }
    }
  } catch {
    /* Server kurz weg – nächster Versuch */
  }
}

async function connect() {
  busy.value = true
  try {
    try { localStorage.setItem(GW_KEY, gateway.value.trim()) } catch { /* nur Komfort */ }
    await api.monitorStart(gateway.value.trim())
    await poll()
  } catch (e) {
    store.notify('error', e instanceof ApiError ? e.message : String(e))
  } finally {
    busy.value = false
  }
}
async function disconnect() {
  await api.monitorStop()
  await poll()
}
async function clear() {
  await api.monitorClear()
  rows.value = []
}

const shown = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const list = q
    ? rows.value.filter((r) => [r.src, r.src_name, r.dst, r.dst_name, r.value, r.service].some((x) => x.toLowerCase().includes(q)))
    : rows.value
  return list.slice(-500).reverse()
})

function time(ts: number) {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('de-DE', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

function reveal(addr: string) {
  store.reveal?.('groupaddresses', `ga:${addr}`)
}

onMounted(async () => {
  try { gateway.value = localStorage.getItem(GW_KEY) ?? '' } catch { /* egal */ }
  await poll()
  timer = window.setInterval(poll, 700)
})
onBeforeUnmount(() => clearInterval(timer))

const stateLabel = computed(() => ({
  verbunden: `verbunden${own.value ? ' als ' + own.value : ''}`,
  verbinde: 'verbinde …',
  getrennt: 'getrennt',
  Fehler: 'Fehler',
})[state.value] ?? state.value)
</script>

<template>
  <section class="mon">
    <div class="tools">
      <NInput v-model:value="gateway" size="small" placeholder="IP des KNX-IP-Interfaces" class="gw" :disabled="state === 'verbunden'"
              @keyup.enter="state !== 'verbunden' && connect()" />
      <NButton v-if="state !== 'verbunden' && state !== 'verbinde'" size="small" type="primary" :loading="busy" :disabled="!gateway.trim()" @click="connect">Verbinden</NButton>
      <NButton v-else size="small" @click="disconnect">Trennen</NButton>
      <span class="state" :class="state">{{ stateLabel }}</span>
      <span class="spacer" />
      <label class="pause"><NSwitch v-model:value="paused" size="small" /> Pause</label>
      <NButton size="small" quaternary @click="clear">Leeren</NButton>
    </div>
    <div class="tools">
      <NInput v-model:value="filter" size="small" clearable placeholder="Filtern (GA, Name, Absender, Wert) …" />
      <span class="muted count">{{ rows.length }} Telegramme</span>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <div class="table">
      <table>
        <thead><tr><th>Zeit</th><th>Absender</th><th>Gruppenadresse</th><th>Dienst</th><th>Wert</th></tr></thead>
        <tbody>
          <tr v-for="r in shown" :key="r.id" :class="{ unknown: !r.known }">
            <td class="mono">{{ time(r.ts) }}</td>
            <td><span class="mono">{{ r.src }}</span> <span class="muted">{{ r.src_name }}</span></td>
            <td><a class="mono link" @click="reveal(r.dst)">{{ r.dst }}</a> {{ r.dst_name || (r.known ? '' : '(nicht im Projekt)') }}</td>
            <td>{{ r.service }}</td>
            <td><b>{{ r.value }}</b> <span v-if="r.raw && r.raw !== r.value" class="muted mono">{{ r.raw }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!shown.length" class="muted empty">
        {{ state === 'verbunden' ? 'Warte auf Telegramme …' : 'Nicht verbunden. Der Monitor hört nur mit und sendet nichts.' }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.mon { height: 100%; display: flex; flex-direction: column; min-width: 0; background: var(--kl-bg); }
.tools { display: flex; gap: 6px; padding: 6px 8px; align-items: center; }
.gw { width: 180px; flex: none; }
.spacer { flex: 1; }
.state { font-size: 12px; color: var(--kl-muted); }
.state.verbunden { color: #18a058; }
.state.Fehler { color: #d03050; }
.pause { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--kl-muted); }
.count { font-size: 12px; white-space: nowrap; }
.err { color: #d03050; font-size: 12px; margin: 0 10px 6px; }
.table { flex: 1; min-height: 0; overflow: auto; border-top: 1px solid var(--kl-border); }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { position: sticky; top: 0; background: var(--kl-chrome); text-align: left; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid var(--kl-border); }
td { padding: 3px 8px; border-bottom: 1px solid var(--kl-border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 320px; }
tr.unknown td { color: var(--kl-muted); }
.link { cursor: pointer; color: var(--kl-accent); }
.empty { padding: 12px; }
</style>
