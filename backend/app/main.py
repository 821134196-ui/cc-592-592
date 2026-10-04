"""FastAPI 主程序：散装食品称重标签错贴纠正系统。"""
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from . import devices, models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="散装食品称重标签错贴纠正系统", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def money(x: float) -> float:
    return round(x + 1e-9, 2)


def gen_no(prefix: str) -> str:
    return f"{prefix}{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:4]}"


def label_to_out(lbl: models.Label) -> schemas.LabelOut:
    return schemas.LabelOut(
        id=lbl.id,
        barcode=lbl.barcode,
        product_id=lbl.product_id,
        product_name=lbl.product_name,
        batch_no=lbl.batch.batch_no if lbl.batch else "",
        net_weight=lbl.net_weight,
        unit_price=lbl.unit_price,
        total_price=lbl.total_price,
        allergen_snapshot=lbl.allergen_snapshot,
        status=lbl.status,
        printed_at=lbl.printed_at,
        voided_at=lbl.voided_at,
        void_reason=lbl.void_reason,
        reprinted_from_barcode=lbl.reprinted_from.barcode if lbl.reprinted_from else None,
    )


def order_to_out(db: Session, order: models.Order) -> schemas.OrderOut:
    items = sorted(order.items, key=lambda i: i.id)
    return schemas.OrderOut(
        id=order.id,
        order_no=order.order_no,
        cashier=order.cashier,
        status=order.status,
        created_at=order.created_at,
        paid_at=order.paid_at,
        items=[
            schemas.OrderItemOut(
                id=i.id,
                label_id=i.label_id,
                product_name=i.product_name,
                batch_no=i.batch_no,
                net_weight=i.net_weight,
                unit_price=i.unit_price,
                final_price=i.final_price,
                allergens_snapshot=i.allergens_snapshot,
            )
            for i in items
        ],
        total=money(sum(i.final_price for i in items)),
    )


def corr_to_out(c: models.CorrectionRequest) -> schemas.CorrectionOut:
    return schemas.CorrectionOut(
        id=c.id,
        req_no=c.req_no,
        kind=c.kind,
        status=c.status,
        order_no=c.order.order_no if c.order else None,
        old_barcode=c.old_label.barcode if c.old_label else None,
        new_barcode=c.new_label.barcode if c.new_label else None,
        reason=c.reason,
        allergen_concern=c.allergen_concern,
        requested_by=c.requested_by,
        requested_at=c.requested_at,
        reviewed_by=c.reviewed_by,
        review_note=c.review_note,
        original_amount=c.original_amount,
        correct_amount=c.correct_amount,
        diff_amount=c.diff_amount,
        adjust_type=c.adjust_type,
    )


def require_supervisor(x_role: Optional[str] = Header(default="cashier")) -> str:
    """角色校验：只有主管能审批纠错、办理补收/退款、调价。"""
    if x_role != "supervisor":
        raise HTTPException(403, "权限不足：该操作仅主管可执行，收银员只能发起申请")
    return x_role


# ===========================================================================
# 商品 / 批次
@app.get("/api/products", response_model=List[schemas.ProductOut], tags=["商品"])
def list_products(db: Session = Depends(get_db)):
    return db.query(models.Product).filter_by(active=True).order_by(models.Product.id).all()


