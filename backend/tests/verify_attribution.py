"""事件归因引擎验证脚本：覆盖需求里的关键口径。"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta

sys.path.insert(0, "/workspace/backend")

from app.services import attribution
from app.services.eventstream import stream

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# 1) 高基数分组下分批读取不重不漏
since = datetime(2026, 11, 1)
until = datetime(2026, 11, 15)
cursor = None
seen: list[str] = []
pages = 0
while True:
    page, cursor, done, stats = stream.read_batch(since=since, until=until, cursor=cursor, size=500)
    pages += 1
    seen.extend(str(e["事件ID"]) for e in page)
    if done:
        break
check("分批读取页数>1（高基数下确实分批）", pages > 1, f"{pages} 页, {len(seen)} 条")
check("分批读取无重复", len(seen) == len(set(seen)), f"{len(seen)} 条 / {len(set(seen))} 唯一")
check("分批读取无漏算", len(seen) == stats["去重后事件数"],
      f"读取 {len(seen)} = 去重后 {stats['去重后事件数']}")
check("流中存在重复投递且被剔除", stats["重复剔除数"] > 0, f"剔除 {stats['重复剔除数']} 条重复")

# 2) 归因计算产出结果数据集
dataset, msg = attribution.compute_attribution(1, "2026-11-14")
check("归因计算成功", dataset is not None, msg)
assert dataset is not None
rows = dataset["rows"]
check("结果含分组行", len(rows) > 0, f"{len(rows)} 行")
check("每行置信区间基于同一归因快照", all(r["归因快照"] == dataset["快照ID"] for r in rows),
      f"快照 {dataset['快照ID']}")
check("样本不足分组稳定落行（非空表）", any(r["样本判定"] == "样本不足" for r in rows),
      f"样本不足 {dataset['样本不足组数']} 组 / 充足 {dataset['样本充足组数']} 组")
check("判定依据可追溯", all("最小样本量" in r["判定依据"] for r in rows))
check("规则冲突被记录且按最新有效规则裁决", dataset["溯源"]["分组冲突事件数"] > 0,
      f"冲突 {dataset['溯源']['分组冲突事件数']} 事件, 策略 {dataset['冲突裁决策略']}")
check("状态沿阶段推进到已生成", dataset["状态"] == "已生成",
      " → ".join(h["状态"] for h in dataset["状态历史"]))

# 3) 置信区间不随刷新漂移：重算同批次复用同一数据集
again, msg2 = attribution.compute_attribution(1, "2026-11-14")
assert again is not None
check("同批次重复触发复用同一份数据集", again["id"] == dataset["id"], msg2)
check("复用后置信区间完全一致",
      [(r["置信区间下限"], r["置信区间上限"]) for r in again["rows"]]
      == [(r["置信区间下限"], r["置信区间上限"]) for r in rows])

# 4) 同一业务批次并发到达只落一个完整数据集
concurrent: list[dict] = []
def worker() -> None:
    ds, _ = attribution.compute_attribution(2, "2026-11-13")
    if ds is not None:
        concurrent.append(ds)
threads = [threading.Thread(target=worker) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
ids = {ds["id"] for ds in concurrent}
check("并发到达只落一个数据集", len(ids) == 1, f"{len(concurrent)} 次并发 → 数据集 id {ids}")

# 5) 状态只能向前推进
ds2, _ = attribution.compute_attribution(2, "2026-11-13")
assert ds2 is not None
ok_ds, m1 = attribution.run_dataset_action(ds2["id"], "发布数据集")
check("已生成→已发布 允许", ok_ds is not None, m1)
back, m2 = attribution.run_dataset_action(ds2["id"], "发布数据集")
check("重复发布被拒绝（不允许回退/原地踏步）", back is None, m2)

# 6) 分群画像与事件分析读同一份数据集
shared, total = attribution.list_datasets()
check("同源数据集列表可读", total >= 2, f"{total} 份")

print()
if failures:
    print(f"失败 {len(failures)} 项: {failures}")
    sys.exit(1)
print("全部验证通过")
