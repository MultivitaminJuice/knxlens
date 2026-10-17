<script setup lang="ts">
// Eingabefeld für einen Parameter. Prüfung und Speichern macht der Kern (PUT …/param);
// das Feld schickt erst beim Bestätigen (Auswahl, Enter, Verlassen), nicht bei jedem Tastendruck.
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'
import type { ParamItem } from '../api'

const props = defineProps<{ item: ParamItem; busy: boolean }>()
const emit = defineEmits<{ change: [string | null] }>()

const text = ref(String(props.item.value ?? ''))
watch(() => props.item.value, (v) => (text.value = String(v ?? '')))

const numberValue = computed(() => (props.item.value === null ? null : Number(props.item.value)))
const options = computed(() => (props.item.options ?? []).map((o) => ({ value: o.value, label: o.label })))
const step = computed(() => (props.item.kind === 'float' ? 0.1 : 1))

function commitNumber(v: number | null) {
  if (v !== null && v !== numberValue.value) emit('change', String(v))
}
function commitText() {
  if (text.value !== String(props.item.value ?? '')) emit('change', text.value)
}
</script>

<template>
  <div class="field">
    <span v-if="!item.editable" class="ro">{{ item.display }}</span>
    <NSwitch v-else-if="item.kind === 'enum' && item.checkbox" size="small" :disabled="busy"
             :value="item.value === 1" @update:value="(on: boolean) => emit('change', on ? '1' : '0')" />
    <NSelect v-else-if="item.kind === 'enum'" size="small" :options="options" :value="item.value as number"
             :disabled="busy" :consistent-menu-width="false" filterable
             @update:value="(v: number) => emit('change', String(v))" />
    <NInputNumber v-else-if="item.kind === 'number' || item.kind === 'time' || item.kind === 'float'" size="small"
                  :value="numberValue" :min="item.min ?? undefined" :max="item.max ?? undefined" :step="step"
                  :update-value-on-input="false" :disabled="busy" :show-button="false"
                  @update:value="commitNumber">
      <template v-if="item.suffix" #suffix>{{ item.suffix }}</template>
    </NInputNumber>
    <NInput v-else-if="item.kind === 'text' || item.kind === 'color'" size="small" v-model:value="text"
            :maxlength="item.maxlength" :disabled="busy" @blur="commitText" @keyup.enter="commitText" />
    <span v-else class="ro">{{ item.display }}</span>
    <NButton v-if="item.changed && item.editable" quaternary size="tiny" :disabled="busy"
             :title="`Auf Standard zurücksetzen: ${item.default}`" @click="emit('change', null)">↺</NButton>
  </div>
</template>

<style scoped>
.field { display: flex; align-items: center; gap: 4px; min-width: 0; }
.field > :first-child { flex: 1; min-width: 130px; }
.ro { color: var(--kl-muted); }
</style>
