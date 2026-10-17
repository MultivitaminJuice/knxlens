<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { NInput, NModal, NSwitch, useDialog } from 'naive-ui'
import ParamField from './ParamField.vue'
import { api, ApiError, type DeviceDetail, type Orphan } from '../api'
import { loadAll, store } from '../store'

const detail = ref<DeviceDetail | null>(null)
const page = ref<string>('')
const search = ref('')
const onlyChanged = ref(false)
const busy = ref(false)
const dialog = useDialog()

// Rückfrage, wenn eine Änderung Objekte mit Gruppenadressen wegfallen lässt
function askOrphans(message: string, list: Orphan[]): Promise<'unlink' | 'keep' | null> {
  return new Promise((resolve) => {
    let answered = false
    const done = (v: 'unlink' | 'keep' | null) => { if (!answered) { answered = true; resolve(v) } }
    const d = dialog.warning({
      title: message,
      content: () => h('div', { class: 'orphans' }, [
        h('p', 'Diese Objekte gibt es nach der Änderung nicht mehr:'),
        h('ul', list.map((o) => h('li', [h('b', `${o.number} `), o.text, h('span', { class: 'muted' }, ` → ${o.gas.join(', ')}`)]))),
        h('p', { class: 'muted' }, '„Lösen“ entfernt die Zuordnungen. „Behalten“ lässt sie als inaktiv stehen – sie werden nicht ins Gerät geladen und kommen zurück, wenn du die Einstellung zurücknimmst.'),
      ]),
      closable: true,
      maskClosable: false,
      action: () => h('div', { class: 'acts' }, [
        h('button', { class: 'btn', onClick: () => { done(null); d.destroy() } }, 'Abbrechen'),
        h('button', { class: 'btn', onClick: () => { done('keep'); d.destroy() } }, 'Behalten'),
        h('button', { class: 'btn danger', onClick: () => { done('unlink'); d.destroy() } }, 'Zuordnungen lösen'),
      ]),
      onClose: () => done(null),
      onAfterLeave: () => done(null),
    })
  })
}

const show = computed({
  get: () => !!store.paramsFor,
  set: (v: boolean) => { if (!v) store.paramsFor = null },
})

watch(() => store.paramsFor, async (uid) => {
  detail.value = null
  search.value = ''
  if (!uid) return
  detail.value = await api.device(uid)
  page.value = title(detail.value.params[0]?.path ?? [])
})

function title(path: string[]) {
  return path.join(' › ') || 'Allgemein'
}

const pages = computed(() =>
  (detail.value?.params ?? []).map((g) => ({
    title: title(g.path),
    depth: Math.max(0, g.path.length - 1),
    label: g.path[g.path.length - 1] || 'Allgemein',
    items: g.items,
    changed: g.items.filter((i) => i.changed).length,
  })),
)

// Navigation mit Zwischenüberschriften für übergeordnete Seiten (z. B. „Kanal A“ über „A“, „Logik“)
const nav = computed(() => {
  const out: ({ header: true; label: string; depth: number; key: string } | ({ header: false; key: string } & (typeof pages.value)[number]))[] = []
  const shown = new Set<string>()
  for (const p of pages.value) {
    const segs = p.title === 'Allgemein' ? [] : p.title.split(' › ')
    for (let d = 0; d < segs.length - 1; d++) {
      const key = segs.slice(0, d + 1).join(' › ')
      if (!shown.has(key) && !pages.value.some((x) => x.title === key)) {
        shown.add(key)
        out.push({ header: true, label: segs[d], depth: d, key: 'h:' + key })
      }
    }
    out.push({ header: false, key: p.title, ...p })
  }
  return out
})

const visibleItems = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (q || onlyChanged.value) {
    // Suche/Filter über alle Seiten
    return pages.value.flatMap((p) =>
      p.items
        .filter((i) => (!q || i.text.toLowerCase().includes(q) || i.display.toLowerCase().includes(q)) && (!onlyChanged.value || i.changed))
        .map((i) => ({ ...i, page: p.title })),
    )
  }
  return (pages.value.find((p) => p.title === page.value) ?? pages.value[0])?.items.map((i) => ({ ...i, page: '' })) ?? []
})

