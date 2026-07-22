<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-8">
    <!-- ngrok -->
    <div class="space-y-4">
      <h4 class="font-medium text-ink-gray-8 text-sm">ngrok</h4>
      <Alert v-if="!ngrokConnected" theme="blue" title="Why connect ngrok?" :dismissible="false">
        <template #description>
          <p class="text-ink-gray-6 text-p-sm">
            An authtoken lets <code class="text-xs">mise r tunnel:ngrok</code> expose a site publicly
            on demand. Sign in to ngrok and copy your
            <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" rel="noopener"
              class="underline underline-offset-2">authtoken</a> from the dashboard.
          </p>
        </template>
      </Alert>

      <div v-if="ngrokConnected" class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3">
        <div>
          <p class="font-medium text-ink-gray-8 text-sm">Connected</p>
          <p class="text-ink-gray-5 text-p-sm">Authtoken {{ ngrokTokenPreview }}</p>
        </div>
        <Button class="flex-1 sm:flex-none" variant="subtle" theme="red" :loading="ngrokDisconnecting"
          @click="disconnectNgrok">Disconnect</Button>
      </div>

      <div class="space-y-4">
        <FormControl label="Authtoken" type="password" v-model="ngrokToken"
          :placeholder="ngrokConnected ? ngrokTokenPreview : 'Paste your ngrok authtoken'"
          @keydown.enter="connectNgrok" />
        <ErrorMessage v-if="ngrokError" :message="ngrokError" />
        <div class="flex justify-end">
          <Button variant="solid" :loading="ngrokConnecting" @click="connectNgrok">
            {{ ngrokConnected ? 'Update Token' : 'Connect' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- slim -->
    <div class="space-y-4 pt-4 border-t border-outline-gray-2">
      <h4 class="font-medium text-ink-gray-8 text-sm">slim</h4>
      <Alert v-if="!slimConnected" theme="blue" title="Why connect slim?" :dismissible="false">
        <template #description>
          <p class="text-ink-gray-6 text-p-sm">
            Logging in unlocks <code class="text-xs">slim share</code>, a public HTTPS tunnel for a
            local site.
          </p>
        </template>
      </Alert>

      <div v-if="slimConnected" class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3">
        <p class="font-medium text-ink-gray-8 text-sm">Connected</p>
        <Button class="flex-1 sm:flex-none" variant="subtle" theme="red" :loading="slimDisconnecting"
          @click="disconnectSlim">Disconnect</Button>
      </div>

      <div v-else-if="slimLoginUrl" class="space-y-3">
        <p class="text-ink-gray-6 text-p-sm">Open this URL to finish signing in:</p>
        <a :href="slimLoginUrl" target="_blank" rel="noopener"
          class="block bg-surface-gray-1 px-3 py-2 rounded border border-outline-gray-2 text-ink-gray-8 text-p-sm underline underline-offset-2 break-all">
          {{ slimLoginUrl }}
        </a>
        <p class="flex items-center gap-2 text-ink-gray-5 text-p-sm">
          <span class="size-3.5 animate-spin lucide-loader-circle"></span>
          Waiting for authentication…
        </p>
      </div>

      <div v-else class="flex justify-end">
        <Button variant="solid" :loading="slimStarting" @click="loginSlim">Login with slim</Button>
      </div>
      <ErrorMessage v-if="slimError" :message="slimError" />
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { shareApi } from '@/api/share'

const loading = ref(true)

const ngrokConnected = ref(false)
const ngrokTokenPreview = ref('')
const ngrokToken = ref('')
const ngrokConnecting = ref(false)
const ngrokDisconnecting = ref(false)
const ngrokError = ref('')

const slimConnected = ref(false)
const slimStarting = ref(false)
const slimDisconnecting = ref(false)
const slimLoginUrl = ref('')
const slimError = ref('')
let slimPollTimer = null

async function load() {
  loading.value = true
  try {
    const [ngrok, slim] = await Promise.all([shareApi.ngrokStatus(), shareApi.slimStatus()])
    ngrokConnected.value = ngrok.connected
    ngrokTokenPreview.value = ngrok.token_preview
    slimConnected.value = slim.connected
  } finally {
    loading.value = false
  }
}

async function connectNgrok() {
  if (!ngrokToken.value.trim()) { ngrokError.value = 'Paste an ngrok authtoken to connect.'; return }
  ngrokConnecting.value = true
  ngrokError.value = ''
  try {
    const result = await shareApi.ngrokConnect(ngrokToken.value.trim())
    if (result.error) {
      ngrokError.value = apiErrorMessage(result, 'Could not save the ngrok authtoken.')
    } else {
      ngrokToken.value = ''
      ngrokConnected.value = result.connected
      ngrokTokenPreview.value = result.token_preview
      toast.success('ngrok connected')
    }
  } catch (e) {
    ngrokError.value = e.message || 'Could not save the ngrok authtoken.'
  } finally {
    ngrokConnecting.value = false
  }
}

async function disconnectNgrok() {
  ngrokDisconnecting.value = true
  try {
    await shareApi.ngrokDisconnect()
    ngrokConnected.value = false
    ngrokTokenPreview.value = ''
    toast.success('ngrok disconnected')
  } finally {
    ngrokDisconnecting.value = false
  }
}

function stopSlimPoll() {
  clearTimeout(slimPollTimer)
  slimPollTimer = null
}

async function pollSlimLogin() {
  try {
    const snapshot = await shareApi.slimLoginStatus()
    if (snapshot.error) {
      stopSlimPoll()
      slimLoginUrl.value = ''
      slimError.value = apiErrorMessage(snapshot, 'Could not check slim login status.')
      return
    }
    slimLoginUrl.value = snapshot.url || slimLoginUrl.value
    if (snapshot.status === 'success') {
      stopSlimPoll()
      slimLoginUrl.value = ''
      slimConnected.value = true
      toast.success('slim connected')
      return
    }
    if (snapshot.status === 'failed') {
      stopSlimPoll()
      slimLoginUrl.value = ''
      slimError.value = snapshot.message || 'slim login failed.'
      return
    }
    slimPollTimer = setTimeout(pollSlimLogin, 2000)
  } catch (e) {
    stopSlimPoll()
    slimError.value = e.message || 'Could not check slim login status.'
  }
}

async function loginSlim() {
  slimStarting.value = true
  slimError.value = ''
  try {
    const snapshot = await shareApi.slimLogin()
    if (snapshot.error) {
      slimError.value = apiErrorMessage(snapshot, 'Could not start slim login.')
      return
    }
    slimLoginUrl.value = snapshot.url || ''
    if (snapshot.status === 'failed') {
      slimError.value = snapshot.message || 'Could not start slim login.'
      return
    }
    pollSlimLogin()
  } catch (e) {
    slimError.value = e.message || 'Could not start slim login.'
  } finally {
    slimStarting.value = false
  }
}

async function disconnectSlim() {
  slimDisconnecting.value = true
  try {
    await shareApi.slimDisconnect()
    slimConnected.value = false
    toast.success('slim disconnected')
  } finally {
    slimDisconnecting.value = false
  }
}

onMounted(load)
onBeforeUnmount(stopSlimPoll)
</script>
