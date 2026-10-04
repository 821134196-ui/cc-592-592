"""FastAPI 入口：业务 API + 本地模拟扫码枪/小票打印机 + 静态前端托管。

一条命令启动：./run.sh  → http://127.0.0.1:8000
"""
import os
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, services
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="超市散装食品称重标签错贴纠正系统", version="1.0")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static")


# ============ 基础数据 ============
@app.get("/api/users", response_model=List[schemas.UserOut])
def users(db: Session = Depends(get_db)):
    return db.scalars(select(models.User).order_by(models.User.id)).all()


@app.get("/api/products", response_model=List[schemas.ProductOut])
def products(db: Session = Depends(get_db)):
    return db.scalars(
        select(models.Product).where(models.Product.active.is_(True))
        .order_by(models.Product.id)).unique().all()


@app.get("/api/products/{pid}/batches", response_model=List[schemas.BatchOut])
def product_batches(pid: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(models.Batch).where(models.Batch.product_id == pid)
        .order_by(models.Batch.id.desc())).all()


@app.get("/api/batches")
def batches(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.Batch).order_by(models.Batch.id.desc())).all()
    return [{"id": b.id, "batch_no": b.batch_no, "product_id": b.product_id,
             "product_name": b.product.name} for b in rows]


@app.get("/api/labels/recent")
def labels_recent(db: Session = Depends(get_db)):
    return services.recent_labels(db)


# ============ 称重台 ============
@app.post("/api/weigh", response_model=schemas.LabelOut, status_code=201)
def weigh(body: schemas.WeighRequest, db: Session = Depends(get_db)):
    label = services.weigh(
        db,
        product_id=body.product_id,
        batch_id=body.batch_id,
        net_weight=body.net_weight,
        user_id=body.user_id,
        reprint_of_barcode=body.reprint_of_barcode,
    )
    out = services.label_out(label)
    if label.reprint_of_id:
        old = db.get(models.WeightLabel, label.reprint_of_id)
        out["reprint_of_barcode"] = old.barcode if old else None
    return out


@app.post("/api/labels/void", response_model=schemas.LabelOut)
def void_label(body: schemas.VoidRequest, db: Session = Depends(get_db)):
    return services.void_label(
        db, barcode=body.barcode, user_id=body.user_id, reason=body.reason)


# ============ 模拟本地设备接口（扫码枪 / 标签打印机 / 小票打印机） ============
def _render_label_paper(label: models.WeightLabel) -> str:
    p = label.product
    reprint_note = ""
    if label.reprint_of_id:
        old = None
        from sqlalchemy.orm import object_session
        sess = object_session(label)
        if sess is not None:
            old = sess.get(models.WeightLabel, label.reprint_of_id)
        if old:
            reprint_note = f"\n  [补打] 原条码 {old.barcode} 已注销"
    allergens = label.allergens_snapshot or "无"
    head = (
        "╔════════ 散装食品称重标签 ════════╗\n"
        f"║ 商品：{p.name:<22s}║\n"
        f"║ 批次：{label.batch.batch_no:<22s}║\n"
        f"║ 净重：{label.net_weight:>7.3f} kg              ║\n"
        f"║ 单价：{label.unit_price:>8.2f} 元/kg        ║\n"
        f"║ 金额：{label.amount:>8.2f} 元             ║\n"
        f"║ 过敏原：{allergens:<20s}║\n"
        f"║ 条码：{label.barcode:<22s}║\n"
        "╚══════════════════════════════════╝"
    )
    if label.status == "voided":
        head += "\n  ［本标签已作废，收银扫描将被拦截］"
    return head + reprint_note


@app.get("/api/labels/{barcode}/paper", response_class=PlainTextResponse)
def label_paper(barcode: str, db: Session = Depends(get_db)):
    """本地模拟标签打印机：按已有唯一条码补打标签样张。"""
    label = db.scalar(select(models.WeightLabel).where(
        models.WeightLabel.barcode == barcode))
    if not label:
        raise HTTPException(404, f"条码不存在: {barcode}")
    return _render_label_paper(label)


@app.post("/api/device/print-label", response_class=PlainTextResponse)
def print_label(body: schemas.WeighRequest, db: Session = Depends(get_db)):
    """本地模拟标签打印机：称重后打印含唯一条码的实体标签样张。"""
    label = services.weigh(
        db,
        product_id=body.product_id,
        batch_id=body.batch_id,
        net_weight=body.net_weight,
        user_id=body.user_id,
        reprint_of_barcode=body.reprint_of_barcode,
    )
    return _render_label_paper(label)


@app.post("/api/device/scan", status_code=201)
def device_scan(body: schemas.ScanRequest, db: Session = Depends(get_db)):
    """本地模拟扫码枪：扫到作废码时返回 409 拦截，POS 端必须弹红。"""
    txn, _item = services.scan(
        db, barcode=body.barcode, cashier_id=body.cashier_id,
        txn_no=body.txn_no)
    return services.txn_out(db, txn)


# ============ 收银台 ============
@app.post("/api/txn/scan", status_code=201)
def txn_scan(body: schemas.ScanRequest, db: Session = Depends(get_db)):
    txn, _item = services.scan(
        db, barcode=body.barcode, cashier_id=body.cashier_id,
        txn_no=body.txn_no)
    return services.txn_out(db, txn)


