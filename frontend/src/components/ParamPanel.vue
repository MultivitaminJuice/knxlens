<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NBadge, NCollapse, NCollapseItem, NSwitch, useMessage } from 'naive-ui'
import ParamField from './ParamField.vue'
import { api, type DeviceDetail } from '../api'

const props = defineProps<{ device: DeviceDetail }>()
const emit = defineEmits<{ updated: [DeviceDetail] }>()
const message = useMessage()

const onlyChanged = ref(false)
const busy = ref(false)
// Aufgeklappte Seiten über den Titel merken – Seiten kommen und gehen mit der Sichtbarkeit
const expanded = ref<string[]>([])
watch(() => props.device.uid, () => (expanded.value = props.device.params.slice(0, 1).map((g) => title(g.path))), {
  immediate: true,
})

function title(path: string[]) {
  return path.join(' › ') || 'Allgemein'
}

const groups = computed(() =>
  props.device.params
    .map((g) => ({
      title: title(g.path),
      items: onlyChanged.value ? g.items.filter((p) => p.changed) : g.items,
      changed: g.items.filter((p) => p.changed).length,
    }))
    .filter((g) => g.items.length),
)
const total = computed(() => props.device.params.reduce((n, g) => n + g.items.filter((p) => p.changed).length, 0))

async function change(id: string, value: string | null) {
  busy.value = true
  try {
    const before = new Set(props.device.params.flatMap((g) => g.items.map((i) => i.id)))
    const updated = await api.setParam(props.device.uid, id, value)
    const after = updated.params.flatMap((g) => g.items.map((i) => i.id))
    const added = after.filter((x) => !before.has(x)).length
    const removed = before.size - after.filter((x) => before.has(x)).length
    emit('updated', updated)
    if (added || removed) message.info(`Sichtbarkeit geändert: ${added} Parameter neu, ${removed} ausgeblendet`)
  } catch (e) {
    message.error(String((e as Error).message ?? e))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="panel">
    <header>
      <h3>Parameter</h3>
      <label class="muted"><NSwitch v-model:value="onlyChanged" size="small" /> nur geänderte ({{ total }})</label>
    </header>
    <p v-if="!device.has_products" class="muted pad">Ohne Produktdaten nicht verfügbar.</p>
    <div v-else class="scroll">
      <NCollapse v-model:expanded-names="expanded" display-directive="show">
        <NCollapseItem v-for="g in groups" :key="g.title" :name="g.title">
          <template #header>
            <span>{{ g.title }}</span>
            <NBadge v-if="g.changed" :value="g.changed" type="info" class="badge" />
          </template>
          <table>
            <tr v-for="p in g.items" :key="p.id" :class="{ changed: p.changed }">
              <td :title="p.changed ? `Standard: ${p.default}` : ''">{{ p.text }}</td>
              <td><ParamField :item="p" :busy="busy" @change="(v) => change(p.id, v)" /></td>
            </tr>
          </table>
        </NCollapseItem>
      </NCollapse>
    </div>
  </div>
</template>

<style scoped>
.panel { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
header { display: flex; align-items: center; justify-content: space-between; padding: 12px 12px 6px; gap: 8px; }
h3 { margin: 0; font-size: 15px; }
label { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.scroll { flex: 1; overflow: auto; padding: 0 12px 12px; }
.badge { margin-left: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td { padding: 3px 4px; border-bottom: 1px solid var(--kl-border); vertical-align: middle; }
td:first-child { width: 48%; min-width: 120px; }
table { min-width: 280px; }
tr.changed td:first-child { font-weight: 600; }
.pad { padding: 0 12px; }
</style>
