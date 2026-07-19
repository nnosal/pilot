<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h4 class="text-base font-semibold text-ink-gray-9">App Validation</h4>
        <p class="text-sm text-ink-gray-5 mt-1">
          Skip dependency validation when installing apps. Use this for apps with known compatibility issues (e.g. builder).
        </p>
      </div>
      <Switch v-model="apps_skip_validations" @change="updateConfig" />
    </div>

    <div class="flex items-center justify-between">
      <div>
        <h4 class="text-base font-semibold text-ink-gray-9">Skip Update Checks</h4>
        <p class="text-sm text-ink-gray-5 mt-1">
          Do not check app repositories for updates and hide the "Update available" button.
        </p>
      </div>
      <Switch v-model="apps_skip_update_check" @change="updateConfig" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Switch } from 'frappe-ui'

const apps_skip_validations = ref(true)
const apps_skip_update_check = ref(true)

onMounted(async () => {
  const config = await getConfig()
  apps_skip_validations.value = config.bench?.apps_skip_validations ?? true
  apps_skip_update_check.value = config.bench?.apps_skip_update_check ?? true
})

async function getConfig() {
  return await fetch('/api/v1/settings').then(r => r.json())
}

async function updateConfig() {
  await fetch('/api/v1/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bench: {
        apps_skip_validations: apps_skip_validations.value,
        apps_skip_update_check: apps_skip_update_check.value
      }
    })
  })
}
</script>
