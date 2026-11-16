"""事件分析接口：维护分析模型，覆盖提交模型评审、启用模型、归档模型等动作。

事件归因结果数据集：读取用户事件流与分析模型配置，按归因窗口归并计算，
同一业务批次只落一份完整结果，置信区间与样本判定随快照可追溯。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import attribution
from app.services.eventanalysis import EventanalysisService

router = APIRouter(prefix="/api/eventanalysis", tags=["事件分析"])

service = EventanalysisService()

LIST_FIELDS = ["模型编号", "事件名称", "分组维度", "度量指标", "时间粒度", "归因窗口", "置信水平", "最小样本量"]
STATUSES = ["草稿", "模型评审中", "已启用", "已归档"]


@router.post("/compute", response_model=ActionResult)
def compute_attribution(payload: EntryPayload) -> ActionResult:
    """对指定分析模型发起事件归因计算：读取用户事件流与模型配置，按归因窗口
    归并后产出结果数据集。同一业务批次并发到达只落一份完整结果，重复触发直接复用。"""
    raw_model = payload.values.get("模型id", payload.values.get("model_id"))
    try:
        model_id = int(raw_model)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="缺少要计算的分析模型 id")
    batch_key = str(payload.values.get("业务批次") or "").strip() or None
    dataset, message = attribution.compute_attribution(model_id, batch_key)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    summary = {k: v for k, v in dataset.items() if k != "rows"}
    return ActionResult(ok=True, message=message, entry=summary)


@router.get("/attribution", response_model=PageResult[dict])
def list_attribution_datasets(page: int = 1, size: int = 20) -> PageResult[dict]:
    """事件归因结果数据集列表：每个业务批次一份，含快照、规则版本与样本判定汇总。"""
    items, total = attribution.list_datasets(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/attribution/{dataset_id}", response_model=dict)
def get_attribution_dataset(dataset_id: int) -> dict:
    """事件归因结果数据集明细：分组行、置信区间、样本判定与完整溯源信息。"""
    dataset = attribution.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"事件归因结果数据集 {dataset_id} 不存在")
    return dataset


@router.post("/attribution/{dataset_id}/actions", response_model=ActionResult)
def run_attribution_action(dataset_id: int, payload: EntryPayload) -> ActionResult:
    """推进结果数据集状态（如发布数据集）；状态只沿处理阶段向前推进。"""
    action = str(payload.values.get("action") or "").strip()
    dataset, message = attribution.run_dataset_action(dataset_id, action)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    summary = {k: v for k, v in dataset.items() if k != "rows"}
    return ActionResult(ok=True, message=message, entry=summary)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模型编号检索"),
    status: str | None = Query(default=None, description="草稿、模型评审中、已启用、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模型编号与状态过滤事件分析列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析模型明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析模型 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析模型，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析模型已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析模型执行提交模型评审、启用模型、归档模型；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出事件分析清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "eventanalysis", "total": total, "items": items}
