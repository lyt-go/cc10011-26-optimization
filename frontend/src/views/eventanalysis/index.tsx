import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/eventanalysis'
const columns = [
  "模型编号",
  "事件名称",
  "分组维度",
  "度量指标",
  "时间粒度",
  "归因窗口",
  "置信水平",
  "最小样本量"
]
const actions = [
  "提交模型评审",
  "启用模型",
  "归档模型"
]

type Row = Record<string, string | number | null>
type AttributionRow = {
  分组值: string
  触达用户数: number
  归因转化用户数: number
  转化率: number
  置信区间下限: number
  置信区间上限: number
  置信水平: number
  样本判定: string
  判定依据: string
  归因快照: string
}
type Dataset = {
  id: number
  批次键: string
  模型编号: string
  事件名称: string
  业务批次: string
  状态: string
  快照ID: string
  规则版本: string
  冲突裁决策略: string
  归因窗口: string
  置信水平: number
  最小样本量: number
  行数: number
  样本充足组数: number
  样本不足组数: number
  生成时间: string
  状态历史: { 状态: string; 时间: string }[]
  溯源: Record<string, string | number>
  rows?: AttributionRow[]
}

function formatPct(value: number) {
  return `${(value * 100).toFixed(2)}%`
}

export default function EventanalysisView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetTotal, setDatasetTotal] = useState(0)
  const [detail, setDetail] = useState<Dataset | null>(null)
  const [batchKey, setBatchKey] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('分析模型列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件分析列表读取失败')
    }
  }, [filters])

  const reloadDatasets = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/attribution`)
      if (!response.ok) {
        throw new Error('事件归因结果数据集读取失败')
      }
      const payload = await response.json()
      setDatasets(payload.items ?? [])
      setDatasetTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件归因结果数据集读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
    void reloadDatasets()
  }, [reload, reloadDatasets])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('事件分析动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件分析操作失败')
    }
  }

  async function computeAttribution(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/compute`, {
        method: 'POST',
        body: JSON.stringify({ 模型id: row.id, 业务批次: batchKey.trim() || undefined }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        throw new Error(payload.message || '事件归因计算未生效')
      }
      setNotice(payload.message)
      await reloadDatasets()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件归因计算失败')
    }
  }

  async function openDetail(dataset: Dataset) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/attribution/${dataset.id}`)
      if (!response.ok) {
        throw new Error('事件归因结果数据集明细读取失败')
      }
      setDetail(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件归因结果数据集明细读取失败')
    }
  }

  async function publishDataset(dataset: Dataset) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/attribution/${dataset.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action: '发布数据集' }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        throw new Error(payload.message || '数据集状态推进未生效')
      }
      setNotice(payload.message)
      await reloadDatasets()
      if (detail?.id === dataset.id) {
        await openDetail(dataset)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据集状态推进失败')
    }
  }

  const stats = useMemo(() => ([
    { label: '分析模型总数', value: total },
    { label: '已启用模型', value: rows.filter((row) => row.status === '已启用').length },
    { label: '归因结果数据集', value: datasetTotal },
  ]), [total, rows, datasetTotal])

  return (
    <section className="page" data-module="eventanalysis">
      <header className="page-head">
        <div>
          <h2>事件分析管理</h2>
          <p className="page-desc">定义事件驱动的分析模型，对特定事件进行多维度归因和度量。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('分析模型登记入口尚未接入审批流')}>
            登记分析模型
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出事件分析清单
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
                <button className="link" type="button" onClick={() => void computeAttribution(row)}>
                  计算归因
                </button>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无事件分析数据，可先登记分析模型</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条事件分析记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
        {notice && <span className="notice-text">{notice}</span>}
      </footer>

      <section className="panel">
        <header className="panel-head">
          <h3>事件归因结果数据集</h3>
          <div className="panel-actions">
            <label className="filter-item">
              <span>业务批次（留空取最新批次）</span>
              <input
                value={batchKey}
                placeholder="如 2026-11-14"
                onChange={(event) => setBatchKey(event.target.value)}
              />
            </label>
            <button className="btn" type="button" onClick={() => void reloadDatasets()}>刷新列表</button>
          </div>
        </header>
        <p className="panel-desc">
          读取用户事件流与分析模型配置，按归因窗口归并计算；同一业务批次只落一份完整结果，
          置信区间与样本判定基于同一归因快照，可追溯、可复算。
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
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((dataset) => (
              <tr key={dataset.id}>
                <td>{dataset.批次键}</td>
                <td>{dataset.事件名称}</td>
                <td>{dataset.快照ID}</td>
                <td>{dataset.规则版本}</td>
                <td><span className="badge">{dataset.状态}</span></td>
                <td>{dataset.样本充足组数} / {dataset.样本不足组数}</td>
                <td>{dataset.生成时间}</td>
                <td className="row-actions">
                  <button className="link" type="button" onClick={() => void openDetail(dataset)}>查看明细</button>
                  {dataset.状态 === '已生成' && (
                    <button className="link" type="button" onClick={() => void publishDataset(dataset)}>发布数据集</button>
                  )}
                </td>
              </tr>
            ))}
            {datasets.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={8}>暂无归因结果数据集，可在上方模型行点击「计算归因」生成</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {detail && (
        <section className="panel">
          <header className="panel-head">
            <h3>归因明细：{detail.批次键}</h3>
            <button className="btn ghost" type="button" onClick={() => setDetail(null)}>收起明细</button>
          </header>

          {detail.样本不足组数 > 0 && (
            <p className="conclusion-bar">
              稳定结论：{detail.样本不足组数} 个分组样本不足，按口径「{String(detail.溯源['样本判定口径'] ?? '')}」判定，
              结论待样本积累，页面不再展示空表。
            </p>
          )}

          <table className="data-table">
            <thead>
              <tr>
                <th>分组值</th>
                <th>触达用户数</th>
                <th>归因转化用户数</th>
                <th>转化率</th>
                <th>置信区间（{formatPct(detail.置信水平)}）</th>
                <th>样本判定</th>
                <th>判定依据</th>
              </tr>
            </thead>
            <tbody>
              {(detail.rows ?? []).map((row) => (
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
                  <td>{row.判定依据}</td>
                </tr>
              ))}
              {(detail.rows ?? []).length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={7}>本批次无分组行，归因窗口内未归并到事件</td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="trace-grid">
            <div><span>归因快照</span><strong>{detail.快照ID}</strong></div>
            <div><span>规则版本</span><strong>{detail.规则版本}</strong></div>
            <div><span>冲突裁决策略</span><strong>{detail.冲突裁决策略}</strong></div>
            <div><span>分组冲突事件数</span><strong>{String(detail.溯源['分组冲突事件数'] ?? '—')}</strong></div>
            <div><span>读取批次数</span><strong>{String(detail.溯源['读取批次数'] ?? '—')}</strong></div>
            <div><span>原始事件数</span><strong>{String(detail.溯源['原始事件数'] ?? '—')}</strong></div>
            <div><span>重复剔除数</span><strong>{String(detail.溯源['重复剔除数'] ?? '—')}</strong></div>
            <div><span>去重后事件数</span><strong>{String(detail.溯源['去重后事件数'] ?? '—')}</strong></div>
            <div><span>状态流转</span><strong>{detail.状态历史.map((h) => h.状态).join(' → ')}</strong></div>
          </div>
          <p className="panel-desc">{String(detail.溯源['置信区间口径'] ?? '')}</p>
        </section>
      )}
    </section>
  )
}
