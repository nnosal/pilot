<template>
  <Dialog v-model="open" :title="jobId ? 'Creating project' : 'New project (mef)'" size="lg">
    <template #default>
      <!-- Job streaming view: once we have a job_id we leave the form up but
           switch the body to the log tail until the process exits. -->
      <div v-if="jobId" class="flex flex-col gap-3" @pointerdown.stop>
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-sm">
            <Badge :theme="statusTheme" :label="statusLabel" />
            <span v-if="currentStep" class="text-ink-gray-5 font-mono">{{ currentStep }}</span>
            <span class="text-ink-gray-5">exit code: {{ job?.exit_code ?? '—' }}</span>
          </div>
          <Button variant="ghost" size="sm" @click="open = false">Close</Button>
        </div>
        <ErrorMessage v-if="error" :message="error" />
        <LogView
          :lines="logLines"
          :streaming="job?.status === 'running'"
          empty-text="Waiting for output…"
        />
      </div>

      <!-- Form -->
      <div v-else class="space-y-5" @pointerdown.stop>
        <!-- Project directory (== mef project name) -->
        <FormControl
          v-model="form.directory"
          label="Project directory"
          type="text"
          placeholder="my-project"
          :description="`Created under the mef root${form.directory ? ': ' + form.directory : ''}`"
          @keyup.enter="submit"
        />

        <!-- Profile + DB engine -->
        <div class="gap-3 grid grid-cols-1 sm:grid-cols-2">
          <FormControl v-model="form.profile" label="Profile" type="select" :options="profileOptions" />
          <FormControl
            v-model="form.db_engine"
            label="DB engine"
            type="select"
            :options="dbEngineOptions"
          />
        </div>
        <p v-if="!v16Compatible" class="bg-surface-amber-2 -mt-3 px-2.5 py-2 rounded text-ink-amber-8 text-p-sm flex items-center gap-1.5">
          <span class="size-3.5 lucide-triangle-alert shrink-0" />
          sqlite engines need frappe v16+ or develop.
        </p>

        <!-- Overlays -->
        <FormControl
          v-model="form.overlays"
          label="Overlays"
          type="text"
          placeholder="mcp, erpnext"
          description="Comma-separated overlay names (validated by the backend)."
        />

        <!-- Apps preset -->
        <FormControl
          v-model="form.apps_preset"
          label="Apps preset"
          type="text"
          placeholder="erpnext, payments"
          description="Comma-separated app names; passed through to apps.json."
        />

        <!-- Run setup -->
        <label class="flex items-center gap-2 cursor-pointer select-none">
          <Checkbox :model-value="form.new_run_setup" @update:model-value="form.new_run_setup = $event" />
          <span class="text-ink-gray-8 text-sm">
            Run <code class="font-mono text-ink-gray-7">mise r setup</code> after creation
          </span>
        </label>

        <!-- Run wizard (needs the bench+site that only setup creates) -->
        <label
          class="flex items-center gap-2 select-none"
          :class="form.new_run_setup ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'"
        >
          <Checkbox
            :model-value="form.new_run_wizard"
            :disabled="!form.new_run_setup"
            @update:model-value="form.new_run_wizard = $event"
          />
          <span class="text-ink-gray-8 text-sm">
            Run <code class="font-mono text-ink-gray-7">mise r wizard</code> after creation
          </span>
        </label>

        <!-- Wizard values (defaults match .config/mise/tasks/wizard's own fallbacks) -->
        <div v-if="form.new_run_setup && form.new_run_wizard" class="space-y-3 pl-6 border-l-2 border-outline-gray-2">
          <div class="gap-3 grid grid-cols-2">
            <FormControl v-model="form.wizard_language" label="Language" type="text" />
            <FormControl v-model="form.wizard_country" label="Country" type="text" />
            <FormControl v-model="form.wizard_timezone" label="Timezone" type="text" />
            <FormControl v-model="form.wizard_currency" label="Currency" type="text" />
          </div>
          <FormControl
            v-if="appsIncludeErpnext"
            v-model="form.wizard_company"
            label="Company"
            type="text"
            placeholder="Defaults to the project name"
          />
        </div>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="open = false">Cancel</Button>
          <Button variant="solid" :loading="submitting" :disabled="!canSubmit" @click="submit">
            Create project
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch, onBeforeUnmount } from 'vue'
import { Badge, Button, Checkbox, Dialog, ErrorMessage, FormControl, toast } from 'frappe-ui'
import LogView from '@/components/logs/LogView.vue'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'
import { processLine } from '@/utils/ansi'

