<script setup lang="ts">
import { computed, ref } from 'vue'
import { NBadge, NCollapse, NCollapseItem, NSwitch } from 'naive-ui'
import type { DeviceDetail } from '../api'

const props = defineProps<{ device: DeviceDetail }>()
const onlyChanged = ref(false)

const groups = computed(() =>
  props.device.params
    .map((g, i) => ({
      name: String(i),
      title: g.path.join(' › ') || 'Allgemein',
      items: onlyChanged.value ? g.items.filter((p) => p.changed) : g.items,
      changed: g.items.filter((p) => p.changed).length,
    }))
    .filter((g) => g.items.length),
)
const total = computed(() => props.device.params.reduce((n, g) => n + g.items.filter((p) => p.changed).length, 0))
const open = computed(() => (onlyChanged.value ? groups.value.map((g) => g.name) : groups.value.slice(0, 1).map((g) => g.name)))
</script>

<template>
  <div class="panel">
    <header>
      <h3>Parameter</h3>
      <label class="muted"><NSwitch v-model:value="onlyChanged" size="small" /> nur geänderte ({{ total }})</label>
    </header>
    <p v-if="!device.has_products" class="muted pad">Ohne Produktdaten nicht verfügbar.</p>
    <div v-else class="scroll">
      <NCollapse :key="String(onlyChanged) + device.uid" :default-expanded-names="open" display-directive="show">
        <NCollapseItem v-for="g in groups" :key="g.name" :name="g.name">
          <template #header>
            <span>{{ g.title }}</span>
            <NBadge v-if="g.changed" :value="g.changed" type="info" class="badge" />
          </template>
          <table>
            <tr v-for="p in g.items" :key="p.id" :class="{ changed: p.changed }">
              <td>{{ p.text }}</td>
              <td>{{ p.display }}</td>
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
td { padding: 3px 4px; border-bottom: 1px solid var(--kl-border); vertical-align: top; }
td:first-child { width: 55%; }
tr.changed td { font-weight: 600; }
.pad { padding: 0 12px; }
</style>
