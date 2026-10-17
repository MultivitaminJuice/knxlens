<script setup lang="ts">
import { computed } from 'vue'
import { NConfigProvider, NDialogProvider, NMessageProvider, darkTheme, useOsTheme, type GlobalThemeOverrides } from 'naive-ui'
import Shell from './components/Shell.vue'

const os = useOsTheme()
const theme = computed(() => (os.value === 'dark' ? darkTheme : null))
const overrides: GlobalThemeOverrides = {
  common: { primaryColor: '#2563eb', primaryColorHover: '#3b82f6', primaryColorPressed: '#1d4ed8', borderRadius: '5px' },
}
</script>

<template>
  <NConfigProvider :theme="theme" :theme-overrides="overrides" class="root">
    <NMessageProvider placement="bottom-right">
      <NDialogProvider>
        <Shell />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style scoped>
.root { height: 100%; }
</style>
