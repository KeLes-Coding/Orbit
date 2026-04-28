<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useOrbit } from '../composables/useOrbit'

const {
  activeView,
  user,
  sortedConversations,
  activeConversationId,
  isBooting,
  displayName,
  accountInitial,
  setActiveView,
  logout,
  openAuth,
  createNewThread,
  selectConversation,
  formatConversationTitle,
  renameConversation,
  archiveConversation,
} = useOrbit()

const editingThreadId = ref(null)
const editingTitle = ref('')
const editInputRef = ref(null)
const showAccountMenu = ref(false)
const accountBtnRef = ref(null)

const startRename = (conversation, event) => {
  event.stopPropagation()
  editingThreadId.value = conversation.id
  editingTitle.value = conversation.title || ''
  nextTick(() => {
    editInputRef.value?.focus()
    editInputRef.value?.select()
  })
}

const submitRename = async () => {
  const title = editingTitle.value.trim()
  if (title && editingThreadId.value) {
    await renameConversation(editingThreadId.value, title)
  }
  cancelEdit()
}

const cancelEdit = () => {
  editingThreadId.value = null
  editingTitle.value = ''
}

const handleDelete = async (conversation, event) => {
  event.stopPropagation()
  if (!confirm(`Archive "${formatConversationTitle(conversation)}"?`)) return
  await archiveConversation(conversation.id)
}

const toggleAccountMenu = () => {
  if (!user.value) {
    openAuth()
    return
  }
  showAccountMenu.value = !showAccountMenu.value
}

const handleMenuAction = (action) => {
  showAccountMenu.value = false
  action()
}

const onClickOutside = (event) => {
  if (showAccountMenu.value && accountBtnRef.value && !accountBtnRef.value.contains(event.target)) {
    showAccountMenu.value = false
  }
}

onMounted(() => document.addEventListener('click', onClickOutside))
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<template>
  <aside class="side-nav" aria-label="Primary navigation">
    <div class="brand-panel">
      <h1>Orbit</h1>
      <p>Zen AI Assistant</p>
    </div>

    <div class="nav-action">
      <button type="button" class="primary-button" @click="createNewThread()">
        <span class="material-symbols-outlined" aria-hidden="true">add</span>
        <span>New Chat</span>
      </button>
    </div>

    <nav class="nav-list" aria-label="Workspace">
      <div class="thread-section-title">Chats</div>
      <div class="thread-list" aria-label="Recent conversations">
        <div
          v-for="conversation in sortedConversations"
          :key="conversation.id"
          class="thread-item"
          :class="{ active: conversation.id === activeConversationId }"
        >
          <template v-if="editingThreadId === conversation.id">
            <input
              ref="editInputRef"
              v-model="editingTitle"
              class="thread-edit-input"
              @keydown.enter="submitRename()"
              @keydown.escape="cancelEdit()"
              @blur="submitRename()"
              @click.stop
            />
          </template>
          <template v-else>
            <button type="button" class="thread-button" @click="selectConversation(conversation.id)">
              {{ formatConversationTitle(conversation) }}
            </button>
            <div class="thread-actions">
              <button
                type="button"
                class="thread-action-btn"
                aria-label="Rename"
                title="Rename"
                @click="startRename(conversation, $event)"
              >
                <span class="material-symbols-outlined">edit</span>
              </button>
              <button
                type="button"
                class="thread-action-btn thread-action-delete"
                aria-label="Archive"
                title="Archive"
                @click="handleDelete(conversation, $event)"
              >
                <span class="material-symbols-outlined">close</span>
              </button>
            </div>
          </template>
        </div>
        <p v-if="isBooting">Loading workspace...</p>
        <p v-else-if="!user">Sign in to sync chats</p>
        <p v-else-if="sortedConversations.length === 0">No conversations yet</p>
      </div>

      <button
        type="button"
        :class="['nav-item', { active: activeView === 'library' }]"
        @click="setActiveView('library')"
      >
        <span class="material-symbols-outlined" aria-hidden="true">book_2</span>
        <span>Library</span>
      </button>
    </nav>

    <div class="account-section" ref="accountBtnRef">
      <button type="button" class="account-button" @click="toggleAccountMenu">
        <span class="avatar" aria-hidden="true">{{ user ? accountInitial : '?' }}</span>
        <span class="account-copy">
          <strong>{{ displayName }}</strong>
          <small>{{ user ? 'Sign out' : 'Sign in' }}</small>
        </span>
        <span class="material-symbols-outlined" aria-hidden="true">
          <template v-if="user">{{ showAccountMenu ? 'expand_less' : 'expand_more' }}</template>
          <template v-else>login</template>
        </span>
      </button>

      <div v-if="showAccountMenu && user" class="account-menu">
        <button
          type="button"
          class="account-menu-item"
          @click="handleMenuAction(() => setActiveView('library'))"
        >
          <span class="material-symbols-outlined">tune</span>
          <span>LLM Configs</span>
        </button>
        <button
          type="button"
          class="account-menu-item"
          @click="handleMenuAction(() => logout())"
        >
          <span class="material-symbols-outlined">logout</span>
          <span>Sign Out</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.thread-item {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 34px;
  padding: 0 8px;
  border-radius: 6px;
  transition:
    background 160ms ease,
    color 160ms ease;
}

.thread-item:hover,
.thread-item.active {
  background: var(--surface-low);
}

.thread-item.active .thread-button {
  color: var(--ink);
  font-weight: 600;
}

.thread-button {
  max-width: 100%;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  font-size: 14px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  flex: 1;
  min-width: 0;
  padding: 7px 0;
}

.thread-button:hover {
  color: var(--ink);
}

.thread-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  flex-shrink: 0;
  transition: opacity 160ms ease;
}

.thread-item:hover .thread-actions {
  opacity: 1;
}

.thread-action-btn {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--ink-soft);
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease;
}

.thread-action-btn:hover {
  background: var(--surface-mid);
  color: var(--ink);
}

.thread-action-delete:hover {
  color: #ba1a1a;
}

.theme-dark .thread-action-delete:hover {
  color: #ffb4ab;
}

.thread-action-btn .material-symbols-outlined {
  font-size: 14px;
}

.thread-edit-input {
  width: 100%;
  padding: 4px 8px;
  border: 1px solid var(--primary);
  border-radius: 4px;
  outline: 0;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  line-height: 1.4;
}

.account-section {
  position: relative;
}

.account-menu {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 0;
  right: 0;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow);
  z-index: 30;
}

.account-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background 140ms ease;
}

.account-menu-item:hover {
  background: var(--surface-low);
}

.account-menu-item .material-symbols-outlined {
  font-size: 18px;
  color: var(--ink-muted);
}
</style>
