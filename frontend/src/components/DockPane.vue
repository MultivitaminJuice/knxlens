<script setup lang="ts">
// Hülle: dockview-Panel → Baumansicht bzw. Eigenschaften.
import type { IDockviewPanelProps } from 'dockview-vue'
import WorkPane from './WorkPane.vue'
import Inspector from './Inspector.vue'
import type { TreeKind } from '../store'

const props = defineProps<{ params: IDockviewPanelProps<{ kind: TreeKind | 'inspector' }> }>()
</script>

<template>
  <Inspector v-if="props.params.params.kind === 'inspector'" />
  <WorkPane v-else :kind="props.params.params.kind" :pane-id="props.params.api.id" />
</template>
