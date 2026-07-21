<template>
  <Dialog v-model="open" title="Run setup wizard" size="sm">
    <template #default>
      <div class="space-y-3">
        <p class="text-ink-gray-6 text-p-sm">
          Runs <code class="font-mono text-ink-gray-7">mise r wizard</code> headlessly against
          {{ siteName }}. Defaults match the mise task's own fallbacks.
        </p>
        <div class="gap-3 grid grid-cols-2">
          <FormControl v-model="form.wizard_language" label="Language" type="select" :options="languageOptions" />
          <FormControl v-model="form.wizard_country" label="Country" type="select" :options="countryOptions" />
          <FormControl v-model="form.wizard_timezone" label="Timezone" type="select" :options="timezoneOptions" />
          <FormControl v-model="form.wizard_currency" label="Currency" type="select" :options="currencyOptions" />
        </div>
        <FormControl
          v-if="hasErpnext"
          v-model="form.wizard_company"
          label="Company"
          type="text"
          placeholder="Defaults to the site name"
        />
        <div class="flex justify-end gap-2">
          <Button variant="subtle" :disabled="loading" @click="open = false">Cancel</Button>
          <Button variant="solid" :loading="loading" @click="run">Run wizard</Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, FormControl, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'
import { sitesApi } from '@/api/sites'

const props = defineProps({
  siteName: { type: String, required: true },
  hasErpnext: { type: Boolean, default: false },
})
const emit = defineEmits(['completed'])
const open = defineModel({ type: Boolean, default: false })

const loading = ref(false)
const WIZARD_POLL_MS = 1500

// Mirrors .config/mise/tasks/wizard's own headless fallbacks.
const form = reactive({
  wizard_language: 'English',
  wizard_country: 'France',
  wizard_timezone: 'Europe/Paris',
  wizard_currency: 'EUR',
  wizard_company: '',
})

// Sourced from the bench itself (frappe.geo.country_info + Language/Currency
// doctypes) rather than hardcoded — see admin/backend/api/v1/sites/wizard.py.
// Rarely changes, backend caches it for the daemon's lifetime; fetch once
// here too so re-opening the dialog doesn't re-request it.
const toStringOptions = (values) => values.map((value) => ({ label: value, value }))
const options = ref(null)
const optionsLoading = ref(false)

const languageOptions = computed(() => toStringOptions(options.value?.languages ?? [form.wizard_language]))
const countryOptions = computed(() => toStringOptions(options.value?.countries ?? [form.wizard_country]))
const currencyOptions = computed(() => toStringOptions(options.value?.currencies ?? [form.wizard_currency]))
const timezoneOptions = computed(() => {
  const countryTimezones = options.value?.country_timezones?.[form.wizard_country]
  return toStringOptions(countryTimezones?.length ? countryTimezones : (options.value?.all_timezones ?? [form.wizard_timezone]))
})

async function loadOptions() {
  if (options.value || optionsLoading.value) return
  optionsLoading.value = true
  try {
    options.value = await sitesApi.getWizardOptions(props.siteName)
  } catch (caught) {
    toast.error(caught.message || 'Could not load wizard options; falling back to free text.')
  } finally {
    optionsLoading.value = false
  }
}

watch(open, (visible) => {
  if (visible) loadOptions()
})

// Re-derive timezone/currency defaults when the country changes, once
// options are loaded — same cascading behavior as the browser wizard.
watch(
  () => form.wizard_country,
  (country) => {
    if (!options.value) return
    const timezones = options.value.country_timezones[country]
    if (timezones?.length && !timezones.includes(form.wizard_timezone)) {
      form.wizard_timezone = timezones[0]
    }
    const currency = options.value.country_currency[country]
    if (currency) form.wizard_currency = currency
  },
)

async function run() {
  loading.value = true
  try {
    const payload = {}
    for (const [field, value] of Object.entries(form)) {
      const trimmed = value.trim()
      if (trimmed) payload[field] = trimmed
    }
    const result = await sitesApi.runWizard(props.siteName, payload)
    if (!result.job_id) {
      toast.error(apiErrorMessage(result, 'Could not start the setup wizard.'))
      loading.value = false
      return
    }
    pollJob(result.job_id)
  } catch (caught) {
    toast.error(caught.message || 'Could not start the setup wizard.')
    loading.value = false
  }
}

async function pollJob(jobId) {
  let detail
  try {
    detail = await mefApi.getJob(jobId)
  } catch {
    setTimeout(() => pollJob(jobId), WIZARD_POLL_MS)
    return
  }
  if (detail?.error) {
    toast.error(apiErrorMessage(detail, 'Lost track of the wizard job.'))
    loading.value = false
    return
  }
  if (detail.status === 'running') {
    setTimeout(() => pollJob(jobId), WIZARD_POLL_MS)
    return
  }
  loading.value = false
  if (detail.status === 'success') {
    toast.success(`Wizard completed for ${props.siteName}`)
    open.value = false
    emit('completed')
  } else {
    toast.error(`Wizard failed (exit code ${detail.exit_code}). Check pilot logs.`)
  }
}
</script>
