<template>
  <el-table :data="rows" class="cfg-table" size="small">
    <el-table-column label="名称" min-width="200">
      <template #default="{ row }">
        <span class="tool-name">{{ row.name }}</span>
      </template>
    </el-table-column>
    <el-table-column label="处置" width="130">
      <template #default="{ row }">
        <el-tag
          :type="actionTagType(row.action)"
          size="small"
          disable-transitions
        >{{ actionLabel(row.action) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="变更字段" min-width="180">
      <template #default="{ row }">
        <span class="uc-ledger-fields">{{ row.fields.join('、') || '—' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="冲突字段" min-width="180">
      <template #default="{ row }">
        <span
          class="uc-ledger-fields"
          :class="{ 'uc-ledger-conflict': row.conflicts.length }"
        >{{ row.conflicts.join('、') || '—' }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { UpgradeEntityChange } from '@/api/config'
import { actionLabel, actionTagType } from './updateCenter'

defineProps<{
  rows: UpgradeEntityChange[]
}>()
</script>

<style scoped>
.uc-ledger-fields {
  color: var(--el-text-color-secondary);
  word-break: break-word;
}

.uc-ledger-conflict {
  color: var(--el-color-danger);
}
</style>