@app.post("/api/products", response_model=schemas.ProductOut, tags=["商品"])
def create_product(body: schemas.ProductIn, db: Session = Depends(get_db)):
    if db.query(models.Product).filter_by(sku=body.sku).first():
        raise HTTPException(409, f"商品编码 {body.sku} 已存在")
    p = models.Product(
        sku=body.sku, name=body.name, unit_price=money(body.unit_price),
        allergens=body.allergens.strip(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@app.put("/api/products/{pid}/price", response_model=schemas.ProductOut, tags=["商品"])
def change_price(pid: int, body: schemas.ProductPriceIn, db: Session = Depends(get_db),
                 _role: str = Depends(require_supervisor)):
    """调价只影响新标签，已打印标签与已成交订单保留原价。"""
    p = db.get(models.Product, pid)
    if not p or not p.active:
        raise HTTPException(404, "商品不存在")
    old = p.unit_price
    p.unit_price = money(body.unit_price)
    db.add(models.PriceChange(
        product_id=p.id, old_price=old, new_price=p.unit_price, reason=body.reason
    ))
    db.commit()
    db.refresh(p)
    return p


@app.get("/api/products/{pid}/price-history", tags=["商品"])
def price_history(pid: int, db: Session = Depends(get_db)):
    p = db.get(models.Product, pid)
    if not p:
        raise HTTPException(404, "商品不存在")
    rows = db.query(models.PriceChange).filter_by(product_id=pid).order_by(
        models.PriceChange.changed_at
    ).all()
    return {
        "product": {"id": p.id, "name": p.name, "current_price": p.unit_price},
        "history": [
            {"old_price": r.old_price, "new_price": r.new_price,
             "reason": r.reason, "changed_by": r.changed_by,
             "changed_at": r.changed_at.isoformat(timespec="seconds")}
            for r in rows
        ],
    }


@app.post("/api/batches", response_model=schemas.BatchOut, tags=["批次"])
def create_batch(body: schemas.BatchIn, db: Session = Depends(get_db)):
    p = db.get(models.Product, body.product_id)
    if not p:
        raise HTTPException(404, "商品不存在")
    if db.query(models.Batch).filter_by(batch_no=body.batch_no).first():
        raise HTTPException(409, "批次号已存在")
    b = models.Batch(batch_no=body.batch_no, product_id=body.product_id, note=body.note)
    db.add(b)
    db.commit()
    db.refresh(b)
    return schemas.BatchOut(
        id=b.id, batch_no=b.batch_no, product_id=b.product_id,
        note=b.note, product_name=p.name,
    )


@app.get("/api/batches", tags=["批次"])
def list_batches(db: Session = Depends(get_db)):
    rows = db.query(models.Batch).order_by(models.Batch.id.desc()).all()
    return [
        {"id": b.id, "batch_no": b.batch_no, "product_id": b.product_id,
         "product_name": b.product.name, "note": b.note}
        for b in rows
    ]


# ===========================================================================
# 称重台：打印标签 / 注销旧码并重贴
# ===========================================================================
@app.post("/api/labels", response_model=schemas.LabelOut, tags=["称重台"])
def print_label(body: schemas.LabelIn, db: Session = Depends(get_db)):
    """称重台称重并打印标签：唯一条码 + 净重/批次/价格/过敏原快照。"""
    p = db.get(models.Product, body.product_id)
    if not p or not p.active:
        raise HTTPException(404, "商品不存在或已停用")
    b = db.get(models.Batch, body.batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    if b.product_id != p.id:
        raise HTTPException(400, "批次与商品不匹配")

    lbl = models.Label(
        barcode="LB" + uuid.uuid4().hex[:16].upper(),
        product_id=p.id,
        batch_id=b.id,
        net_weight=round(body.net_weight, 3),
        unit_price=p.unit_price,                 # 打印瞬间单价快照
        total_price=money(body.net_weight * p.unit_price),
        allergen_snapshot=p.allergens,           # 过敏原快照
        product_name=p.name,
    )
    db.add(lbl)
    db.commit()
    db.refresh(lbl)
    return label_to_out(lbl)


@app.post("/api/labels/void-and-reprint", response_model=schemas.CorrectionOut, tags=["称重台"])
def void_and_reprint(body: schemas.ReprintIn, db: Session = Depends(get_db)):
    """付款前发现贴错：注销旧码，重新称重打印新码。

    - 旧标签置为 void，记录注销原因与时间；
    - 新标签 reprinted_from 指向旧标签；
    - 已被消费(结账)的标签不允许注销 —— 必须走结账后差额流程。
    """
    old = db.query(models.Label).filter_by(barcode=body.old_barcode).first()
    if not old:
        raise HTTPException(404, "旧条码不存在")
    if old.status == "void":
        raise HTTPException(409, f"旧码 {old.barcode} 已注销，禁止再次扫描")
    if old.status == "consumed":
        raise HTTPException(409, "标签已随订单结账，不能注销重贴，请走结账后纠错流程")

    old.status = "void"
    old.voided_at = datetime.now()
    old.void_reason = body.reason

    new = models.Label(
        barcode="LB" + uuid.uuid4().hex[:16].upper(),
        product_id=old.product_id,
        batch_id=old.batch_id,
        net_weight=old.net_weight,          # 演示：沿用原称重；实物侧会重新上秤
        unit_price=old.product.unit_price,  # 用当前价（付款前通常同价）
        total_price=money(old.net_weight * old.product.unit_price),
        allergen_snapshot=old.product.allergens,
        product_name=old.product_name,
        reprinted_from_id=old.id,
    )
    db.add(new)
    db.flush()

    req = models.CorrectionRequest(
        req_no=gen_no("CR"),
        kind="pre_payment_reprint",
        status="done",                      # 付款前重贴由称重台直接执行，系统留痕
        old_label_id=old.id,
        new_label_id=new.id,
        reason=body.reason,
        allergen_concern=bool(old.allergen_snapshot),
        requested_by="station-scale",
        reviewed_by="station-scale",
        reviewed_at=datetime.now(),
        review_note="付款前重贴，旧码即时注销",
        completed_at=datetime.now(),
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return corr_to_out(req)


@app.get("/api/labels", tags=["称重台"])
def list_labels(limit: int = 50, db: Session = Depends(get_db)):
    rows = (db.query(models.Label).order_by(models.Label.id.desc()).limit(limit).all())
    return [label_to_out(l).model_dump() for l in rows]


@app.get("/api/adjustments", tags=["纠错"])
def list_adjustments(db: Session = Depends(get_db)):
    """差额收退款台账。"""
    rows = db.query(models.Adjustment).order_by(models.Adjustment.id.desc()).all()
    return [
        {"id": a.id, "correction_id": a.correction_id, "order_no": a.order_no,
         "direction": a.direction, "amount": a.amount,
         "original_amount": a.original_amount, "handled_by": a.handled_by,
         "handled_at": a.handled_at.isoformat(timespec="seconds"), "note": a.note}
        for a in rows
    ]


# ===========================================================================
# 扫码枪模拟（本地硬件接口）
# ===========================================================================
@app.post("/api/scan/trigger", tags=["扫码模拟"])
def scan_trigger(body: schemas.ScanBody, register_no: str = "R1"):
    """模拟扫码枪触发：把条码推进该收银机的扫码队列。"""
    devices.push_scan(register_no, body.barcode)
    return {"ok": True, "register": register_no, "queued": body.barcode}


@app.get("/api/scan/poll", tags=["扫码模拟"])
def scan_poll(register_no: str = "R1"):
    """收银端轮询/获取扫码枪下一次输入。"""
    code = devices.pop_scan(register_no)
    return {"barcode": code}


# ===========================================================================
# 收银台
# ===========================================================================
@app.post("/api/orders", response_model=schemas.OrderOut, tags=["收银台"])
def create_order(body: schemas.OrderCreate, db: Session = Depends(get_db)):
    order = models.Order(
        order_no=gen_no("SO"), cashier=body.cashier, register_no=body.register_no
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order_to_out(db, order)


@app.get("/api/orders", tags=["收银台"])
def list_orders(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.Order)
    if status:
        q = q.filter_by(status=status)
    return [order_to_out(db, o).model_dump() for o in q.order_by(models.Order.id.desc()).all()]


@app.get("/api/orders/{oid}", response_model=schemas.OrderOut, tags=["收银台"])
def get_order(oid: int, db: Session = Depends(get_db)):
    order = db.get(models.Order, oid)
    if not order:
        raise HTTPException(404, "订单不存在")
    return order_to_out(db, order)


@app.post("/api/orders/{oid}/scan", response_model=schemas.OrderOut, tags=["收银台"])
def scan_into_order(oid: int, body: schemas.ScanBody, db: Session = Depends(get_db)):
    """收银扫描标签入单 —— 关键拦截点。

    作废码 → 409 拦截；已消费码 → 409 拦截；争议码 → 409 拦截；
    有效码 → 按标签快照写入成交行（价格/过敏原永久定格）。
    """
    order = db.get(models.Order, oid)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "open":
        raise HTTPException(409, f"订单 {order.order_no} 已{order.status}，不能再加商品")

    lbl = db.query(models.Label).filter_by(barcode=body.barcode).first()
    if not lbl:
        raise HTTPException(404, f"条码 {body.barcode} 查无此标签")

    if lbl.status == "void":
        raise HTTPException(
            409,
            f"⛔ 作废标签禁止扫描：{lbl.barcode}（{lbl.product_name}，"
            f"已于 {lbl.voided_at:%Y-%m-%d %H:%M} 注销：{lbl.void_reason}）",
        )
    if lbl.status == "consumed":
        raise HTTPException(409, f"⛔ 标签 {lbl.barcode} 已在另一笔订单结账，不能重复使用")
    if lbl.status == "disputed":
        raise HTTPException(409, f"⛔ 标签 {lbl.barcode} 处于争议处理中，暂停收款")

    # 同一订单重复扫同一码也拦截
    exists = db.query(models.OrderItem).filter_by(
        order_id=order.id, label_id=lbl.id
    ).first()
    if exists:
        raise HTTPException(409, f"标签 {lbl.barcode} 已在本单中，请勿重复扫描")

    item = models.OrderItem(
        order_id=order.id,
        label_id=lbl.id,
        product_id=lbl.product_id,
        product_name=lbl.product_name,       # 成交快照
        unit_price=lbl.unit_price,           # 成交单价 = 标签打印时单价
        net_weight=lbl.net_weight,
        final_price=lbl.total_price,
        batch_no=lbl.batch.batch_no,
        allergens_snapshot=lbl.allergen_snapshot,
    )
    db.add(item)
    db.commit()
    db.refresh(order)
    return order_to_out(db, order)


@app.post("/api/orders/{oid}/checkout", response_model=schemas.OrderOut, tags=["收银台"])
def checkout(oid: int, db: Session = Depends(get_db)):
    order = db.get(models.Order, oid)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "open":
        raise HTTPException(409, "订单已结账")
    if not order.items:
        raise HTTPException(400, "空订单不能结账")
    order.status = "paid"
    order.paid_at = datetime.now()
    for it in order.items:
        it.label.status = "consumed"
    db.commit()
    db.refresh(order)
    return order_to_out(db, order)


@app.get("/api/orders/{oid}/receipt", response_class=PlainTextResponse, tags=["收银台"])
def get_receipt(oid: int, db: Session = Depends(get_db)):
    """本地收银回执（打印文件读取）。"""
    order = db.get(models.Order, oid)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "paid":
        raise HTTPException(409, "订单未结账，无回执")
    return devices.print_receipt(order)


# ===========================================================================
# 结账后纠错：收银员发起 → 主管核对 → 补收/退款
# ===========================================================================
@app.post("/api/corrections", response_model=schemas.CorrectionOut, tags=["纠错"])
def create_correction(body: schemas.CorrectionCreate, db: Session = Depends(get_db)):
    """普通收银员只能发起申请（不能自行改价/换货/退款）。"""
    order = db.get(models.Order, body.order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "paid":
        raise HTTPException(400, "仅已结账订单走差额纠错；未付款请注销旧码重贴")

    old = db.get(models.Label, body.old_label_id)
    if not old:
        raise HTTPException(404, "原标签不存在")
    in_order = db.query(models.OrderItem).filter_by(
        order_id=order.id, label_id=old.id
    ).first()
    if not in_order:
        raise HTTPException(400, "原标签不属于该订单")

    real_p = db.get(models.Product, body.correct_product_id)
    if not real_p or not real_p.active:
        raise HTTPException(404, "实物商品不存在")

    original_amount = in_order.final_price
    correct_amount = money(body.correct_weight * real_p.unit_price)
    diff = money(correct_amount - original_amount)

    c = models.CorrectionRequest(
        req_no=gen_no("CR"),
        kind="post_payment_adjust",
        status="pending",
        order_id=order.id,
        old_label_id=old.id,
        reason=body.reason,
        allergen_concern=body.allergen_concern,
        requested_by=body.requested_by,
        correct_product_name=real_p.name,
        correct_weight=round(body.correct_weight, 3),
        correct_allergens=real_p.allergens,
        original_amount=original_amount,        # 原金额永久保留
        correct_amount=correct_amount,
        diff_amount=diff,
        adjust_type="charge" if diff > 0 else ("refund" if diff < 0 else "none"),
    )
    db.add(c)
    db.flush()
    c.related_labels.append(old)
    old.status = "disputed"                    # 争议标签冻结
    db.commit()
    db.refresh(c)
    return corr_to_out(c)


@app.get("/api/corrections", response_model=List[schemas.CorrectionOut], tags=["纠错"])
def list_corrections(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.CorrectionRequest)
    if status:
        q = q.filter_by(status=status)
    return [corr_to_out(c) for c in q.order_by(models.CorrectionRequest.id.desc()).all()]


@app.get("/api/corrections/{cid}", response_model=schemas.CorrectionOut, tags=["纠错"])
def get_correction(cid: int, db: Session = Depends(get_db)):
    c = db.get(models.CorrectionRequest, cid)
    if not c:
        raise HTTPException(404, "纠错单不存在")
    return corr_to_out(c)


@app.post("/api/corrections/{cid}/review", response_model=schemas.CorrectionOut, tags=["纠错"])
def review_correction(cid: int, body: schemas.CorrectionReview, db: Session = Depends(get_db),
                      _role: str = Depends(require_supervisor)):
    """主管核对原标签与实物后批准/驳回。批准只是确认差额，钱款由完成接口收口。"""
    c = db.get(models.CorrectionRequest, cid)
    if not c:
        raise HTTPException(404, "纠错单不存在")
    if c.kind != "post_payment_adjust":
        raise HTTPException(400, "该类纠错无需审批")
    if c.status != "pending":
        raise HTTPException(409, f"纠错单当前状态 {c.status}，不能重复审批")

    c.reviewed_by = body.reviewer
    c.reviewed_at = datetime.now()
    c.review_note = body.review_note
    if body.approve:
        c.status = "approved"
    else:
        c.status = "rejected"
        if c.old_label:
            c.old_label.status = "consumed"   # 驳回，标签恢复正常
    db.commit()
    db.refresh(c)
    return corr_to_out(c)


@app.post("/api/corrections/{cid}/complete", response_model=schemas.CorrectionOut, tags=["纠错"])
def complete_correction(cid: int, body: schemas.CorrectionComplete, db: Session = Depends(get_db),
                        _role: str = Depends(require_supervisor)):
    """主管执行补收/退款：必须与原交易核对（订单、原金额一致才放行）。"""
    c = db.get(models.CorrectionRequest, cid)
    if not c:
        raise HTTPException(404, "纠错单不存在")
    if c.status != "approved":
        raise HTTPException(409, "仅主管批准后的纠错单可办理收退款")

    order = c.order
    # 与原交易核对：订单须存在、已付款、原金额与订单项吻合
    if not order or order.status != "paid":
        raise HTTPException(409, "原交易状态异常，差额处理中止")
    orig_item = db.query(models.OrderItem).filter_by(
        order_id=order.id, label_id=c.old_label_id
    ).first()
    if not orig_item or money(orig_item.final_price) != money(c.original_amount):
        raise HTTPException(409, "与原交易核对失败：原金额不匹配，禁止收退款")

    c.status = "done"
    c.reviewed_by = body.reviewer
    c.completed_at = datetime.now()
    if c.old_label:
        c.old_label.status = "consumed"       # 处理结束，解冻（保留痕迹）

    # 差额台账：原订单金额不动，补收/退款单独记账，与原交易一一挂钩
    if c.adjust_type in ("charge", "refund"):
        db.add(models.Adjustment(
            correction_id=c.id,
            order_no=order.order_no,
            direction=c.adjust_type,
            amount=money(abs(c.diff_amount)),
            original_amount=c.original_amount,
            handled_by=body.reviewer,
            note=f"{c.req_no} 原¥{c.original_amount:.2f} → 应¥{c.correct_amount:.2f}：{c.reason}",
        ))
    db.commit()
    db.refresh(c)
    return corr_to_out(c)


# ===========================================================================
# 过敏原 / 批次追溯
# ===========================================================================
@app.get("/api/trace/batch/{batch_no}", response_model=List[schemas.TraceOrderOut], tags=["追溯"])
def trace_batch(batch_no: str, db: Session = Depends(get_db)):
    """查同批次可能受影响的已结账订单（过敏原事件召回）。"""
    b = db.query(models.Batch).filter_by(batch_no=batch_no).first()
    if not b:
        raise HTTPException(404, "批次不存在")
    rows = (
        db.query(models.OrderItem)
        .join(models.Order, models.OrderItem.order_id == models.Order.id)
        .filter(models.OrderItem.batch_no == batch_no, models.Order.status == "paid")
        .order_by(models.Order.paid_at.desc())
        .all()
    )
    return [
        schemas.TraceOrderOut(
            order_no=it.order.order_no,
            status=it.order.status,
            paid_at=it.order.paid_at,
            product_name=it.product_name,
            batch_no=it.batch_no,
            final_price=it.final_price,
            allergens_snapshot=it.allergens_snapshot,
        )
        for it in rows
    ]


@app.get("/api/trace/allergen/{keyword}", tags=["追溯"])
def trace_allergen(keyword: str, db: Session = Depends(get_db)):
    """按过敏原关键词（如 花生）追溯含该成分的已售订单。"""
    like = f"%{keyword}%"
    rows = (
        db.query(models.OrderItem)
        .join(models.Order, models.OrderItem.order_id == models.Order.id)
        .filter(
            models.Order.status == "paid",
            models.OrderItem.allergens_snapshot.like(like),
        )
        .order_by(models.Order.paid_at.desc())
        .all()
    )
    return [
        {"order_no": it.order.order_no, "paid_at": it.order.paid_at.isoformat(timespec="seconds"),
         "product_name": it.product_name, "batch_no": it.batch_no,
         "allergens_snapshot": it.allergens_snapshot, "final_price": it.final_price}
        for it in rows
    ]


@app.get("/", tags=["系统"])
def root():
    return {"name": "散装食品称重标签错贴纠正系统", "docs": "/docs", "ui": "/static/index.html"}


# 前端静态页面（一条命令同时提供前后端）
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "frontend")
if os.path.isdir(_FRONTEND_DIR):
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/static",
        StaticFiles(directory=os.path.abspath(_FRONTEND_DIR), html=True),
        name="static",
    )
