/**
 * 上游数据源标准化展示名 —— 全站唯一事实源。
 *
 * 命名规则: 「厂商品牌 + 空格 + 产品/模块」。英文产品名 (MooTDX2 / THSDK /
 * OMNIDATA) 保留官方拼写; fuyao 模块名 (a-share 等) 按上游实际
 * endpoint 保留。未收录的上游回退原始 id。
 */
export const UPSTREAM_DISPLAY_NAME: Record<string, string> = {
  mootdx2: '通达信 MooTDX2',
  tdx_quant: '通达信量化终端',
  fuyao_ashare: '同花顺 Fuyao a-share',
  fuyao_index: '同花顺 Fuyao a-share-index',
  fuyao_meta: '同花顺 Fuyao meta',
  fuyao_fund: '同花顺 Fuyao fund',
  omni: 'OMNIDATA 本地库',
  thsdk: 'THSDK 本地终端',
}

/** 上游展示名; 未收录回退 id 本身。 */
export function upstreamDisplayName(id: string): string {
  return UPSTREAM_DISPLAY_NAME[id] ?? id
}
