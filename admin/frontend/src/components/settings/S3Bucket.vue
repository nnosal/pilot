<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!connected" theme="blue" title="Why connect S3?" :dismissible="false">
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connect an S3-compatible bucket to send offsite backups and snapshots.
        </p>
      </template>
    </Alert>

    <div v-if="connected" class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3 bg-surface-gray-1 p-4 rounded-lg border border-outline-gray-2">
      <div>
        <p class="font-medium text-ink-gray-8 text-sm flex items-center gap-2">
          <span class="lucide-check-circle text-green-600 size-4"></span>
          Connected to {{ bucket }}
        </p>
        <p class="text-ink-gray-5 text-p-sm">{{ providerLabel }} · Access key {{ accessKey }}</p>
      </div>
      <Button class="flex-1 sm:flex-none" variant="solid" theme="red" title="Disconnect from current S3 bucket" :loading="disconnecting"
        @click="disconnect">Disconnect</Button>
    </div>

    <div class="space-y-4">
      <!-- Minio Toggle -->
      <div class="flex items-center gap-2">
        <Switch v-model="isMinio" />
        <label class="text-sm font-medium text-ink-gray-7">Minio / S3 Compatible</label>
      </div>

      <!-- Endpoint (Minio) or Provider/Region (S3) -->
      <div v-if="!isMinio" class="space-y-4">
        <div class="flex sm:flex-row flex-col gap-4">
          <Select label="Provider" v-model="provider" :options="providerOptions" class="w-full" />
          <Select label="Region" v-model="region" :options="regionOptions" class="w-full"
            :disabled="buckets.some(b => b.name === bucket && b.region === region)" />
        </div>
      </div>

      <div v-else class="space-y-4">
        <FormControl label="Endpoint" type="text" v-model="endpoint" placeholder="https://eu2.contabostorage.com" />
        <div class="flex items-center gap-2 text-sm text-ink-gray-6">
          <span class="lucide-info size-4"></span>
          <span>Any S3-compatible endpoint (Minio, Contabo, …). Region is detected automatically.</span>
        </div>
      </div>

      <!-- Access Key & Secret Key -->
      <div class="space-y-4">
        <FormControl label="Access Key" type="text" v-model="accessKey" placeholder="AKIA…" />
        <FormControl label="Secret Key" type="password" v-model="secretKey"
          :placeholder="secretKeySet ? '••••••••' : 'Secret key'" />
      </div>

      <!-- Connection Test Buttons -->
      <div v-if="credentialsReady" class="flex flex-wrap items-center gap-3">
        <Button variant="solid" title="Test S3/Minio connection with provided credentials" icon-left="lucide-refresh-cw" :loading="testingConnection" @click="testConnection">
          Test Connection
        </Button>
        <Button v-if="isMinio" variant="solid" title="Auto-detect region from endpoint" icon-left="lucide-globe" :loading="detectingRegion" @click="detectRegion">
          Detect Region
        </Button>
      </div>

      <div v-if="isMinio && !credentialsConfigured" class="p-3 bg-amber-50 border border-amber-200 rounded-lg">
        <p class="text-sm text-amber-800">
          Configure credentials above to list available buckets
        </p>
      </div>

      <div v-if="bucketsLoading && credentialsConfigured" class="flex justify-center items-center h-20">
        <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
        <span class="ml-2 text-sm text-ink-gray-6">Loading buckets...</span>
      </div>

      <div v-if="!bucketsLoading && credentialsConfigured" class="space-y-2">
        <FormControl v-if="useManualBucket" label="Bucket" type="text" v-model="bucket" placeholder="my-bucket" />
        <Select
          v-else
          label="Bucket"
          v-model="bucket"
          :options="bucketOptions"
          class="w-full"
          placeholder="Select a bucket"
        />
        <div class="flex items-center justify-between gap-2">
          <FormControl v-if="buckets.length > 0" type="checkbox" v-model="manualBucket" label="Enter bucket name manually" />
          <span v-else class="text-sm text-ink-gray-6">Bucket list unavailable — enter the bucket name manually.</span>
          <Button variant="subtle" size="sm" title="Refresh list of available buckets" icon-left="lucide-refresh-cw" :loading="refreshingBuckets" @click="refreshBuckets">
            {{ buckets.length > 0 ? 'Refresh Buckets' : 'Retry' }}
          </Button>
        </div>
      </div>

      <ErrorMessage v-if="error" :message="error" />
      <div v-if="connectionSuccess" class="p-3 bg-green-50 border border-green-200 rounded-lg">
        <p class="text-sm text-green-800">
          ✓ {{ connectionSuccess }}
        </p>
      </div>

      <div class="flex justify-end">
        <Button variant="solid" :loading="saving" :disabled="!bucket || !credentialsConfigured"
          :title="connected ? 'Update S3 bucket connection settings' : 'Connect to selected S3 bucket'"
          @click="save">
          {{ connected ? 'Update' : 'Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, Select, Switch, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const disconnecting = ref(false)
const testingConnection = ref(false)
const detectingRegion = ref(false)
const refreshingBuckets = ref(false)
const bucketsLoading = ref(false)
const error = ref('')
const connectionSuccess = ref('')
const accessKey = ref('')
const secretKey = ref('')
const bucket = ref('')
const provider = ref('')
const region = ref('')
const endpoint = ref('')
const isMinio = ref(false)
const secretKeySet = ref(false)
const providers = ref([])
const buckets = ref([])

const credentialsConfigured = computed(() => {
  return Boolean(accessKey.value && (secretKeySet.value || secretKey.value))
})

const credentialsReady = computed(() => {
  if (!credentialsConfigured.value) return false
  return isMinio.value ? Boolean(endpoint.value.trim()) : Boolean(provider.value && region.value)
})

const manualBucket = ref(false)
// Empty listing (provider denies ListBuckets) forces manual entry
const useManualBucket = computed(() => manualBucket.value || buckets.value.length === 0)

// Snapshot of the saved config (set by load) — the GET bucket listing uses the
// saved secret, so it is only valid while the form still matches that config
const savedTarget = ref(null)
const formMatchesSaved = computed(() => {
  const saved = savedTarget.value
  if (!saved || isMinio.value !== saved.isMinio) return false
  if (isMinio.value) return endpoint.value.trim() === saved.endpoint
  return provider.value === saved.provider && region.value === saved.region
})

const connected = computed(() => {
  if (isMinio.value) {
    return Boolean(accessKey.value && bucket.value && endpoint.value && secretKeySet.value)
  }
  return Boolean(accessKey.value && bucket.value && provider.value && region.value && secretKeySet.value)
})

const providerLabel = computed(() => {
  if (isMinio.value) return 'Minio / S3 Compatible'
  return providers.value.find((p) => p.value === provider.value)?.label || provider.value
})

const providerOptions = computed(() => providers.value.map((p) => ({ label: p.label, value: p.value })))

const regionOptions = computed(
  () => providers.value.find((p) => p.value === provider.value)?.regions.map((r) => ({ label: r, value: r })) || [],
)

const bucketOptions = computed(() =>
  buckets.value
    .filter(b => {
      // Filter by region for non-Minio providers
      if (!isMinio.value && region.value && b.region) {
        return b.region === region.value
      }
      return true
    })
    .map(b => ({ label: b.name, value: b.name }))
)

// True while load() hydrates the form from the saved config — the reset
// watchers below must not wipe freshly loaded values
let hydrating = false

watch(provider, () => {
  if (hydrating) return
  if (!isMinio.value && !regionOptions.value.some((o) => o.value === region.value)) {
    region.value = regionOptions.value[0]?.value || ''
  }
})

watch(region, () => {
  // Refresh buckets when region changes (for non-Minio)
  if (!isMinio.value && credentialsConfigured.value) {
    loadBuckets()
  }
})

watch(isMinio, () => {
  if (hydrating) return
  connectionSuccess.value = ''
  // Buckets listed for the previous mode are meaningless in the new one
  buckets.value = []
  bucket.value = ''
  if (isMinio.value) {
    provider.value = ''
    region.value = ''
  } else {
    // Don't reset endpoint when toggling - preserve user input
    if (providers.value.length > 0) {
      provider.value = providers.value[0]?.value || ''
      region.value = regionOptions.value[0]?.value || ''
    }
  }
})

let bucketsLoadTimer = null
watch([accessKey, secretKey, endpoint, isMinio], () => {
  clearTimeout(bucketsLoadTimer)
  if (hydrating || !credentialsReady.value) return
  bucketsLoadTimer = setTimeout(loadBuckets, 800)
})

async function loadBuckets() {
  const hasFormCredentials = Boolean(accessKey.value && secretKey.value)
  // Without form credentials, the GET path lists via the SAVED config — only
  // valid while the form still targets it (e.g. not after toggling Minio off)
  if (!hasFormCredentials && !(secretKeySet.value && formMatchesSaved.value)) {
    buckets.value = []
    return
  }

  bucketsLoading.value = true
  try {
    let response
    if (hasFormCredentials) {
      response = await fetch('/api/v1/s3/buckets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          access_key: accessKey.value.trim(),
          secret_key: secretKey.value.trim(),
          provider: isMinio.value ? 'minio' : provider.value,
          region: isMinio.value ? '' : region.value,
          endpoint: isMinio.value ? endpoint.value.trim() : '',
          is_minio: isMinio.value,
        }),
      })
    } else {
      const url = new URL('/api/v1/s3/buckets', window.location.origin)
      if (!isMinio.value && region.value) {
        url.searchParams.set('region', region.value)
      }
      response = await fetch(url.toString())
    }
    if (response.ok) {
      const data = await response.json()
      buckets.value = data.buckets || []
    } else {
      // Listing can legitimately fail (provider denies ListBuckets) — the
      // manual bucket input covers it, no error banner needed
      buckets.value = []
    }
  } catch {
    buckets.value = []
  } finally {
    bucketsLoading.value = false
  }
}

