<script setup lang="ts">
import { h } from 'vue'
import { NDataTable, NTag, type DataTableColumns } from 'naive-ui'
import type { DeviceDetail, DeviceObject } from '../api'

defineProps<{ device: DeviceDetail }>()
const emit = defineEmits<{ ga: [string] }>()

const mono = (text: string) => h('span', { class: 'mono' }, text)

const columns: DataTableColumns<DeviceObject> = [
  { title: 'Nr.', key: 'number', width: 56 },
  {
    title: 'Objekt', key: 'text', minWidth: 160,
    render: (o) => [o.text, o.function ? h('span', { class: 'muted' }, ` – ${o.function}`) : null],
  },
  {
    title: 'Gruppenadressen', key: 'gas', minWidth: 200,
    render: (o) => o.gas.map((g) => h('a', { class: 'ga', title: g.name, onClick: () => emit('ga', g.address) },
      [mono(g.address), ' ', h('span', { class: 'muted' }, g.name)])),
  },
  { title: 'DPT', key: 'dpt', width: 80, render: (o) => mono(o.dpt) },
  { title: 'Größe', key: 'size', width: 80 },
  { title: 'Flags', key: 'flags', width: 76, render: (o) => mono(o.flags) },
]

const rowClass = (o: DeviceObject) => (o.gas.length ? '' : 'unlinked')
</script>

<template>
  <div class="view">
    <header>
      <h2><span class="mono">{{ device.pa || '–' }}</span> {{ device.name || device.product }}</h2>
      <p class="muted">
        {{ device.product }} · <span class="mono">{{ device.app_id }}</span>
        <NTag v-if="device.origin === 'rekonstruiert'" size="small" type="warning">rekonstruiert</NTag>
      </p>
    </header>
    <p v-if="!device.has_products" class="muted pad">Keine Produktdaten zu dieser Applikation im Projekt.</p>
    <NDataTable v-else :columns="columns" :data="device.objects" :row-key="(o: DeviceObject) => o.number"
                :row-class-name="rowClass" size="small" flex-height virtual-scroll class="objects" />
  </div>
</template>

<style scoped>
.view { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
header { padding: 12px 16px 4px; }
h2 { margin: 0; font-size: 17px; }
header p { margin: 4px 0 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.objects { flex: 1; min-height: 0; padding: 0 8px 8px; }
.objects :deep(.unlinked td) { color: var(--kl-muted); }
.objects :deep(.ga) { display: block; cursor: pointer; }
.objects :deep(.ga:hover .mono) { text-decoration: underline; }
.pad { padding: 0 16px; }
</style>
