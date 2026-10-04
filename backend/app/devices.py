"""本地设备模拟：扫码枪队列 + 收银回执打印。

真实超市场景下扫码枪是 HID 键盘设备逐字符输入、回执由热敏打印机输出。
本地演示用内存队列模拟扫码枪，用本地文件 + JSON 接口模拟回执。
"""
import json
import os
from collections import deque
from datetime import datetime

RECEIPT_DIR = os.environ.get(
    "RECEIPT_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "receipts")
)
os.makedirs(RECEIPT_DIR, exist_ok=True)

# 每台收银机一个扫码队列（内存模拟）
_scan_queues: dict[str, deque] = {}


def push_scan(register: str, barcode: str) -> None:
    """模拟扫码枪扫到一个条码（硬件驱动回调）。"""
    _scan_queues.setdefault(register, deque()).append(barcode)


def pop_scan(register: str):
    q = _scan_queues.setdefault(register, deque())
    return q.popleft() if q else None


def print_receipt(order) -> str:
    """把收银回执写到本地文件，返回回执文本（模拟热敏打印）。"""
    lines = [
        "=" * 32,
        "        阳光超市 · 散装食品回执",
        f"订单号 : {order.order_no}",
        f"收银员 : {order.cashier}",
        f"时  间 : {order.paid_at:%Y-%m-%d %H:%M:%S}",
        "-" * 32,
    ]
    total = 0.0
    for it in order.items:
        total += it.final_price
        allergen = f" [过敏原:{it.allergens_snapshot}]" if it.allergens_snapshot else ""
        lines.append(f"{it.product_name}{allergen}")
        lines.append(
            f"  {it.net_weight:.3f}kg x ¥{it.unit_price:.2f} = ¥{it.final_price:.2f}"
        )
        lines.append(f"  批次:{it.batch_no}")
    lines += ["-" * 32, f"合计: ¥{total:.2f}", "=" * 32]
    text = "\n".join(lines)

    path = os.path.join(RECEIPT_DIR, f"{order.order_no}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    # 同步追加一份 JSONL 记账
    journal = os.path.join(RECEIPT_DIR, "receipts.jsonl")
    with open(journal, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "order_no": order.order_no,
            "cashier": order.cashier,
            "printed_at": datetime.now().isoformat(),
            "total": round(total, 2),
        }, ensure_ascii=False) + "\n")
    return text
