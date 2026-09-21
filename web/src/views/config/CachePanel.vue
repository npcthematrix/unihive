<template>
  <div class="cache-panel">
    <div class="cfg-banner cache-banner">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span v-if="isDbMode">
        此处为当前生效的 <code>cache</code> 全局配置 — 存于配置库
        <code>data/config.db</code> 的 <code>config_section</code>
        (空库由出厂 YAML 模板 seed; 与"上游数据源状态"标签页的运行时缓存是不同概念)。
        只读展示, <strong>修改后需重启网关</strong>生效。
      </span>
      <span v-else>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 逃生模式启动, 此处为
        出厂 YAML (<code>config/upstreams.yaml</code> /
        <code>config/config.yaml</code>) 中的 <code>cache</code> 配置,
        只读展示, 编辑文件后重启网关生效。
      </span>
    </div>

    <!-- 缓存规则说明（默认折叠：数据表为主体，规则按需展开） -->
    <details class="cfg-section">
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          缓存有效期是怎样决定的（两条动态规则）
        </span>
      </summary>
      <div class="cfg-section-body cache-rules">
        <p class="rule-line">
          <span class="rule-tag tag-frozen">规则 1 · 历史日期 → 近似永久</span>
          带日期参数的特色数据工具（涨停/跌停/炸板池、龙虎榜、热门股历史等），
          当传入的日期<strong>严格早于今天</strong>时，该历史交易日的数据在收盘后
          永久冻结，自动套用 <code>historical_immutable</code> 档（约 10 年的极大
          有限值；系统不写真正的永久缓存 <code>expires_at=NULL</code>）。日期留空、
          当天或未来时不触发。
        </p>
        <p class="rule-line">
          <span class="rule-tag tag-session">规则 2 · 短档（≤1 小时）→ 交易时段感知</span>
          配置秒数 ≤ 3600 的档（当前为 <code>special_data</code> /
          <code>index_data</code>）只在<strong>交易时段</strong>按该短档缓存；
          非交易时段数据不再变化，自动把缓存延长到「下一次数据会变化的边界」，
          不按分钟空转重拉。其余 &gt;1 小时的档不受此规则影响，按原值全天生效。
        </p>
        <div class="boundary-grid">
          <div class="boundary-head">上海时段</div>
          <div class="boundary-head">生效的缓存时长</div>
          <div class="b-cell">周末（周六 / 周日）</div>
          <div class="b-cell">缓存到次日零点（跨周末逐日续期）</div>
          <div class="b-cell">00:00 – 9:30 开盘前</div>
          <div class="b-cell">缓存到 9:30 开盘</div>
          <div class="b-cell">9:30 – 11:30 上午盘</div>
          <div class="b-cell">该短档（分钟级）</div>
          <div class="b-cell">11:30 – 13:00 午间休市</div>
          <div class="b-cell">缓存到 13:00 午后开盘</div>
          <div class="b-cell">13:00 – 15:00 下午盘</div>
          <div class="b-cell">该短档（分钟级）</div>
          <div class="b-cell">15:00 – 24:00 收盘后</div>
          <div class="b-cell">缓存到次日零点</div>
        </div>
        <p class="rule-line">
          <span class="rule-tag tag-exempt">规则 3 · 本地终端不缓存</span>
          通达信量化终端（<code>tdx_quant</code>）与 THSDK 本地终端（<code>thsdk</code>）
          这两个上游<strong>不参与任何缓存</strong>（不读、不单飞、不写）——本地取数足够快，
          且要求实时。仅当某工具的整条路由链都是这两个源时才豁免；链上同时含
          fuyao 等远程源的，远程降级响应仍会缓存。
        </p>
        <p class="rule-note">
          说明：时段按周一至周五 + 固定钟点判断，<strong>未区分法定节假日</strong>
          （节假日若落在工作日，当天按交易时段短档处理，代价仅是多几次无害重拉）；
          缓存键对全部参数做哈希，不同日期 / 不同标的各自独立，互不影响。
        </p>
      </div>
    </details>

    <!-- 基本配置 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          缓存基本配置
        </span>
      </summary>
      <div class="cfg-section-body">
        <div class="cfg-kv-grid">
          <div class="cfg-kv-key">启用</div>
          <div class="cfg-kv-val">
            <span
              class="cfg-switch"
              :class="enabled ? 'on' : 'off'"
            >{{ enabled ? '启用' : '停用' }}</span>
          </div>
          <div class="cfg-kv-key">DB 路径</div>
          <div class="cfg-kv-val">
            <code v-if="dbPath">{{ dbPath }}</code>
            <span v-else class="muted">-</span>
          </div>
          <div class="cfg-kv-key">TTL 配置</div>
          <div class="cfg-kv-val">
            <span class="num">{{ ttlEntries.length }}</span> 条 ·
            最短 <strong>{{ ttlMinText }}</strong> ·
            最长 <strong>{{ ttlMaxText }}</strong>
          </div>
        </div>
      </div>
    </details>

    <!-- TTL 配置 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          {{ ttlEntries.length }} 条 TTL 配置
          <span class="cfg-section-sub">— 按 TTL 升序排列</span>
        </span>
      </summary>
      <div class="cfg-section-body">
        <el-table :data="ttlEntries" class="cfg-table cache-ttl-table" :empty-text="ttlEmptyText">
          <el-table-column label="键" min-width="190">
            <template #default="{ row }">
              <span class="tool-name">{{ row.key }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">
              <span class="ttl-badge" :class="badgeClass(row)">{{ badgeText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="340">
            <template #default="{ row }">
              <span class="ttl-hint">{{ TTL_KEY_HINTS[row.key] ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="秒" width="100" align="right" header-align="right">
            <template #default="{ row }"><span class="num">{{ row.seconds }}</span></template>
          </el-table-column>
          <el-table-column label="人类可读" width="120">
            <template #default="{ row }">
              <span class="ttl-human">{{ humanizeDuration(row.seconds) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { GatewayConfig } from '@/api/config'
import { humanizeDuration } from './format'

interface Props {
  config: GatewayConfig | null
  /** 配置事实源: db (默认, 配置库) / yaml (逃生模式)。决定 banner 措辞。 */
  source?: 'db' | 'yaml'
}

const props = withDefaults(defineProps<Props>(), { source: 'db' })

const isDbMode = computed(() => props.source === 'db')

const cache = computed(() => props.config?.cache || {})

const enabled = computed(() => Boolean(cache.value.enabled))
const dbPath = computed(() => (cache.value.db_path as string | undefined) || '')

interface TtlRow {
  key: string
  seconds: number
}

/**
 * TTL 档 → 含义 / 数据可变性 / 代表工具 / 取该时长的依据。
 * 与 config 的 cache.ttl 各键一一对应。
 */
const TTL_KEY_HINTS: Record<string, string> = {
  index_data:
    '指数行情快照（如 a_share_index_prices_snapshot：上证 / 深证 / 行业指数点位）。' +
    '交易时段实时变动走 60s 短档削峰；非交易时段不再变动，缓存到下一变化边界。',
  special_data:
    '当日特色数据：涨停 / 跌停 / 炸板池、龙虎榜、异动、热门股榜单等。' +
    '日期留空或当天时，交易时段按 1 分钟刷新、非交易时段缓存到下一变化边界；' +
    '若传入严格早于今天的日期，则改走 historical_immutable 近似永久。',
  historical:
    '历史行情与复权数据：日 / 分钟 K 线、复权因子等。收盘后当日数据不再变化，' +
    '缓存 1 天；不用更长，是因为前复权价会在未来发生除权除息时整体平移。',
  auction_snapshot:
    '集合竞价快照（a_share_auction_snapshot）。无日期参数、恒指当天，' +
    '9:25 竞价成交后数值在整个交易日固定不变，故缓存 1 天，次日自然换新。',
  fund_history:
    'ETF / LOF 历史行情、基金历史业绩走势指标等。历史序列随交易日追加，' +
    '缓存 1 天即可在当日复用。',
  valuation:
    'A 股估值快照（PE / PB / 股息率等，a_share_valuations_snapshot）。' +
    '估值随价格变动，但使用上无需敏感，缓存 1 天。',
  fundamentals:
    '财务数据：资产负债表 / 利润表 / 现金流量表、主要财务指标。' +
    '数值随定期报告披露、平时不变，缓存 7 天；财报密集披露期可手动缩短。',
  ticker_list:
    '证券代码目录、指数 / 板块成分股、全量 ticker 列表（meta_tickers_list 等）。' +
    '仅在新股上市、退市、指数调样时变化，缓存 7 天。',
  fund_data:
    '基金基础档案：基金资料、业绩表现、基金经理、规模等。' +
    '信息低频变动，缓存 7 天。',
  fund_holdings:
    '基金持仓 / 行业配置 / 资产配置（股票 / 债券持仓、报告日等）。' +
    '持仓随季报披露、约每季度更新一次，缓存 90 天与之匹配。',
  workday:
    '交易日历（a_share_calendar_trading_days）。交易所提前发布、常年不变，' +
    '缓存 180 天。',
  historical_immutable:
    '历史交易日冻结快照档，不由工具直接配置：special_data 类工具传入严格早于' +
    '今天的日期时自动套用。历史数据收盘后永久不变，取约 10 年的极大有限值≈永久' +
    '（系统禁止 expires_at=NULL 的真永久缓存）。',
}

/**
 * 短档阈值：≤1 小时的档按交易时段感知（仅用于徽标显示）。
 * 必须与后端 cache_strategy.SESSION_AWARE_TTL_MAX 保持一致，后端调整时此处同步。
 */
const SESSION_AWARE_MAX = 3600
/** 自动冻结档（历史日期提升）。 */
const FROZEN_KEY = 'historical_immutable'
/** special_data 既可时段感知，又能在历史日期下提升冻结。 */
const DUAL_KEY = 'special_data'

function badgeClass(row: TtlRow): string {
  if (row.key === FROZEN_KEY) return 'badge-frozen'
  if (row.seconds <= SESSION_AWARE_MAX) return 'badge-session'
  return 'badge-regular'
}

function badgeText(row: TtlRow): string {
  if (row.key === FROZEN_KEY) return '冻结档 · 自动'
  if (row.key === DUAL_KEY) return '时段档 + 冻结'
  if (row.seconds <= SESSION_AWARE_MAX) return '时段档'
  return '常规档'
}

const ttlEntries = computed<TtlRow[]>(() => {
  const ttl = cache.value.ttl || {}
  return Object.entries(ttl)
    .sort((a, b) => Number(a[1]) - Number(b[1]))
    .map(([key, seconds]) => ({ key, seconds: Number(seconds) }))
})

const ttlMinText = computed(() => {
  if (!ttlEntries.value.length) return '-'
  return humanizeDuration(ttlEntries.value[0].seconds)
})

const ttlMaxText = computed(() => {
  if (!ttlEntries.value.length) return '-'
  return humanizeDuration(ttlEntries.value[ttlEntries.value.length - 1].seconds)
})

const ttlEmptyText = computed(() => (ttlEntries.value.length ? '' : '无 TTL 配置'))
</script>

<style scoped>
.cache-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cache-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
}

.cache-banner code {
  background: var(--cfg-bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

/* 规则说明卡片 */
.cache-rules {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rule-line {
  margin: 0;
  line-height: 1.7;
}

.rule-tag {
  display: inline-block;
  padding: 2px 8px;
  margin-right: 8px;
  border-radius: 4px;
  font-size: 0.78rem;
  font-weight: 600;
  white-space: nowrap;
}

.tag-frozen {
  background: rgba(64, 158, 255, 0.14);
  color: #2563eb;
}

.tag-session {
  background: rgba(230, 162, 60, 0.16);
  color: #b45309;
}

.tag-exempt {
  background: rgba(148, 163, 184, 0.18);
  color: #475569;
}

.rule-note {
  margin: 0;
  font-size: 0.8rem;
  color: var(--cfg-text-muted, #888);
  line-height: 1.6;
}

/* 时段边界表 */
.boundary-grid {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.4fr);
  border: 1px solid var(--cfg-border, #e2e2e6);
  border-radius: 6px;
  overflow: hidden;
  font-size: 0.82rem;
}

.boundary-head {
  background: var(--cfg-bg-soft, #f5f6f8);
  font-weight: 600;
  padding: 7px 12px;
}

.b-cell {
  padding: 6px 12px;
  border-top: 1px solid var(--cfg-border, #ececf0);
}

/* 类型徽标 */
.ttl-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 0.74rem;
  font-weight: 600;
  white-space: nowrap;
}

.badge-session {
  background: rgba(230, 162, 60, 0.16);
  color: #b45309;
}

.badge-frozen {
  background: rgba(64, 158, 255, 0.14);
  color: #2563eb;
}

.badge-regular {
  background: var(--cfg-bg-soft, #f0f1f4);
  color: var(--cfg-text-muted, #666);
}

.ttl-hint {
  line-height: 1.6;
}

/* 秒列与人类可读列都含数字, 等宽数字避免排序后上下行跳动 */
.cache-ttl-table :deep(.num),
.cache-ttl-table :deep(.ttl-human) {
  font-variant-numeric: tabular-nums;
}
</style>
