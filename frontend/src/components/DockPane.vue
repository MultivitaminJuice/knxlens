<script setup lang="ts">
// Hülle: dockview-Panel → Baumansicht bzw. Eigenschaften.
import type { IDockviewPanelProps } from 'dockview-vue'
import WorkPane from './WorkPane.vue'
import Inspector from './Inspector.vue'
import BusMonitor from './BusMonitor.vue'
import DevicePane from './DevicePane.vue'
import GaPane from './GaPane.vue'
import type { TreeKind, ViewKind } from '../store'

const props = defineProps<{ params: IDockviewPanelProps<{ kind: ViewKind }> }>()
</script>

<template>
  <Inspector v-if="props.params.params.kind === 'inspector'" />
  <BusMonitor v-else-if="props.params.params.kind === 'monitor'" />
  <DevicePane v-else-if="props.params.params.kind === 'device'" />
  <GaPane v-else-if="props.params.params.kind === 'gadetail'" />
  <WorkPane v-else :kind="props.params.params.kind as TreeKind" :pane-id="props.params.api.id" />
</template>
