<script setup lang="ts">
// „⋯“-Menü in der Kopfleiste jedes Fensters: verschieben ohne Ziehen, maximieren, schließen.
import { computed } from 'vue'
import { NDropdown, type DropdownOption } from 'naive-ui'
import type { IDockviewHeaderActionsProps } from 'dockview-vue'

const props = defineProps<{ params: IDockviewHeaderActionsProps }>()

type Dir = 'above' | 'below' | 'left' | 'right'
const options = computed<DropdownOption[]>(() => {
  const maxed = props.params.activePanel?.api.isMaximized() ?? false
  return [
    { label: 'Nach oben (ganze Breite)', key: 'move:above' },
    { label: 'Nach unten (ganze Breite)', key: 'move:below' },
    { label: 'Nach links (ganze Höhe)', key: 'move:left' },
    { label: 'Nach rechts (ganze Höhe)', key: 'move:right' },
    { type: 'divider', key: 'd1' },
    { label: 'Unter diesem Fenster teilen', key: 'split:below' },
    { label: 'Rechts von diesem Fenster teilen', key: 'split:right' },
    { type: 'divider', key: 'd2' },
    { label: maxed ? 'Wiederherstellen' : 'Maximieren', key: 'max' },
    { label: 'Schließen', key: 'close' },
  ]
})

function onSelect(key: string) {
  const panel = props.params.activePanel
  const api = props.params.containerApi
  if (!panel) return
  const [action, dir] = key.split(':') as [string, Dir]
  if (action === 'move') {
    // neue Fläche am Rand des Gesamtlayouts, Fenster dorthin verschieben
    const group = api.addGroup({ direction: dir })
    panel.api.moveTo({ group })
  } else if (action === 'split') {
    if (props.params.panels.length < 2) return // allein im Fenster: nichts zu teilen
    const group = api.addGroup({ referenceGroup: props.params.group, direction: dir })
    panel.api.moveTo({ group })
  } else if (action === 'max') {
    if (panel.api.isMaximized()) panel.api.exitMaximized()
    else panel.api.maximize()
  } else if (action === 'close') {
    panel.api.close()
  }
}
</script>

<template>
  <div class="actions">
    <NDropdown trigger="click" :options="options" size="small" placement="bottom-end" @select="onSelect">
      <button class="more" title="Fenster anordnen">⋯</button>
    </NDropdown>
  </div>
</template>

<style scoped>
.actions { height: 100%; display: flex; align-items: center; padding: 0 6px; }
.more { font: inherit; font-size: 16px; line-height: 1; padding: 2px 6px; border: 0; border-radius: 4px; background: none; color: var(--kl-muted); cursor: pointer; }
.more:hover { background: var(--kl-hover); color: var(--kl-text); }
</style>
