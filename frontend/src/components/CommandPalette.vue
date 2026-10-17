<script setup lang="ts">
// ⌘K: Gruppenadressen, Geräte und Befehle suchen. Auswahl springt im passenden Arbeitsbereich hin.
import { computed, nextTick, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { redo, save, store, undo, type Item, type TreeKind } from '../store'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [boolean]; reveal: [TreeKind, string] }>()

const q = ref('')
const idx = ref(0)
const input = ref<HTMLInputElement | null>(null)
watch(() => props.show, (s) => {
  if (s) {
    q.value = ''
    idx.value = 0
    nextTick(() => input.value?.focus())
  }
})

interface Entry { label: string; hint: string; mono?: string; run: () => void }

function flatten(items: Item[], out: Item[] = []): Item[] {
  for (const i of items) {
    out.push(i)
    if (i.children) flatten(i.children, out)
  }
  return out
}

const commands: Entry[] = [
  { label: 'Speichern', hint: '⌘S', run: save },
  { label: 'Rückgängig', hint: '⌘Z', run: undo },
  { label: 'Wiederholen', hint: '⇧⌘Z', run: redo },
]

const entries = computed<Entry[]>(() => {
  const s = q.value.trim().toLowerCase()
  const gas = flatten(store.trees.groupaddresses).filter((i) => i.kind === 'ga' || i.kind === 'range')
  const devs = flatten(store.trees.topology).filter((i) => i.kind === 'device')
  const all: Entry[] = [
    ...commands,
    ...devs.map((d) => ({ label: d.label, mono: d.mono, hint: 'Gerät', run: () => emit('reveal', 'topology', d.key) })),
    ...gas.map((g) => ({ label: g.label, mono: g.mono, hint: g.kind === 'ga' ? 'Gruppenadresse' : 'Gruppe', run: () => emit('reveal', 'groupaddresses', g.key) })),
  ]
  if (!s) return commands
  return all.filter((e) => `${e.mono ?? ''} ${e.label}`.toLowerCase().includes(s)).slice(0, 60)
})

function choose(e?: Entry) {
  if (!e) return
  emit('update:show', false)
  e.run()
}
function key(ev: KeyboardEvent) {
  if (ev.key === 'ArrowDown') { ev.preventDefault(); idx.value = Math.min(entries.value.length - 1, idx.value + 1) }
  if (ev.key === 'ArrowUp') { ev.preventDefault(); idx.value = Math.max(0, idx.value - 1) }
  if (ev.key === 'Enter') { ev.preventDefault(); choose(entries.value[idx.value]) }
}
watch(q, () => (idx.value = 0))
</script>

<template>
  <NModal :show="show" @update:show="(v: boolean) => emit('update:show', v)" :auto-focus="false">
    <div class="palette">
      <input ref="input" v-model="q" placeholder="Gruppenadresse, Gerät oder Befehl …" @keydown="key" />
      <ul>
        <li v-for="(e, i) in entries" :key="e.hint + (e.mono ?? '') + e.label" :class="{ on: i === idx }"
            @mouseenter="idx = i" @click="choose(e)">
          <span v-if="e.mono" class="mono">{{ e.mono }}</span>
          <span class="lbl">{{ e.label }}</span>
          <span class="hint">{{ e.hint }}</span>
        </li>
        <li v-if="!entries.length" class="muted">Nichts gefunden</li>
      </ul>
    </div>
  </NModal>
</template>

<style scoped>
.palette { width: min(640px, 92vw); margin-top: -20vh; background: var(--kl-bg); color: var(--kl-text); border: 1px solid var(--kl-border); border-radius: 10px; box-shadow: 0 20px 60px rgb(0 0 0 / 25%); overflow: hidden; }
input { width: 100%; box-sizing: border-box; font: inherit; font-size: 16px; padding: 14px 16px; border: 0; border-bottom: 1px solid var(--kl-border); background: transparent; color: inherit; outline: none; }
ul { list-style: none; margin: 0; padding: 6px; max-height: 50vh; overflow: auto; }
li { display: flex; gap: 10px; align-items: center; padding: 7px 10px; border-radius: 6px; cursor: pointer; font-size: 13px; }
li.on { background: var(--kl-sel); }
.lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hint { color: var(--kl-muted); font-size: 12px; }
</style>
