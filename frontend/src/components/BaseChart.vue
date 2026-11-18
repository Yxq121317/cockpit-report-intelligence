<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'

const props = withDefaults(
  defineProps<{
    option: EChartsOption
    height?: string
    loading?: boolean
    empty?: boolean
    emptyText?: string
  }>(),
  {
    height: '280px',
    loading: false,
    empty: false,
    emptyText: '暂无可展示的数据',
  },
)

const container = ref<HTMLDivElement | null>(null)
const chart = shallowRef<ECharts | null>(null)
let observer: ResizeObserver | null = null

function applyOption(): void {
  if (!chart.value || props.empty) return
  chart.value.setOption(props.option, true)
}

function syncLoading(): void {
  if (!chart.value) return
  if (props.loading) {
    chart.value.showLoading('default', {
      text: '加载中',
      color: '#2f6bff',
      textColor: '#7b8aa3',
      maskColor: 'rgba(255, 255, 255, 0.72)',
      spinnerRadius: 9,
      lineWidth: 2,
      fontSize: 11,
    })
  } else {
    chart.value.hideLoading()
  }
}

onMounted(() => {
  if (!container.value) return
  chart.value = echarts.init(container.value)
  applyOption()
  syncLoading()
  observer = new ResizeObserver(() => chart.value?.resize())
  observer.observe(container.value)
})

watch(
  () => props.option,
  () => applyOption(),
  { deep: true },
)

watch(
  () => props.empty,
  (isEmpty) => {
    if (!isEmpty) applyOption()
  },
)

watch(
  () => props.loading,
  () => syncLoading(),
)

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart.value?.dispose()
  chart.value = null
})
</script>

<template>
  <div class="base-chart" :style="{ height }">
    <div ref="container" class="chart-canvas"></div>
    <div v-if="empty && !loading" class="chart-empty">
      <span class="empty-dot" aria-hidden="true"></span>
      <span>{{ emptyText }}</span>
    </div>
  </div>
</template>

<style scoped>
.base-chart {
  position: relative;
  width: 100%;
}

.chart-canvas {
  width: 100%;
  height: 100%;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 9px;
  font-size: 12px;
  color: #8b9ab2;
  background: rgba(255, 255, 255, 0.68);
}

.empty-dot {
  width: 30px;
  height: 3px;
  border-radius: 999px;
  background: #dfe8f5;
}
</style>
