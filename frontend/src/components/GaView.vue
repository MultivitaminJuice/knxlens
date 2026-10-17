<script setup lang="ts">
import { h } from 'vue'
import { NDataTable, NTag, type DataTableColumns } from 'naive-ui'
import type { GaDetail } from '../api'

defineProps<{ ga: GaDetail }>()
const emit = defineEmits<{ device: [string] }>()

type User = GaDetail['users'][number]
const columns: DataTableColumns<User> = [
  {
    title: 'Gerät', key: 'device', minWidth: 200,
    render: (u) => h('a', { class: 'link', onClick: () => emit('device', u.uid) },
      [h('span', { class: 'mono' }, u.pa || '–'), ' ', u.device]),
  },
  { title: 'Nr.', key: 'number', width: 56 },
  { title: 'Objekt', key: 'object', minWidth: 160 },
  {
    title: '', key: 'sending', width: 120,
    render: (u) => (u.sending
      ? h(NTag, { size: 'small', type: 'info', title: 'Erste GA des Objekts – darauf sendet es, wenn es sendet' }, () => 'sendende GA')
      : null),
  },
]
</script>

<template>
  <div class="view">
    <header>
      <h2><span class="mono">{{ ga.address }}</span> {{ ga.name }}</h2>
      <p class="muted">
        DPT <span class="mono">{{ ga.dpt || '–' }}</span>
        <NTag v-if="ga.central" size="small">Zentral</NTag>
        <span v-if="ga.description">· {{ ga.description }}</span>
      </p>
    </header>
    <NDataTable :columns="columns" :data="ga.users" size="small" flex-height class="users" />
  </div>
</template>

<style scoped>
.view { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
header { padding: 12px 16px 4px; }
h2 { margin: 0; font-size: 17px; }
header p { margin: 4px 0 8px; display: flex; gap: 8px; align-items: center; }
.users { flex: 1; min-height: 0; padding: 0 8px 8px; }
.users :deep(.link) { cursor: pointer; }
.users :deep(.link:hover) { text-decoration: underline; }
</style>
