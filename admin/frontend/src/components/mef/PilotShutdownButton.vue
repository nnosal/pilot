<template>
  <Button
    variant="ghost"
    size="sm"
    theme="red"
    icon="lucide-x"
    title="Close this pilot instance"
    @click="show = true"
  />

  <Dialog v-model="show" :options="{ title: 'Close pilot instance', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm leading-relaxed">
        Stop this pilot admin? This runs
        <code class="font-mono text-ink-gray-7">mise r pilot:down</code> and ends this
        session. The bench and its sites keep running.
      </p>
      <ErrorMessage v-if="error" :message="error" class="mt-3" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="subtle" @click="show = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="loading" @click="confirm">
          Close instance
        </Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref } from 'vue'
import { Button, Dialog, ErrorMessage, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'

const show = ref(false)
const loading = ref(false)
const error = ref('')

async function confirm() {
  loading.value = true
  error.value = ''
  try {
    const result = await mefApi.selfPilotDown()
    if (result?.error) {
      error.value = apiErrorMessage(result)
      return
    }
    show.value = false
    toast.success('Closing pilot instance…')
  } catch (caught) {
    error.value = caught.message || 'Could not close pilot instance.'
  } finally {
    loading.value = false
  }
}
</script>
