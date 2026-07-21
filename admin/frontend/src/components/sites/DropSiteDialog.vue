<template>
  <Dialog v-model="open" :options="{ title: 'Delete this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This permanently deletes <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span>
        and everything on it. Backups are kept for 30 days.
      </p>
      <TextInput v-model="confirmName" :placeholder="siteName" class="mt-4 w-full">
        <template #label>
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
        </template>
      </TextInput>
      <ErrorMessage v-if="error" :message="error" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="open = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="dropping" :disabled="confirmName !== siteName" @click="confirm">
          Delete site
        </Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, TextInput } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

const props = defineProps({ siteName: { type: String, required: true } })
const open = defineModel({ type: Boolean, default: false })

const router = useRouter()
const confirmName = ref('')
const dropping = ref(false)
const error = ref('')

watch(open, (visible) => {
  if (visible) {
    confirmName.value = ''
    error.value = ''
  }
})

async function confirm() {
  dropping.value = true
  error.value = ''
  try {
    const data = await sitesApi.drop(props.siteName)
    if (data.task_id) {
      open.value = false
      openTaskDetailPage(router, data.task_id)
    } else {
      error.value = apiErrorMessage(data, 'Failed to drop site.')
      dropping.value = false
    }
  } catch (e) {
    error.value = e.message || 'Failed to drop site.'
    dropping.value = false
  }
}
</script>
