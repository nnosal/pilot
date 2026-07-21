<template>
  <Dialog v-model="open" :options="{ title: 'Migrate this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This runs <span class="font-mono text-ink-gray-8">bench migrate</span> on
        <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> without taking a backup first.
        If the migration fails partway, you'll need an existing backup to recover.
      </p>
      <ErrorMessage v-if="error" :message="error" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="open = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="migrating" @click="confirm">Migrate</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

const props = defineProps({ siteName: { type: String, required: true } })
const open = defineModel({ type: Boolean, default: false })

const router = useRouter()
const migrating = ref(false)
const error = ref('')

async function confirm() {
  migrating.value = true
  error.value = ''
  try {
    const data = await sitesApi.migrate(props.siteName)
    if (data.task_id) {
      open.value = false
      openTaskDetailPage(router, data.task_id)
    } else error.value = apiErrorMessage(data, 'Failed to migrate site.')
  } catch (e) {
    error.value = e.message || 'Failed to migrate site.'
  } finally {
    migrating.value = false
  }
}
</script>