async function change(id: string, value: string | null) {
  if (!detail.value) return
  busy.value = true
  try {
    const before = pages.value.length
    try {
      detail.value = await api.setParam(detail.value.uid, id, value)
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) throw e
      const choice = await askOrphans(e.message, (e.detail as { orphans: Orphan[] }).orphans)
      if (!choice) {
        detail.value = await api.device(detail.value.uid) // Feld auf den alten Wert zurück
        return
      }
      detail.value = await api.setParam(detail.value.uid, id, value, choice)
      store.notify('info', choice === 'unlink' ? 'Zuordnungen gelöst' : 'Zuordnungen bleiben als inaktiv stehen')
    }
    if (!pages.value.some((p) => p.title === page.value)) page.value = pages.value[0]?.title ?? ''
    const diff = pages.value.length - before
    if (diff) store.notify('info', diff > 0 ? `${diff} Parameterseite(n) eingeblendet` : `${-diff} Parameterseite(n) ausgeblendet`)
    await loadAll()
  } catch (e) {
    store.notify('error', e instanceof ApiError ? e.message : String(e))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <NModal v-model:show="show" preset="card" :bordered="false" class="dlg" :segmented="{ content: true }" :auto-focus="false"
          :title="detail ? `${detail.pa ?? '–'}  ${detail.name || detail.product}` : 'Parameter'">
    <template #header-extra><span class="muted">{{ detail?.product }}</span></template>
    <div v-if="detail" class="body">
      <nav class="pages">
        <NInput v-model:value="search" size="small" clearable placeholder="Parameter suchen …" />
        <label class="only"><NSwitch v-model:value="onlyChanged" size="small" /> nur geänderte</label>
        <template v-for="p in nav" :key="p.key">
          <div v-if="p.header" class="group" :style="{ paddingLeft: `${8 + p.depth * 14}px` }">{{ p.label }}</div>
          <button v-else class="page" :class="{ on: p.title === page && !search && !onlyChanged }"
                  :style="{ paddingLeft: `${8 + p.depth * 14}px` }" @click="page = p.title; search = ''; onlyChanged = false">
            <span>{{ p.label }}</span><span v-if="p.changed" class="cnt">{{ p.changed }}</span>
          </button>
        </template>
      </nav>
      <div class="fields">
        <p v-if="!visibleItems.length" class="muted">Keine Parameter.</p>
        <div v-for="it in visibleItems" :key="it.id" class="field" :class="{ changed: it.changed }">
          <div class="name">
            {{ it.text }}
            <span v-if="it.page" class="muted pageref">{{ it.page }}</span>
            <span v-if="it.changed" class="muted def">Standard: {{ it.default }}</span>
          </div>
          <ParamField :item="it" :busy="busy" @change="(v) => change(it.id, v)" />
        </div>
      </div>
    </div>
    <p v-else class="muted">lädt …</p>
  </NModal>
</template>

<style scoped>
:global(.dlg) { width: min(1100px, 94vw); }
.body { display: grid; grid-template-columns: 260px 1fr; gap: 16px; height: min(72vh, 760px); }
.pages { display: flex; flex-direction: column; gap: 2px; overflow: auto; border-right: 1px solid var(--kl-border); padding-right: 8px; }
.only { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--kl-muted); margin: 6px 0; }
.page { font: inherit; font-size: 13px; text-align: left; border: 0; background: none; color: var(--kl-text); padding: 5px 8px; border-radius: 4px; cursor: pointer; display: flex; justify-content: space-between; gap: 6px; }
.page:hover { background: var(--kl-hover); }
.group { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--kl-muted); padding: 10px 8px 2px; }
.page.on { background: var(--kl-sel); font-weight: 600; }
.cnt { font-size: 11px; background: var(--kl-accent); color: #fff; border-radius: 8px; padding: 0 6px; }
.fields { overflow: auto; display: flex; flex-direction: column; }
.field { display: grid; grid-template-columns: minmax(200px, 1fr) minmax(220px, 1fr); gap: 12px; align-items: center; padding: 6px 4px; border-bottom: 1px solid var(--kl-border); }
.field.changed .name { font-weight: 600; }
.name { display: flex; flex-direction: column; font-size: 13px; }
.pageref, .def { font-size: 11px; font-weight: 400; }
:global(.orphans ul) { margin: 6px 0; padding-left: 18px; font-size: 13px; }
:global(.orphans li) { margin: 2px 0; }
:global(.acts) { display: flex; gap: 8px; justify-content: flex-end; }
:global(.acts .btn) { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 4px; border: 1px solid var(--kl-border); background: var(--kl-bg); color: var(--kl-text); cursor: pointer; }
:global(.acts .btn.danger) { background: #d03050; border-color: #d03050; color: #fff; }
</style>
