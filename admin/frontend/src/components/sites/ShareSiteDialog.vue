<template>
  <Dialog v-model="open" :options="{ title: 'Share this site', size: 'md' }">
    <template #body-content>
      <div v-if="loading" class="flex justify-center items-center h-24">
        <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
      </div>

      <!-- Provider choice: only shown when both ngrok and slim are configured -->
      <div v-else-if="phase === 'choose'" class="space-y-3">
        <p class="text-ink-gray-6 text-p-sm">Choose how to expose <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> publicly.</p>
        <button type="button"
          class="flex justify-between items-center gap-3 hover:bg-surface-gray-1 px-3 py-2 rounded border border-outline-gray-2 w-full text-left"
          @click="startShare">
          <span class="text-ink-gray-8 text-sm">slim share</span>
          <span class="size-4 text-ink-gray-5 lucide-arrow-right"></span>
        </button>
        <div class="flex justify-between items-center gap-3 opacity-50 px-3 py-2 rounded border border-outline-gray-2 w-full text-left cursor-not-allowed">
          <span class="text-ink-gray-8 text-sm">ngrok tunnel</span>
          <span class="text-ink-gray-5 text-p-xs">Coming soon</span>
        </div>
      </div>

      <!-- Live tunnel state -->
      <div v-else-if="phase === 'live'" class="space-y-3">
        <div v-if="status === 'starting'" class="flex items-center gap-2 text-ink-gray-6 text-p-sm">
          <span class="size-3.5 animate-spin lucide-loader-circle"></span>
          Opening tunnel…
        </div>

        <template v-else-if="status === 'live'">
          <p class="text-ink-gray-6 text-p-sm">
            <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> is live at:
          </p>
          <a :href="publicUrl" target="_blank" rel="noopener"
            class="block bg-surface-gray-1 px-3 py-2 rounded border border-outline-gray-2 text-ink-gray-8 text-p-sm underline underline-offset-2 break-all">
            {{ publicUrl }}
          </a>
        </template>

        <ErrorMessage v-else-if="status === 'failed'" :message="message || 'Could not open the tunnel.'" />

        <p v-else-if="status === 'stopped'" class="text-ink-gray-6 text-p-sm">Sharing stopped.</p>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2 mt-4">
          <Button variant="outline" @click="open = false">Close</Button>
          <Button v-if="status === 'live'" variant="solid" theme="red" :loading="stopping" @click="stopShare">
            Stop sharing
          </Button>
          <Button v-else-if="status === 'failed' || status === 'stopped'" variant="solid" @click="startShare">
            Retry
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { Button, Dialog, ErrorMessage } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { shareApi } from '@/api/share'
import { sitesApi } from '@/api/sites'

const props = defineProps({ siteName: { type: String, required: true } })
const open = defineModel({ type: Boolean, default: false })

const loading = ref(true)
const phase = ref('choose') // 'choose' | 'live'
const status = ref('starting') // starting | live | failed | stopped
const publicUrl = ref('')
const message = ref('')
const error = ref('')
const stopping = ref(false)
let pollTimer = null

function stopPoll() {
  clearTimeout(pollTimer)
  pollTimer = null
}

async function pollStatus() {
  try {
    const snapshot = await sitesApi.share.status(props.siteName)
    if (snapshot.error) {
      stopPoll()
      status.value = 'failed'
      message.value = apiErrorMessage(snapshot, 'Could not check share status.')
      return
    }
    status.value = snapshot.status
    publicUrl.value = snapshot.url || publicUrl.value
    message.value = snapshot.message || ''
    if (snapshot.status === 'starting') {
      pollTimer = setTimeout(pollStatus, 1500)
    }
  } catch (e) {
    stopPoll()
    status.value = 'failed'
    message.value = e.message || 'Could not check share status.'
  }
}

async function startShare() {
  phase.value = 'live'
  status.value = 'starting'
  error.value = ''
  publicUrl.value = ''
  try {
    const snapshot = await sitesApi.share.start(props.siteName)
    if (snapshot.error) {
      status.value = 'failed'
      message.value = apiErrorMessage(snapshot, 'Could not start the tunnel.')
      return
    }
    status.value = snapshot.status
    publicUrl.value = snapshot.url || ''
    message.value = snapshot.message || ''
    if (snapshot.status === 'starting') pollStatus()
  } catch (e) {
    status.value = 'failed'
    message.value = e.message || 'Could not start the tunnel.'
  }
}

async function stopShare() {
  stopping.value = true
  try {
    await sitesApi.share.stop(props.siteName)
    stopPoll()
    status.value = 'stopped'
    publicUrl.value = ''
  } finally {
    stopping.value = false
  }
}

watch(open, async (value) => {
  if (!value) {
    stopPoll()
    return
  }
  loading.value = true
  error.value = ''
  try {
    const [ngrok, slim] = await Promise.all([shareApi.ngrokStatus(), shareApi.slimStatus()])
    if (ngrok.connected && slim.connected) {
      phase.value = 'choose'
    } else if (slim.connected) {
      phase.value = 'live'
      await startShare()
    } else {
      phase.value = 'live'
      status.value = 'failed'
      message.value = 'Connect slim in Settings > Share first.'
    }
  } finally {
    loading.value = false
  }
}, { immediate: true })

onBeforeUnmount(stopPoll)
</script>
