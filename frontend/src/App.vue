<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NConfigProvider, NMessageProvider, NSplit, darkTheme, useOsTheme } from 'naive-ui'
import Navigator, { type Selection } from './components/Navigator.vue'
import DeviceView from './components/DeviceView.vue'
import ParamPanel from './components/ParamPanel.vue'
import GaView from './components/GaView.vue'
import { api, type DeviceDetail, type GaDetail, type ProjectInfo } from './api'

const os = useOsTheme()
const theme = computed(() => (os.value === 'dark' ? darkTheme : null))

const project = ref<ProjectInfo | null>(null)
const device = ref<DeviceDetail | null>(null)
const ga = ref<GaDetail | null>(null)
const loading = ref(false)
const error = ref('')
const dirty = ref(false)
const saving = ref(false)

function onUpdated(d: DeviceDetail) {
  device.value = d
  dirty.value = true
}

async function save() {
  if (!dirty.value || saving.value) return
  saving.value = true
  try {
    await api.save()
    dirty.value = false
  } catch (e) {
    error.value = String(e)
  } finally {
    saving.value = false
  }
}

function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault()
    save()
  }
}
function onLeave(e: BeforeUnloadEvent) {
  if (dirty.value) e.preventDefault() // ungespeicherte Änderungen: Browser fragt nach
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', onLeave)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', onLeave)
})

onMounted(async () => {
  try {
    project.value = await api.project()
    dirty.value = project.value.dirty
    document.title = `${project.value.name} · knxlens`
  } catch (e) {
    error.value = String(e)
  }
})

async function select(sel: Selection) {
  loading.value = true
  error.value = ''
  try {
    if (sel.type === 'device') {
      device.value = await api.device(sel.uid)
      ga.value = null
    } else {
      ga.value = await api.ga(sel.address)
    }
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <NConfigProvider :theme="theme" class="app">
   <NMessageProvider>
    <header class="bar">
      <strong>knxlens</strong>
      <span v-if="project" class="muted">
        {{ project.name }} · {{ project.devices }} Geräte · {{ project.group_addresses }} GAs · {{ project.links }} Verknüpfungen
      </span>
      <span class="spacer" />
      <span v-if="loading" class="muted">lädt …</span>
      <span v-if="error" class="error">{{ error }}</span>
      <span v-if="dirty" class="muted">ungespeicherte Änderungen</span>
      <NButton size="small" :type="dirty ? 'primary' : 'default'" :disabled="!dirty" :loading="saving" @click="save"
               title="Speichern (⌘S / Strg+S)">Speichern</NButton>
    </header>
    <NSplit class="main" direction="horizontal" :default-size="0.28" :min="0.15" :max="0.5">
      <template #1><Navigator @select="select" /></template>
      <template #2>
        <NSplit direction="horizontal" :default-size="0.6" :min="0.3" :max="0.85">
          <template #1>
            <GaView v-if="ga" :ga="ga" @device="(uid: string) => select({ type: 'device', uid })" />
            <DeviceView v-else-if="device" :device="device" @ga="(a: string) => select({ type: 'ga', address: a })" />
            <p v-else class="empty muted">Gerät oder Gruppenadresse im Navigator wählen.</p>
          </template>
          <template #2>
            <ParamPanel v-if="device" :device="device" @updated="onUpdated" />
            <p v-else class="empty muted">Parameter des gewählten Geräts erscheinen hier.</p>
          </template>
        </NSplit>
      </template>
    </NSplit>
   </NMessageProvider>
  </NConfigProvider>
</template>

<style scoped>
.app { height: 100%; display: flex; flex-direction: column; }
.bar { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--kl-border); flex-wrap: wrap; }
.spacer { flex: 1; }
.main { flex: 1; min-height: 0; }
.main :deep(.n-split-pane-1), .main :deep(.n-split-pane-2) { overflow: hidden; height: 100%; }
.main :deep(.n-split__resize-trigger) { background: var(--kl-border); }
.empty { padding: 24px; }
.error { color: #d03050; }
</style>
