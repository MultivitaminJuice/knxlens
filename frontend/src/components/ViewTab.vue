<script setup lang="ts">
// Reiter eines Fensters wie in der ETS: Titel mit Auswahl „Topologie ▾“ – wechselt den Inhalt
// des Fensters an seinem Platz, statt ein neues Fenster zu öffnen. Dazu Schließen.
import { computed, onBeforeUnmount, ref } from 'vue'
import { NDropdown, type DropdownOption } from 'naive-ui'
import type { IDockviewPanelHeaderProps } from 'dockview-vue'
import { VIEW_TITLES, type ViewKind } from '../store'

const props = defineProps<{ params: IDockviewPanelHeaderProps<{ kind: ViewKind }> }>()
const title = ref(props.params.api.title ?? '')
const sub = props.params.api.onDidTitleChange((e) => (title.value = e.title))
onBeforeUnmount(() => sub.dispose())

const GROUPS: { label: string; kinds: ViewKind[] }[] = [
  { label: 'Bäume', kinds: ['topology', 'groupaddresses', 'buildings', 'catalog'] },
  { label: 'Details', kinds: ['device', 'gadetail', 'inspector', 'monitor'] },
]
const kind = ref<ViewKind>(props.params.params.kind)
const options = computed<DropdownOption[]>(() => GROUPS.flatMap((g, i) => [
  ...(i ? [{ type: 'divider', key: `d${i}` } as DropdownOption] : []),
  { type: 'group', label: g.label, key: g.label, children: g.kinds.map((k) => ({ label: VIEW_TITLES[k], key: k, disabled: k === kind.value })) },
]))
function switchTo(next: ViewKind) {
  kind.value = next
  props.params.api.updateParameters({ kind: next })
  props.params.api.setTitle(VIEW_TITLES[next])
}
function close(e: MouseEvent) {
  e.stopPropagation()
  props.params.api.close()
}
</script>

<template>
  <div class="tab">
    <NDropdown trigger="click" :options="options" size="small" @select="switchTo">
      <button class="name" title="Inhalt dieses Fensters wechseln" @mousedown.stop @pointerdown.stop>{{ title }} <span class="caret">▾</span></button>
    </NDropdown>
    <button class="x" title="Fenster schließen" @mousedown.stop @pointerdown.stop @click="close">×</button>
  </div>
</template>

<style scoped>
.tab { display: flex; align-items: center; height: 100%; padding: 0 4px 0 8px; gap: 2px; font-size: 12.5px; }
.name { font: inherit; border: 0; background: none; color: inherit; cursor: pointer; padding: 2px 4px; border-radius: 4px; display: flex; align-items: center; gap: 4px; }
.name:hover { background: var(--kl-hover); }
.caret { font-size: 9px; opacity: 0.7; }
.x { border: 0; background: none; color: inherit; opacity: 0.55; cursor: pointer; font-size: 14px; line-height: 1; padding: 2px 4px; border-radius: 4px; }
.x:hover { opacity: 1; background: var(--kl-hover); }
</style>
