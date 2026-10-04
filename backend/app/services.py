"""核心业务逻辑：称重标签、扫码拦截、结账、调价、纠错、过敏原追溯。

设计要点：
- 标签条码全局唯一；付款前注销的标签 status=voided，扫码一律拦截。
- 交易行保存成交瞬间的单价/金额快照，商品事后调价不影响原订单。
- 结账后不能换货：只允许主管按原标签与实物差额补收/退款，原金额原样保留。
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


# ---------------- 标签序列化 ----------------
def label_out(l: models.WeightLabel) -> Dict:
    return {
        "id": l.id,
        "barcode": l.barcode,
        "product_id": l.product_id,
        "batch_id": l.batch_id,
        "product_name": l.product.name,
        "batch_no": l.batch.batch_no,
        "net_weight": l.net_weight,
        "unit_price": l.unit_price,
        "amount": l.amount,
        "allergens": _split(l.allergens_snapshot),
        "status": l.status,
        "printed_at": l.printed_at,
        "voided_at": l.voided_at,
        "void_reason": l.void_reason,
        "reprint_of_barcode": None,
    }


def _split(s: Optional[str]) -> List[str]:
    return [x for x in (s or "").split("、") if x]


def _get_user(db: Session, user_id: int) -> models.User:
    u = db.get(models.User, user_id)
    if not u:
        raise HTTPException(404, f"用户不存在: {user_id}")
    return u


def _require_role(db: Session, user_id: int, role: str):
    u = _get_user(db, user_id)
    if u.role != role:
        raise HTTPException(403, f"该操作仅{ '主管' if role == 'supervisor' else role }可执行")
    return u


# ---------------- 称重台 ----------------
def weigh(db: Session, *, product_id: int, batch_id: int, net_weight: float,
          user_id: int, reprint_of_barcode: Optional[str] = None) -> models.WeightLabel:
    user = _get_user(db, user_id)
    product = db.get(models.Product, product_id)
    if not product or not product.active:
        raise HTTPException(404, "商品不存在或已停售")
    batch = db.get(models.Batch, batch_id)
    if not batch or batch.product_id != product_id:
        raise HTTPException(404, "批次不存在或不属于该商品")
    if net_weight <= 0:
        raise HTTPException(400, "净重必须大于 0")

    old_label = None
    if reprint_of_barcode:
        old_label = db.scalar(
            select(models.WeightLabel).where(
                models.WeightLabel.barcode == reprint_of_barcode)
        )
        if not old_label:
            raise HTTPException(404, "被补打的原标签不存在")
        if old_label.status == "voided":
            raise HTTPException(409, f"原标签 {old_label.barcode} 已是作废码，不能再补打")
        if old_label.status == "consumed":
            raise HTTPException(409, "原标签已结账售出，不能补打，须走结账后纠错流程")

    next_id = (db.query(models.WeightLabel).count() or 0) + 1
    label = models.WeightLabel(
        barcode=f"29{next_id:011d}",
        product_id=product.id,
        batch_id=batch.id,
        net_weight=round(net_weight, 3),
        unit_price=product.unit_price,
        amount=round(net_weight * product.unit_price, 2),
        allergens_snapshot="、".join(a.name for a in product.allergens),
        printed_by=user.id,
    )
    db.add(label)

    # 重新称重即注销旧码，旧码从此不可扫描
    if old_label:
        old_label.status = "voided"
        old_label.voided_at = datetime.utcnow()
        old_label.voided_by = user.id
        old_label.void_reason = f"付款前重新称重补打，新码 {label.barcode}"
        label.reprint_of_id = old_label.id

    db.commit()
    db.refresh(label)
    return label


def void_label(db: Session, *, barcode: str, user_id: int, reason: str) -> models.WeightLabel:
    user = _get_user(db, user_id)
    label = db.scalar(select(models.WeightLabel).where(
        models.WeightLabel.barcode == barcode))
    if not label:
        raise HTTPException(404, f"条码不存在: {barcode}")
    if label.status == "voided":
        raise HTTPException(409, f"标签 {barcode} 已是作废状态")
    if label.status == "consumed":
        raise HTTPException(409, "标签已随订单结账，不能注销；请发起结账后纠错申请")
    label.status = "voided"
    label.voided_at = datetime.utcnow()
    label.voided_by = user.id
    label.void_reason = reason or "称重有误，付款前注销"
    db.commit()
    db.refresh(label)
    return label


def recent_labels(db: Session, limit: int = 30) -> List[Dict]:
    rows = db.scalars(
        select(models.WeightLabel).order_by(
            models.WeightLabel.id.desc()).limit(limit)).all()
    out = []
    for l in rows:
        d = label_out(l)
        if l.reprint_of_id:
            old = db.get(models.WeightLabel, l.reprint_of_id)
            d["reprint_of_barcode"] = old.barcode if old else None
        out.append(d)
    return out


# ---------------- 收银台 ----------------
def _new_txn(db: Session, cashier: models.User) -> models.Transaction:
    seq = db.query(models.Transaction).count() + 1
    txn = models.Transaction(
        txn_no=f"T{datetime.utcnow():%Y%m%d%H%M%S}{seq:03d}",
        cashier_id=cashier.id,
        status="open",
    )
    db.add(txn)
    db.flush()
    return txn


def scan(db: Session, *, barcode: str, cashier_id: int,
         txn_no: Optional[str] = None) -> Tuple[models.Transaction, models.TransactionItem]:
    """收银扫描。作废码 / 已用码 / 不存在的码一律拦截。"""
    cashier = _get_user(db, cashier_id)
    label = db.scalar(select(models.WeightLabel).where(
        models.WeightLabel.barcode == barcode))

    if not label:
        raise HTTPException(404, f"无效条码 {barcode}：系统中无此标签")
    if label.status == "voided":
        # 关键拦截点：旧码注销后，任何扫描都不得入账
        raise HTTPException(
            409,
            f"标签 {barcode} 已作废（{label.void_reason or '称重错误'}），扫码拦截，禁止入账",
        )
    if label.status == "consumed":
        existing = db.scalar(select(models.TransactionItem).where(
            models.TransactionItem.label_id == label.id))
        t = db.get(models.Transaction, existing.txn_id) if existing else None
        raise HTTPException(
            409, f"标签 {barcode} 已在订单 {t.txn_no if t else '?'} 中使用，不得重复扫描")

    if txn_no:
        txn = db.scalar(select(models.Transaction).where(
            models.Transaction.txn_no == txn_no))
        if not txn:
            raise HTTPException(404, f"交易不存在: {txn_no}")
        if txn.status == "paid":
            raise HTTPException(409, f"订单 {txn_no} 已结账，不能继续扫码")
    else:
        txn = _new_txn(db, cashier)

    item = models.TransactionItem(
        txn_id=txn.id,
        label_id=label.id,
        product_id=label.product_id,
        batch_id=label.batch_id,
        product_name=label.product.name,
        unit_price=label.unit_price,     # 成交价格快照
        net_weight=label.net_weight,
        amount=label.amount,
        allergens_snapshot=label.allergens_snapshot,
    )
    db.add(item)
    label.status = "consumed"
    db.flush()
    txn.total_amount = round(sum(i.amount for i in txn.items), 2)
    db.commit()
    db.refresh(txn)
    return txn, item


def remove_scanned_item(db: Session, *, txn_no: str, item_id: int,
                        cashier_id: int) -> models.Transaction:
    """付款前允许取消某一行扫描，标签恢复为有效。"""
    _get_user(db, cashier_id)
    txn = db.scalar(select(models.Transaction).where(
        models.Transaction.txn_no == txn_no))
    if not txn:
        raise HTTPException(404, "交易不存在")
    if txn.status != "open":
        raise HTTPException(409, "订单已结账，不能移除商品")
    item = db.get(models.TransactionItem, item_id)
    if not item or item.txn_id != txn.id:
        raise HTTPException(404, "交易行不存在")
    label = db.get(models.WeightLabel, item.label_id)
    label.status = "active"
    db.delete(item)
    db.flush()
    txn.total_amount = round(sum(i.amount for i in txn.items), 2)
    db.commit()
    db.refresh(txn)
    return txn


def pay(db: Session, *, txn_no: str, cashier_id: int) -> models.Transaction:
    _get_user(db, cashier_id)
    txn = db.scalar(select(models.Transaction).where(
        models.Transaction.txn_no == txn_no))
    if not txn:
        raise HTTPException(404, "交易不存在")
    if txn.status == "paid":
        raise HTTPException(409, "订单已结账，请勿重复收款")
    if not txn.items:
        raise HTTPException(400, "空订单不能结账")
    txn.status = "paid"
    txn.paid_at = datetime.utcnow()
    db.commit()
    db.refresh(txn)
    return txn


def txn_out(db: Session, txn: models.Transaction) -> Dict:
    items = []
    for it in txn.items:
        label = db.get(models.WeightLabel, it.label_id)
        product = db.get(models.Product, it.product_id)
        items.append({
            "id": it.id,
            "label_barcode": label.barcode if label else "",
            "product_name": it.product_name,
            "batch_no": db.get(models.Batch, it.batch_id).batch_no,
            "unit_price": it.unit_price,           # 成交时价格
            "current_unit_price": product.unit_price if product else None,  # 现价（仅展示）
            "net_weight": it.net_weight,
            "amount": it.amount,
            "allergens": _split(it.allergens_snapshot),
        })
    return {
        "txn_no": txn.txn_no,
        "status": txn.status,
        "created_at": txn.created_at,
        "paid_at": txn.paid_at,
        "cashier_name": txn.cashier.display_name,
        "total_amount": txn.total_amount,
        "items": items,
    }


def get_txn(db: Session, txn_no: str) -> models.Transaction:
    txn = db.scalar(select(models.Transaction).where(
        models.Transaction.txn_no == txn_no))
    if not txn:
        raise HTTPException(404, f"订单不存在: {txn_no}")
    return txn


def list_txns(db: Session, limit: int = 50) -> List[Dict]:
    rows = db.scalars(
        select(models.Transaction).order_by(
            models.Transaction.id.desc()).limit(limit)).all()
    return [txn_out(db, t) for t in rows]


# ---------------- 调价 ----------------
def change_price(db: Session, *, product_id: int, new_price: float,
                 user_id: int) -> models.PriceChange:
    user = _get_user(db, user_id)
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "商品不存在")
    if new_price < 0:
        raise HTTPException(400, "单价不能为负")
    if abs(new_price - product.unit_price) < 1e-9:
        raise HTTPException(400, "新价格与当前价格相同")
    pc = models.PriceChange(
        product_id=product.id,
        old_price=product.unit_price,
        new_price=new_price,
        changed_by=user.id,
    )
    product.unit_price = new_price
    db.add(pc)
    db.commit()
    db.refresh(pc)
    return pc


def price_as_of(db: Session, product_id: int, t: datetime) -> float:
    """商品在某一时点（通常是原订单成交时刻）的单价。

    取该时点之前最近一次调价后的新价；此前无调价则为商品建档价（=现价仅当从未调价）。
    """
    product = db.get(models.Product, product_id)
    changes = db.scalars(
        select(models.PriceChange)
        .where(models.PriceChange.product_id == product_id,
               models.PriceChange.changed_at <= t)
        .order_by(models.PriceChange.changed_at.desc())
    ).all()
    if changes:
        return changes[0].new_price
    # 没有任何调价记录：建档价即当时价格
    first_change = db.scalar(
        select(models.PriceChange)
        .where(models.PriceChange.product_id == product_id)
        .order_by(models.PriceChange.changed_at.asc()))
    return first_change.old_price if first_change else product.unit_price


# ---------------- 结账后纠错 ----------------
def create_correction(db: Session, *, txn_no: str, label_barcode: str,
                      cashier_id: int, reason: str) -> models.Correction:
    cashier = _get_user(db, cashier_id)
    if cashier.role not in ("cashier", "supervisor"):
        raise HTTPException(403, "非法角色")
    txn = get_txn(db, txn_no)
    if txn.status != "paid":
        raise HTTPException(409, "仅已结账订单需要纠错，付款前请直接注销标签重新称重")
    label = db.scalar(select(models.WeightLabel).where(
        models.WeightLabel.barcode == label_barcode))
    if not label:
        raise HTTPException(404, "标签不存在")
    item = db.scalar(select(models.TransactionItem).where(
        models.TransactionItem.label_id == label.id,
        models.TransactionItem.txn_id == txn.id))
    if not item:
        raise HTTPException(404, "该标签不属于此订单，无法发起纠错")
    dup = db.scalar(select(models.Correction).where(
        models.Correction.item_id == item.id,
        models.Correction.status == "pending"))
    if dup:
        raise HTTPException(409, "该交易行已有待审批的纠错申请")

    seq = db.query(models.Correction).count() + 1
    c = models.Correction(
        correction_no=f"C{datetime.utcnow():%Y%m%d%H%M%S}{seq:03d}",
        txn_id=txn.id,
        item_id=item.id,
        label_id=label.id,
        requested_by=cashier.id,
        reason=reason,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def review_correction(db: Session, *, correction_no: str, supervisor_id: int,
                      correct_product_id: int, correct_weight: float,
                      decision: str, supervisor_note: str) -> models.Correction:
    """主管核对原标签与实物：按原成交时点价格核算差额，决定补收/退款。

    原交易金额永不修改；差额与原交易核对一致后方可入账。
    """
    _require_role(db, supervisor_id, "supervisor")
    if decision not in ("charge", "refund", "none"):
        raise HTTPException(400, "decision 必须为 charge/refund/none")
    c = db.scalar(select(models.Correction).where(
        models.Correction.correction_no == correction_no))
    if not c:
        raise HTTPException(404, "纠错单不存在")
    if c.status != "pending":
        raise HTTPException(409, f"纠错单已 {c.status}，不能重复审批")

    correct_product = db.get(models.Product, correct_product_id)
    if not correct_product or not correct_product.active:
        raise HTTPException(404, "实物商品不存在或已停售")
    if correct_weight <= 0:
        raise HTTPException(400, "实物净重必须大于 0")

    txn = db.get(models.Transaction, c.txn_id)
    original_item = db.get(models.TransactionItem, c.item_id)

    # 与原交易核对：纠错行必须仍指向原交易、原金额保持未被篡改
    if original_item.txn_id != txn.id:
        raise HTTPException(409, "核对失败：纠错行与原交易不一致")
    expected_diff_basis = original_item.amount

    # 按原订单成交时点的价格核算（事后调价不影响纠错核算）
    unit_at_txn = price_as_of(db, correct_product.id, txn.paid_at)
    correct_amount = round(correct_weight * unit_at_txn, 2)
    diff = round(correct_amount - expected_diff_basis, 2)

    if decision == "charge" and diff <= 0:
        raise HTTPException(
            409, f"核对冲突：实物应收 {correct_amount:.2f} ≤ 原收 {expected_diff_basis:.2f}，"
                 f"不存在应补收的差额，请改选退款")
    if decision == "refund" and diff >= 0:
        raise HTTPException(
            409, f"核对冲突：实物应收 {correct_amount:.2f} ≥ 原收 {expected_diff_basis:.2f}，"
                 f"不存在应退款的差额，请改选补收")
    if decision == "none" and abs(diff) > 0.005:
        raise HTTPException(
            409, f"核对冲突：存在差额 {diff:+.2f} 元，必须选择补收或退款")

    c.correct_product_id = correct_product.id
    c.correct_weight = round(correct_weight, 3)
    c.correct_unit_price = unit_at_txn
    c.correct_amount = correct_amount
    c.diff_amount = diff
    c.decision = decision
    c.reviewed_by = supervisor_id
    c.reviewed_at = datetime.utcnow()
    c.supervisor_note = supervisor_note
    c.status = "approved"
    db.commit()
    db.refresh(c)
    return c


def reject_correction(db: Session, *, correction_no: str,
                      supervisor_id: int, note: str) -> models.Correction:
    _require_role(db, supervisor_id, "supervisor")
    c = db.scalar(select(models.Correction).where(
        models.Correction.correction_no == correction_no))
    if not c or c.status != "pending":
        raise HTTPException(404, "待审批纠错单不存在")
    c.status = "rejected"
    c.reviewed_by = supervisor_id
    c.reviewed_at = datetime.utcnow()
    c.supervisor_note = note or "驳回"
    db.commit()
    db.refresh(c)
    return c


def correction_out(db: Session, c: models.Correction) -> Dict:
    txn = db.get(models.Transaction, c.txn_id)
    item = db.get(models.TransactionItem, c.item_id)
    label = db.get(models.WeightLabel, c.label_id)
    batch = db.get(models.Batch, item.batch_id)
    requester = db.get(models.User, c.requested_by)
    reviewer = db.get(models.User, c.reviewed_by) if c.reviewed_by else None
    correct_product = db.get(models.Product, c.correct_product_id) if c.correct_product_id else None
    return {
        "correction_no": c.correction_no,
        "txn_no": txn.txn_no,
        "label_barcode": label.barcode,
        "product_name": item.product_name,
        "batch_no": batch.batch_no,
        "original_amount": item.amount,
        "reason": c.reason,
        "status": c.status,
        "requested_by_name": requester.display_name,
        "requested_at": c.requested_at,
        "correct_product_name": correct_product.name if correct_product else None,
        "correct_weight": c.correct_weight,
        "correct_unit_price": c.correct_unit_price,
        "correct_amount": c.correct_amount,
        "diff_amount": c.diff_amount,
        "decision": c.decision,
        "reviewed_by_name": reviewer.display_name if reviewer else None,
        "reviewed_at": c.reviewed_at,
        "supervisor_note": c.supervisor_note,
    }


def list_corrections(db: Session, status: Optional[str] = None) -> List[Dict]:
    stmt = select(models.Correction).order_by(models.Correction.id.desc())
    if status:
        stmt = stmt.where(models.Correction.status == status)
    return [correction_out(db, c) for c in db.scalars(stmt).all()]


def correction_preview(db: Session, *, txn_no: str, label_barcode: str,
                       correct_product_id: int, correct_weight: float) -> Dict:
    """主管审批前的“与原交易核对”试算，不落库。"""
    txn = get_txn(db, txn_no)
    label = db.scalar(select(models.WeightLabel).where(
        models.WeightLabel.barcode == label_barcode))
    if not label:
        raise HTTPException(404, "标签不存在")
    item = db.scalar(select(models.TransactionItem).where(
        models.TransactionItem.label_id == label.id,
        models.TransactionItem.txn_id == txn.id))
    if not item:
        raise HTTPException(404, "该标签不属于此订单")
    product = db.get(models.Product, correct_product_id)
    if not product:
        raise HTTPException(404, "实物商品不存在")
    unit_at_txn = price_as_of(db, product.id, txn.paid_at)
    correct_amount = round(correct_weight * unit_at_txn, 2)
    diff = round(correct_amount - item.amount, 2)
    return {
        "txn_no": txn.txn_no,
        "txn_paid_at": txn.paid_at,
        "label_barcode": label.barcode,
        "original_product": item.product_name,
        "original_weight": item.net_weight,
        "original_unit_price": item.unit_price,
        "original_amount": item.amount,
        "correct_product": product.name,
        "correct_weight": round(correct_weight, 3),
        "unit_price_at_txn_time": unit_at_txn,
        "current_unit_price": product.unit_price,
        "correct_amount": correct_amount,
        "diff_amount": diff,
        "suggested_decision": "charge" if diff > 0 else ("refund" if diff < 0 else "none"),
    }


# ---------------- 过敏原 / 批次追溯 ----------------
def trace_batch(db: Session, *, batch_no: str,
                allergen: Optional[str] = None) -> Dict:
    """查同批次可能受影响的订单（已结账订单为重点）。"""
    batch = db.scalar(select(models.Batch).where(
        models.Batch.batch_no == batch_no))
    if not batch:
        raise HTTPException(404, f"批次不存在: {batch_no}")

    orders, active_labels = [], []
    for label in batch.labels:
        if allergen and allergen not in _split(label.allergens_snapshot):
            continue
        item = db.scalar(select(models.TransactionItem).where(
            models.TransactionItem.label_id == label.id))
        if item:
            txn = db.get(models.Transaction, item.txn_id)
            orders.append({
                "txn_no": txn.txn_no,
                "status": txn.status,
                "paid_at": txn.paid_at,
                "barcode": label.barcode,
                "product_name": item.product_name,
                "net_weight": item.net_weight,
                "amount": item.amount,
                "allergens": _split(item.allergens_snapshot),
            })
        else:
            active_labels.append({
                "barcode": label.barcode,
                "status": label.status,
                "product_name": label.product.name,
                "net_weight": label.net_weight,
                "allergens": _split(label.allergens_snapshot),
            })
    return {
        "batch_no": batch.batch_no,
        "product_name": batch.product.name,
        "allergen_filter": allergen,
        "affected_orders": sorted(orders, key=lambda x: x["txn_no"]),
        "unsold_or_voided_labels": active_labels,
    }
