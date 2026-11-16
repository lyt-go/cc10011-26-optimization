import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/segmentprofile'
const columns = [
  "画像编号",
  "分群名称",
  "筛选规则",
  "画像维度",
  "对比基准",
  "更新周期",
  "覆盖对象数",
  "画像快照"
]
const actions = [
  "提交画像评审",
  "发布画像",
  "下线画像"
]

type Row = Record<string, string | number | null>
type SharedDataset = {
  id: number
  批次键: string
  模型编号: string
  事件名称: string
  状态: string
  快照ID: string
  规则版本: string
  样本充足组数: number
  样本不足组数: number
  生成时间: string
}
type SharedRow = {
  分组值: string
  触达用户数: number
  归因转化用户数: number
  转化率: number
  置信区间下限: number
  置信区间上限: number
  样本判定: string
}
type SharedDetail = SharedDataset & { rows?: SharedRow[]; 置信水平: number }

function formatPct(value: number) {
  return `${(value * 100).toFixed(2)}%`
}

export default function SegmentprofileView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const [shared, setShared] = useState<SharedDataset[]>([])
  const [sharedDetail, setSharedDetail] = useState<SharedDetail | null>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('画像定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分群画像列表读取失败')
    }
  }, [filters])

  const reloadShared = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/attribution`)
      if (!response.ok) {
        throw new Error('同源事件归因结果数据集读取失败')
      }
      const payload = await response.json()
      setShared(payload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '同源事件归因结果数据集读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
    void reloadShared()
  }, [reload, reloadShared])

  async function openShared(dataset: SharedDataset) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/attribution/${dataset.id}`)
      if (!response.ok) {
        throw new Error('同源事件归因结果数据集明细读取失败')
      }
      setSharedDetail(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '同源事件归因结果数据集明细读取失败')
    }
  }

  const stats = useMemo(() => ([
    { label: '画像定义总数', value: total },
    { label: '已发布画像', value: rows.filter((row) => row.status === '已发布').length },
    { label: '同源归因数据集', value: shared.length },
  ]), [total, rows, shared])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('分群画像动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分群画像操作失败')
    }
  }

  return (
    <section className="page" data-module="segmentprofile">
      <header className="page-head">
        <div>
          <h2>分群画像管理</h2>
          <p className="page-desc">管理用户分群的画像定义和对比分析，展示各分群的特征差异。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('画像定义登记入口尚未接入审批流')}>
            登记画像定义
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分群画像清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无分群画像数据，可先登记画像定义</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条分群画像记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      <section className="panel">
        <header className="panel-head">
          <h3>同源事件归因结果数据集</h3>
          <button className="btn" type="button" onClick={() => void reloadShared()}>刷新列表</button>
        </header>
        <p className="panel-desc">
          分群画像与事件分析读取同一份事件归因结果数据集（只读），数据由事件分析的归因计算产出，
          分群维度上的转化表现与置信区间口径一致。
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>批次键</th>
              <th>事件名称</th>
              <th>归因快照</th>
              <th>规则版本</th>
              <th>状态</th>
              <th>样本充足/不足</th>
              <th>生成时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {shared.map((dataset) => (
              <tr key={dataset.id}>
                <td>{dataset.批次键}</td>
                <td>{dataset.事件名称}</td>
                <td>{dataset.快照ID}</td>
                <td>{dataset.规则版本}</td>
                <td><span className="badge">{dataset.状态}</span></td>
                <td>{dataset.样本充足组数} / {dataset.样本不足组数}</td>
                <td>{dataset.生成时间}</td>
                <td className="row-actions">
                  <button className="link" type="button" onClick={() => void openShared(dataset)}>查看分群表现</button>
                </td>
              </tr>
            ))}
            {shared.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={8}>暂无同源归因结果数据集，请先在事件分析页发起归因计算</td>
              </tr>
            )}
          </tbody>
        </table>

        {sharedDetail && (
          <div className="panel-sub">
            <header className="panel-head">
              <h4>分群表现：{sharedDetail.批次键}</h4>
              <button className="btn ghost" type="button" onClick={() => setSharedDetail(null)}>收起</button>
            </header>
            <table className="data-table">
              <thead>
                <tr>
                  <th>分群（{sharedDetail.规则版本}）</th>
                  <th>触达用户数</th>
                  <th>归因转化用户数</th>
                  <th>转化率</th>
                  <th>置信区间（{formatPct(sharedDetail.置信水平)}）</th>
                  <th>样本判定</th>
                </tr>
              </thead>
              <tbody>
                {(sharedDetail.rows ?? []).map((row) => (
                  <tr key={row.分组值}>
                    <td>{row.分组值}</td>
                    <td>{row.触达用户数}</td>
                    <td>{row.归因转化用户数}</td>
                    <td>{formatPct(row.转化率)}</td>
                    <td>[{formatPct(row.置信区间下限)}, {formatPct(row.置信区间上限)}]</td>
                    <td>
                      <span className={row.样本判定 === '样本充足' ? 'badge ok' : 'badge warn'}>
                        {row.样本判定}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  )
}
