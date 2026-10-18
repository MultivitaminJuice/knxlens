<script setup lang="ts">
// Fenster „Gruppenadresse“ wie in der ETS: Reiter Assoziationen (welche Objekte hängen an der GA).
// Zeigt die zuletzt gewählte GA; die Auswahl eines Geräts ändert sie nicht.
import { ref, watch } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import { api, type GaDetail } from '../api'
import { run, store } from '../store'

const address = ref<string | null>(null)
const detail = ref<GaDetail | null>(null)

watch(() => store.selection, (sel) => {
  const first = sel[0]
  if (first?.address && ['ga', 'link'].includes(first.kind)) address.value = first.address
}, { immediate: true })

async function load() {
  if (!address.value) { detail.value = null; return }
  try { detail.value = await api.ga(address.value) } catch { detail.value = null; address.value = null }
}
watch(address, load, { immediate: true })
watch(() => store.version, load)

function openDevice(uid: string) {
  store.reveal?.('topology', uid)
}
function unlink(u: GaDetail['users'][number]) {
  run('unlink', { device_uid: u.uid, ref: u.ref, address: address.value }, 'Verknüpfung gelöst')
}
function makeSending(u: GaDetail['users'][number]) {
  run('set_sending', { device_uid: u.uid, ref: u.ref, address: address.value }, 'Sendende GA gesetzt')
}
</script>

<template>
  <section class="pane">
    <header v-if="detail" class="head">
      <span class="mono">{{ detail.address }}</span> <strong>{{ detail.name || '(ohne Namen)' }}</strong>
      <span class="muted">{{ detail.dpt.replace('DPST-', '').replace('DPT-', '') }}</span>
    </header>
    <p v-else class="muted empty">Gruppenadresse wählen.</p>
    <NTabs v-if="detail" type="line" size="small" class="tabs" pane-class="tabpane">
      <NTabPane name="assoc" :tab="`Assoziationen ${detail.users.length}`">
        <div class="table">
          <table>
            <thead><tr><th>Objekt</th><th>Gerät</th><th>Senden</th><th>Datentyp</th><th>Länge</th><th>K L S Ü A</th><th>Priorität</th><th /></tr></thead>
            <tbody>
              <tr v-for="u in detail.users" :key="u.uid + u.ref" @dblclick="openDevice(u.uid)">
                <td><span class="mono">{{ u.number ?? '?' }}</span>: {{ u.object }}</td>
                <td><a class="link" @click="openDevice(u.uid)"><span class="mono">{{ u.pa ?? '–' }}</span> {{ u.device }}</a></td>
                <td>{{ u.sending ? 'S' : '' }}</td>
                <td class="mono">{{ u.dpt }}</td>
                <td>{{ u.size }}</td>
                <td class="mono">{{ u.flags.replace(/-/g, '·').split('').join(' ') }}</td>
                <td>{{ u.priority }}</td>
                <td class="acts">
                  <button v-if="!u.sending" class="act" @click="makeSending(u)">als sendend</button>
                  <button class="act danger" @click="unlink(u)">lösen</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!detail.users.length" class="muted empty">Mit keinem Objekt verknüpft.</p>
        </div>
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
.table { flex: 1; min-height: 0; overflow: auto; border-top: 1px solid var(--kl-border); }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { position: sticky; top: 0; background: var(--kl-chrome); text-align: left; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid var(--kl-border); white-space: nowrap; }
td { padding: 3px 8px; border-bottom: 1px solid var(--kl-border); white-space: nowrap; }
tr:hover td { background: var(--kl-hover); }
.link { cursor: pointer; color: var(--kl-accent); }
.acts { text-align: right; }
.act { font: inherit; font-size: 11px; border: 0; background: none; color: var(--kl-muted); cursor: pointer; visibility: hidden; }
tr:hover .act { visibility: visible; }
.act:hover { color: var(--kl-text); }
.act.danger:hover { color: #d03050; }
</style>