const emit = defineEmits(['created', 'done'])
const open = defineModel()

// mef ships these profiles (config.<name>.toml under .config/mise/).
// sqlite* engines are gated to v16+/develop by the backend; mirror that here
// so the user gets immediate feedback before the round-trip.
const PROFILE_OPTIONS = ['v12', 'v13', 'v14', 'v15', 'v16', 'develop']
const SQLITE_ENGINES = ['sqlite', 'dolt_sqlite']
const DB_ENGINE_OPTIONS = ['mariadb', 'dolt', 'sqlite', 'dolt_sqlite']

const profileOptions = PROFILE_OPTIONS.map((value) => ({ label: value, value }))
const dbEngineOptions = DB_ENGINE_OPTIONS.map((value) => ({ label: value, value }))

// Mirrors .config/mise/tasks/wizard's own headless fallbacks — shown here so
// admins see (and can override) what will actually be used before it runs.
const WIZARD_DEFAULTS = {
  wizard_language: 'English',
  wizard_country: 'France',
  wizard_timezone: 'Europe/Paris',
  wizard_currency: 'EUR',
  wizard_company: '',
}

const form = reactive({
  directory: '',
  profile: 'v16',
  db_engine: 'mariadb',
  overlays: '',
  apps_preset: '',
  new_run_setup: true,
  new_run_wizard: false,
  ...WIZARD_DEFAULTS,
})

const appsIncludeErpnext = computed(() =>
  form.apps_preset.toLowerCase().split(',').map((s) => s.trim()).includes('erpnext'),
)

const error = ref('')
const submitting = ref(false)
const jobId = ref(null)
const job = ref(null)
let pollTimer = null

watch(
  () => form.new_run_setup,
  (enabled) => {
    if (!enabled) form.new_run_wizard = false
  },
)

const v16Compatible = computed(() => form.profile === 'v16' || form.profile === 'develop')
const canSubmit = computed(
  () => form.directory.trim().length > 0 && (v16Compatible.value || !SQLITE_ENGINES.includes(form.db_engine)),
)

const STATUS_META = {
  running: { label: 'Running', theme: 'blue' },
  success: { label: 'Success', theme: 'green' },
  failed: { label: 'Failed', theme: 'red' },
}
// Every mise task pipeline prints "[task] ERROR task failed" on the way out
// (see .config/mise/tasks/new). When polling loses the job (session expired
// mid-run, backend restarted) the exit_code is gone, but this marker in the
// last log we did receive is still a reliable signal that it failed.
const logIndicatesFailure = computed(() => /\]\s+ERROR task failed\s*$/m.test(job.value?.log || ''))
const statusLabel = computed(() => {
  if (STATUS_META[job.value?.status]) return STATUS_META[job.value.status].label
  if (logIndicatesFailure.value) return 'Failed (from log)'
  return error.value ? 'Unknown' : 'Queued'
})
const statusTheme = computed(() => {
  if (STATUS_META[job.value?.status]) return STATUS_META[job.value.status].theme
  return logIndicatesFailure.value ? 'red' : 'gray'
})

const logLines = computed(() => {
  const text = (job.value?.log || '').replace(/\n+$/, '')
  return text ? text.split('\n').map(processLine) : []
})

