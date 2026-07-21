<template>
  <div>
    <p class="font-semibold text-ink-gray-8 text-base">Danger</p>
    <div class="mt-1">
      <div v-for="d in DangerActions" :key="d.key"
        class="flex justify-between items-start gap-x-2.5 py-4 border-b last:border-b-0 border-outline-alpha-gray-1">
        <div class="flex flex-col min-w-0">
          <p class="font-medium text-ink-gray-8 text-sm leading-normal">{{ d.label }}</p>
          <div class="mt-0.5">
            <p class="text-ink-gray-6 text-sm line-clamp-2 sm:line-clamp-none">{{ d.description }}</p>
          </div>
        </div>
        <Button size="sm" theme="red" class="ml-4 shrink-0" @click="d.action">{{ d.buttonLabel || d.label }}</Button>
      </div>
    </div>
  </div>

  <MigrateSiteDialog v-model="showMigrate" :site-name="siteName" />

  <!-- Reset dialog -->
  <Dialog v-model="showReset" :options="{ title: 'Reset this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This reinstalls <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> and wipes
        all its data. Apps stay installed.
      </p>
      <TextInput v-model="confirmName" :placeholder="siteName" class="mt-4 w-full">
        <template #label>
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
        </template>
      </TextInput>
      <ErrorMessage v-if="resetError" :message="resetError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="showReset = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="resetting" :disabled="confirmName !== siteName"
          @click="confirmReset">
          Reset site
        </Button>
      </div>
    </template>
  </Dialog>

  <DropSiteDialog v-model="showDrop" :site-name="siteName" />
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, TextInput } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'
import MigrateSiteDialog from '../MigrateSiteDialog.vue'
import DropSiteDialog from '../DropSiteDialog.vue'

const props = defineProps({ siteName: { type: String, required: true } })

const router = useRouter()

const showMigrate = ref(false)

const DangerActions = [
  {
    key: 'migrate',
    label: 'Migrate site',
    buttonLabel: 'Migrate',
    description: 'Runs bench migrate for this site without taking a backup first.',
    action: () => { showMigrate.value = true },
  },
  {
    key: 'reset',
    label: 'Reset site',
    description: 'Wipes the database back to a fresh install. Apps stay; all your data is removed.',
    action: () => { confirmName.value = ''; resetError.value = ''; showReset.value = true },
  },
  {
    key: 'drop',
    label: 'Drop site',
    description: `Permanently deletes ${props.siteName} and all its data.`,
    action: () => { showDrop.value = true },
  },
]

const confirmName = ref('')

const showReset = ref(false)
const resetting = ref(false)
const resetError = ref('')

async function confirmReset() {
  resetting.value = true
  resetError.value = ''
  try {
    const data = await sitesApi.reinstall(props.siteName)
    if (data.task_id) {
      showReset.value = false
      openTaskDetailPage(router, data.task_id)
    } else resetError.value = apiErrorMessage(data, 'Failed to reset site.')
  } catch (e) {
    resetError.value = e.message || 'Failed to reset site.'
  } finally {
    resetting.value = false
  }
}

const showDrop = ref(false)
</script>