async function load() {
  loading.value = true
  hydrating = true
  try {
    const data = await settingsApi.get()
    providers.value = data.s3_providers || []
    const s3 = data.s3 || {}
    accessKey.value = s3.access_key || ''
    bucket.value = s3.bucket || ''
    provider.value = s3.provider || providers.value[0]?.value || ''
    region.value = s3.region || ''
    endpoint.value = s3.endpoint || ''
    isMinio.value = s3.is_minio || false
    secretKeySet.value = !!s3.secret_key_set
    savedTarget.value = {
      isMinio: isMinio.value,
      endpoint: endpoint.value.trim(),
      provider: provider.value,
      region: region.value,
    }

    // Load buckets if credentials are configured
    if (credentialsConfigured.value) {
      await loadBuckets()
    }
    await nextTick()
  } finally {
    hydrating = false
    loading.value = false
  }
}

async function testConnection() {
  if (!accessKey.value.trim() || !secretKey.value.trim()) {
    error.value = 'Access key and secret key are required.'
    return
  }

  if (isMinio.value && !endpoint.value.trim()) {
    error.value = 'Endpoint is required for Minio.'
    return
  }

  if (!isMinio.value && (!provider.value || !region.value)) {
    error.value = 'Provider and region are required.'
    return
  }

  testingConnection.value = true
  connectionSuccess.value = ''
  error.value = ''
  try {
    const response = await fetch('/api/v1/s3/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        access_key: accessKey.value.trim(),
        secret_key: secretKey.value.trim(),
        provider: isMinio.value ? 'minio' : provider.value,
        region: isMinio.value ? (region.value || 'us-east-1') : region.value,
        endpoint: isMinio.value ? endpoint.value.trim() : '',
        is_minio: isMinio.value,
        bucket: bucket.value || 'test',
      }),
    })

    const result = await response.json()
    if (response.ok && result.success) {
      connectionSuccess.value = result.message || 'Connection successful'
      toast.success('S3 connection test successful')
      // Auto-load buckets on successful connection
      await loadBuckets()
    } else {
      error.value = result.error?.message || result.error || 'Connection test failed'
    }
  } catch (e) {
    error.value = e.message || 'Connection test failed'
  } finally {
    testingConnection.value = false
  }
}