// mise tasks consistently prefix their output with "[task:name] ...";
// surfacing the last one gives a rough "current step" indicator for free.
const STEP_RE = /^\[([\w:.-]+)\]/gm
const currentStep = computed(() => {
  const text = job.value?.log || ''
  STEP_RE.lastIndex = 0
  let last = ''
  let match
  while ((match = STEP_RE.exec(text))) last = match[1]
  return last
})

watch(open, (visible) => {
  if (!visible) {
    stopPolling()
    // Reset on close so reopening starts fresh.
    Object.assign(form, {
      directory: '',
      profile: 'v16',
      db_engine: 'mariadb',
      overlays: '',
      apps_preset: '',
      new_run_setup: true,
      new_run_wizard: false,
      ...WIZARD_DEFAULTS,
    })
    error.value = ''
    submitting.value = false
    jobId.value = null
    job.value = null
    return
  }
})

onBeforeUnmount(stopPolling)

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

// mise r new takes minutes (clone+install). Poll gently: the log only advances
// when the subprocess flushes, so 1.5s is responsive without hammering.
const POLL_INTERVAL_MS = 1500
// A poll error (session expired mid-job, backend blip) gets a few retries in
// case it's transient, then surfaces instead of silently going quiet forever.
const MAX_POLL_ERRORS = 5
let pollErrorCount = 0

async function pollJob() {
  if (!jobId.value) return
  try {
    const detail = await mefApi.getJob(jobId.value)
    if (detail?.error) {
      pollErrorCount += 1
      if (pollErrorCount <= MAX_POLL_ERRORS) {
        pollTimer = setTimeout(pollJob, POLL_INTERVAL_MS)
        return
      }
      stopPolling()
      error.value = logIndicatesFailure.value
        ? `${apiErrorMessage(detail, 'Lost track of the job.')} The log shows it already failed — see below.`
        : `${apiErrorMessage(detail, 'Lost track of the job.')} Reload the page — the operation may still be running in the background.`
      return
    }
    pollErrorCount = 0
    job.value = detail
    if (detail.status === 'running') {
      pollTimer = setTimeout(pollJob, POLL_INTERVAL_MS)
    } else {
      stopPolling()
      if (detail.status === 'success') {
        toast.success(`Project created: ${form.directory}`)
        emit('created', form.directory)
        emit('done', 'success', form.directory)
        open.value = false
      } else {
        error.value = `mise r new exited with code ${detail.exit_code}. See log below.`
        emit('done', 'failed', form.directory)
      }
    }
  } catch (caught) {
    // Network blip: retry on the next tick rather than tearing down the stream.
    pollTimer = setTimeout(pollJob, POLL_INTERVAL_MS)
  }
}

function buildPayload() {
  const payload = {
    directory: form.directory.trim(),
    profile: form.profile,
    db_engine: form.db_engine,
  }
  const overlays = form.overlays.split(',').map((s) => s.trim()).filter(Boolean)
  if (overlays.length) payload.overlays = overlays
  const apps = form.apps_preset.split(',').map((s) => s.trim()).filter(Boolean)
  if (apps.length) payload.apps_preset = apps.join(',')
  if (form.new_run_setup) {
    payload.new_run_setup = 1
    if (form.new_run_wizard) {
      payload.new_run_wizard = 1
      for (const field of Object.keys(WIZARD_DEFAULTS)) {
        const value = form[field].trim()
        if (value) payload[field] = value
      }
    }
  }
  return payload
}

async function submit() {
  if (!canSubmit.value) return
  error.value = ''
  submitting.value = true
  try {
    const result = await mefApi.createProject(buildPayload())
    if (!result.job_id) {
      error.value = apiErrorMessage(result, 'Could not start project creation.')
      return
    }
    jobId.value = result.job_id
    pollErrorCount = 0
    // Wait for backend to create log file and start subprocess before first poll
    setTimeout(pollJob, 500)
  } catch (caught) {
    error.value = caught.message || 'Could not start project creation.'
  } finally {
    submitting.value = false
  }
}
</script>
