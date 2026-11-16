"""分群画像接口：维护画像定义，覆盖提交画像评审、发布画像、下线画像等动作。

分群画像与事件分析读取同一份事件归因结果数据集：这里只提供只读视图，
数据仍由事件分析的归因计算产出，顺着既有数据流定位处理点。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import attribution
from app.services.segmentprofile import SegmentprofileService

router = APIRouter(prefix="/api/segmentprofile", tags=["分群画像"])

service = SegmentprofileService()

LIST_FIELDS = ["画像编号", "分群名称", "筛选规则", "画像维度", "对比基准", "更新周期", "覆盖对象数", "画像快照"]
STATUSES = ["草稿", "画像评审中", "已发布", "已下线"]


@router.get("/attribution", response_model=PageResult[dict])
def list_shared_attribution(page: int = 1, size: int = 20) -> PageResult[dict]:
    """分群画像视角读取事件归因结果数据集：与事件分析同源，只读不另算。"""
    items, total = attribution.list_datasets(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/attribution/{dataset_id}", response_model=dict)
def get_shared_attribution(dataset_id: int) -> dict:
    """分群画像读取单份事件归因结果数据集明细：分群维度上的归因转化表现。"""
    dataset = attribution.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"事件归因结果数据集 {dataset_id} 不存在")
    return dataset


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按画像编号检索"),
    status: str | None = Query(default=None, description="草稿、画像评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按画像编号与状态过滤分群画像列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条画像定义明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"画像定义 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条画像定义，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="画像定义已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条画像定义执行提交画像评审、发布画像、下线画像；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分群画像清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "segmentprofile", "total": total, "items": items}
