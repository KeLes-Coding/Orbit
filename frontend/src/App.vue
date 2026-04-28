<script setup>
import { computed } from 'vue'
import { useOrbit } from './composables/useOrbit'
import AuthScreen from './components/AuthScreen.vue'
import SideNav from './components/SideNav.vue'
import ChatShell from './components/ChatShell.vue'
import SettingsView from './components/SettingsView.vue'

const { isDark, showAuth, activeView } = useOrbit()

const appClass = computed(() => ({
  'app-shell': true,
  'theme-dark': isDark.value,
}))
</script>

<template>
  <div :class="appClass">
    <AuthScreen />
    <template v-if="!showAuth">
      <SideNav />
      <SettingsView v-if="activeView === 'library'" />
      <ChatShell v-else />
    </template>
  </div>
</template>
