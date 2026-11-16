# 实时指标计算与监控大盘平台

面向实时指标计算引擎、多维度下钻分析、告警阈值管理、指标异常检测、数据服务开放与可视化看板配置的数据分析与应用工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时指标 | `realtimemetric` | 指标定义 | 指标编号、指标名称、计算窗口秒 |
| 监控大盘 | `dashboard` | 大盘配置 | 大盘编号、大盘名称、包含指标组 |
| 多维下钻 | `drilldown` | 下钻路径 | 路径编号、起始维度、下钻层级序列 |
| 阈值管理 | `threshold` | 阈值规则 | 规则编号、关联指标、阈值下限 |
| 异常检测 | `anomalydetect` | 检测模型 | 模型编号、监控指标、检测算法 |
| 数据服务 | `dataservice` | 服务接口 | 服务编号、服务名称、接口路径 |
| 告警历史 | `alarmhistory` | 告警记录 | 记录编号、告警规则、触发时间 |
| 同比环比 | `comparison` | 对比配置 | 配置编号、目标指标、对比类型 |
| 聚合配置 | `aggregate` | 聚合规则 | 规则编号、源数据粒度、目标粒度 |
| 筛选预设 | `filterpreset` | 预设模板 | 预设编号、预设名称、维度条件组 |
| 用户洞察 | `userinsight` | 洞察报告 | 报告编号、分析主题、目标人群 |
| 事件分析 | `eventanalysis` | 分析模型 | 模型编号、事件名称、分组维度 |
| 分群画像 | `segmentprofile` | 画像定义 | 画像编号、分群名称、筛选规则 |
| 趋势预测 | `trendforecast` | 预测方案 | 方案编号、预测指标、算法模型 |
| 可视化模板 | `viztemplate` | 图表模板 | 模板编号、图表类型、数据绑定 |
| 订阅推送 | `subscription` | 订阅配置 | 订阅编号、订阅内容、接收人 |
| 维度映射 | `dimensionmap` | 映射规则 | 规则编号、原始维度名、标准维度名 |
| 数据质量分 | `qualityscore` | 评分规则 | 规则编号、数据集名称、完整性权重 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 事件归因计算（事件分析）

围绕事件分析模块提供事件归因能力，处理点沿既有数据流定位：

- `app/services/eventstream.py`：用户事件流读取。高基数分组下用 `(事件时间, 事件ID)`
  keyset 游标分批读取，按 `事件ID` 去重，分批不重不漏；流中保留重复投递用于验证。
- `app/services/attribution.py`：归因引擎。读取事件流与分析模型配置（归因窗口、
  置信水平、最小样本量），按归因窗口做末次触点归并，产出事件归因结果数据集：
  - 同一事件命中首次分组规则与最新有效分组规则冲突时，一个归因快照只钉住
    最新有效规则版本，全快照一致适用，置信区间基于同一归因快照；冲突事件记入溯源。
  - 置信区间用 Wilson 公式按快照确定性计算，刷新不漂移；样本不足的分组照常落行，
    给出稳定的「样本不足」判定与判定依据，不出空表。
  - 同一业务批次并发到达只落一份完整结果数据集；状态只沿
    待计算 → 计算中 → 已生成 → 已发布 向前推进。
- 接口：`POST /api/eventanalysis/compute` 发起计算（幂等，同批复用），
  `GET /api/eventanalysis/attribution` 列表，`GET /api/eventanalysis/attribution/{id}`
  明细（含行级置信区间、样本判定与溯源），`POST /api/eventanalysis/attribution/{id}/actions`
  推进状态。
- 分群画像通过 `GET /api/segmentprofile/attribution` 读取同一份结果数据集（只读）。

验证脚本：`cd backend && .venv/bin/python -I tests/verify_attribution.py`

领域：数据工程
子方向：数据分析与数据应用
