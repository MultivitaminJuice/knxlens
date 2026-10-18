<script setup lang="ts">
// Parameterdialog (Doppelklick auf ein Gerät); Inhalt: ParamEditor.
import { computed, ref } from 'vue'
import { NModal } from 'naive-ui'
import ParamEditor from './ParamEditor.vue'
import { store } from '../store'

const editor = ref<InstanceType<typeof ParamEditor> | null>(null)
const show = computed({
  get: () => !!store.paramsFor,
  set: (v: boolean) => { if (!v) store.paramsFor = null },
})
const detail = computed(() => editor.value?.detail ?? null)
</script>

<template>
  <NModal v-model:show="show" preset="card" :bordered="false" class="dlg" :segmented="{ content: true }" :auto-focus="false"
          :title="detail ? `${detail.pa ?? '–'}  ${detail.name || detail.product}` : 'Parameter'">
    <template #header-extra><span class="muted">{{ detail?.product }}</span></template>
    <ParamEditor ref="editor" :uid="store.paramsFor" />
  </NModal>
</template>

<style scoped>
:global(.dlg) { width: min(1100px, 94vw); }
</style>