async function detectRegion() {
  if (!endpoint.value.trim()) {
    error.value = 'Please enter an endpoint first.'
    return
  }

  detectingRegion.value = true
  error.value = ''
  try {
    const response = await fetch('/api/v1/s3/detect-region', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: endpoint.value.trim() }),
    })

    const result = await response.json()
    if (response.ok && result.success) {
      region.value = result.region || 'us-east-1'
      toast.success(`Region detected: ${region.value}`)
    } else {
      region.value = region.value || 'us-east-1'
      toast.warning(`Could not detect region — using ${region.value}`)
    }
  } catch (e) {
    region.value = region.value || 'us-east-1'
    toast.warning(`Could not detect region — using ${region.value}`)
  } finally {
    detectingRegion.value = false
  }
}

async function refreshBuckets() {
  connectionSuccess.value = ''
  refreshingBuckets.value = true
  try {
    await loadBuckets()
  } finally {
    refreshingBuckets.value = false
  }
  if (buckets.value.length > 0) {
    toast.success('Bucket list refreshed')
  } else {
    toast.warning('Could not list buckets — enter the bucket name manually')
  }
}

async function save() {
  if (isMinio.value) {
    if (!accessKey.value.trim() || !bucket.value.trim() || !endpoint.value.trim()) {
      error.value = 'Access key, bucket, and endpoint are required for Minio.'
      return
    }
  } else {
    if (!accessKey.value.trim() || !bucket.value.trim() || !provider.value || !region.value) {
      error.value = 'Access key, bucket, provider, and region are required.'
      return
    }
  }

  if (!secretKeySet.value && !secretKey.value.trim()) {
    error.value = 'Secret key is required.'
    return
  }

  saving.value = true
  error.value = ''
  try {
    const payload = {
      access_key: accessKey.value.trim(),
      secret_key: secretKey.value.trim(),
      bucket: bucket.value.trim(),
      is_minio: isMinio.value,
    }

    if (isMinio.value) {
      payload.endpoint = endpoint.value.trim()
      payload.provider = 'minio'
      payload.region = region.value || 'us-east-1'
    } else {
      payload.provider = provider.value
      payload.region = region.value
    }

    const result = await settingsApi.update({ s3: payload })
    if (!result.error) {
      secretKey.value = ''
      toast.success('S3 settings saved')
      await load()
    } else {
      error.value = apiErrorMessage(result, 'Could not save S3 settings.')
    }
  } catch (e) {
    error.value = e.message || 'Could not save S3 settings.'
  } finally {
    saving.value = false
  }
}

async function disconnect() {
  disconnecting.value = true
  try {
    const result = await settingsApi.update({ s3: { disconnect: true } })
    if (!result.error) {
      accessKey.value = ''
      secretKey.value = ''
      bucket.value = ''
      provider.value = providers.value[0]?.value || ''
      region.value = ''
      endpoint.value = ''
      isMinio.value = false
      buckets.value = []
      secretKeySet.value = false
      savedTarget.value = null
      toast.success('S3 disconnected')
    } else {
      toast.error(apiErrorMessage(result, 'Could not disconnect S3.'))
    }
  } catch (e) {
    toast.error(e.message || 'Could not disconnect S3.')
  } finally {
    disconnecting.value = false
  }
}

onMounted(load)
</script>
