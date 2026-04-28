<script setup>
import { useOrbit } from '../composables/useOrbit'

const {
  isDark,
  showAuth,
  errorMessage,
  isAuthenticating,
  authMode,
  authForm,
  submitAuth,
  closeAuth,
  toggleTheme,
  toggleAuthMode,
} = useOrbit()
</script>

<template>
  <section v-if="showAuth" class="auth-screen">
    <div class="auth-actions">
      <button
        type="button"
        class="icon-button"
        :aria-label="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
        @click="toggleTheme"
      >
        <span class="material-symbols-outlined" aria-hidden="true">
          {{ isDark ? 'light_mode' : 'dark_mode' }}
        </span>
      </button>
      <button type="button" class="icon-button" aria-label="Back to chat" @click="closeAuth">
        <span class="material-symbols-outlined" aria-hidden="true">close</span>
      </button>
    </div>
    <div class="auth-panel">
      <p class="auth-kicker">{{ authMode === 'login' ? 'Login' : 'Register' }}</p>
      <h1>Orbit</h1>
      <p class="auth-copy">Sign in to continue your conversations with the backend workspace.</p>

      <form class="auth-form" @submit.prevent="submitAuth">
        <label v-if="authMode === 'register'">
          <span>Display name</span>
          <input v-model="authForm.displayName" autocomplete="name" placeholder="Master Ink" />
        </label>
        <label>
          <span>Email</span>
          <input v-model="authForm.email" autocomplete="email" placeholder="you@example.com" type="email" required />
        </label>
        <label>
          <span>Password</span>
          <input
            v-model="authForm.password"
            :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
            placeholder="At least 8 characters"
            type="password"
            required
          />
        </label>

        <p v-if="errorMessage" class="status-message error">{{ errorMessage }}</p>

        <button type="submit" class="primary-button" :disabled="isAuthenticating">
          <span class="material-symbols-outlined" aria-hidden="true">
            {{ authMode === 'login' ? 'login' : 'person_add' }}
          </span>
          <span>{{ isAuthenticating ? 'Working' : authMode === 'login' ? 'Sign In' : 'Create Account' }}</span>
        </button>
      </form>

      <button
        type="button"
        class="text-button"
        @click="toggleAuthMode"
      >
        {{ authMode === 'login' ? 'Create a new account' : 'Use an existing account' }}
      </button>
    </div>
  </section>
</template>