@app.post("/api/txn/remove-item")
def txn_remove_item(txn_no: str, item_id: int, cashier_id: int,
                    db: Session = Depends(get_db)):
    txn = services.remove_scanned_item(
        db, txn_no=txn_no, item_id=item_id, cashier_id=cashier_id)
    return services.txn_out(db, txn)


@app.post("/api/txn/pay")
def txn_pay(body: schemas.PayRequest, db: Session = Depends(get_db)):
    txn = services.pay(db, txn_no=body.txn_no, cashier_id=body.cashier_id)
    return services.txn_out(db, txn)


@app.get("/api/txn")
def txn_list(db: Session = Depends(get_db)):
    return services.list_txns(db)


@app.get("/api/txn/{txn_no}")
def txn_detail(txn_no: str, db: Session = Depends(get_db)):
    return services.txn_out(db, services.get_txn(db, txn_no))


@app.get("/api/txn/{txn_no}/receipt", response_class=PlainTextResponse)
def txn_receipt(txn_no: str, db: Session = Depends(get_db)):
    """本地模拟小票打印机输出（结账后可重复补打，金额固定为成交快照）。"""
    txn = services.get_txn(db, txn_no)
    d = services.txn_out(db, txn)
    if d["status"] != "paid":
        return f"【订单 {txn_no} 尚未结账】"
    lines = [
        "          鲜 活 超 市 收 银 小 票          ",
        f"订单号：{d['txn_no']}",
        f"收银员：{d['cashier_name']}    结账：{d['paid_at']:%Y-%m-%d %H:%M:%S} UTC",
        "-" * 46,
        f"{'商品':<12s}{'批次':<10s}{'净重kg':>7s}{'单价':>8s}{'金额':>8s}",
    ]
    for it in d["items"]:
        lines.append(
            f"{it['product_name']:<12s}{it['batch_no']:<10s}"
            f"{it['net_weight']:>7.3f}{it['unit_price']:>8.2f}{it['amount']:>8.2f}")
        ag = "、".join(it["allergens"])
        lines.append(f"    过敏原: {ag or '无'}    条码: {it['label_barcode']}")
    lines += [
        "-" * 46,
        f"应付合计：{d['total_amount']:.2f} 元（成交价已固定，不受后续调价影响）",
        "          谢谢惠顾，欢迎再次光临          ",
    ]
    return "\n".join(lines)


# ============ 调价 ============
@app.post("/api/products/price-change", response_model=schemas.PriceChangeOut,
          status_code=201)
def price_change(body: schemas.PriceChangeRequest, db: Session = Depends(get_db)):
    pc = services.change_price(
        db, product_id=body.product_id, new_price=body.new_price,
        user_id=body.user_id)
    return {
        "product_id": pc.product_id,
        "product_name": db.get(models.Product, pc.product_id).name,
        "old_price": pc.old_price,
        "new_price": pc.new_price,
        "changed_at": pc.changed_at,
    }


@app.get("/api/products/{pid}/price-history")
def price_history(pid: int, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.PriceChange).where(models.PriceChange.product_id == pid)
        .order_by(models.PriceChange.changed_at.desc())).all()
    return [
        {"old_price": r.old_price, "new_price": r.new_price,
         "changed_at": r.changed_at}
        for r in rows
    ]


# ============ 结账后纠错 ============
@app.post("/api/corrections", status_code=201, response_model=schemas.CorrectionOut)
def correction_create(body: schemas.CorrectionCreate, db: Session = Depends(get_db)):
    c = services.create_correction(
        db, txn_no=body.txn_no, label_barcode=body.label_barcode,
        cashier_id=body.cashier_id, reason=body.reason)
    return services.correction_out(db, c)


@app.get("/api/corrections")
def correction_list(status: Optional[str] = None, db: Session = Depends(get_db)):
    return services.list_corrections(db, status=status)


@app.post("/api/corrections/preview")
def correction_preview(txn_no: str, label_barcode: str,
                       correct_product_id: int, correct_weight: float,
                       db: Session = Depends(get_db)):
    """主管核对试算：按原成交时点价格计算差额，不产生任何记录。"""
    return services.correction_preview(
        db, txn_no=txn_no, label_barcode=label_barcode,
        correct_product_id=correct_product_id,
        correct_weight=correct_weight)


@app.post("/api/corrections/review", response_model=schemas.CorrectionOut)
def correction_review(body: schemas.CorrectionReview, db: Session = Depends(get_db)):
    c = services.review_correction(
        db,
        correction_no=body.correction_no,
        supervisor_id=body.supervisor_id,
        correct_product_id=body.correct_product_id,
        correct_weight=body.correct_weight,
        decision=body.decision,
        supervisor_note=body.supervisor_note,
    )
    return services.correction_out(db, c)


@app.post("/api/corrections/reject", response_model=schemas.CorrectionOut)
def correction_reject(correction_no: str, supervisor_id: int, note: str = "",
                      db: Session = Depends(get_db)):
    c = services.reject_correction(
        db, correction_no=correction_no, supervisor_id=supervisor_id, note=note)
    return services.correction_out(db, c)


# ============ 过敏原 / 批次追溯 ============
@app.get("/api/trace/batch/{batch_no}")
def trace_batch(batch_no: str, allergen: Optional[str] = None,
                db: Session = Depends(get_db)):
    return services.trace_batch(db, batch_no=batch_no, allergen=allergen)


# 静态前端（挂在最后，避免吞掉 /api）
app.mount("/", StaticFiles(directory=os.path.abspath(STATIC_DIR), html=True),
          name="web")
