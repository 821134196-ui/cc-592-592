"""散装食品称重标签纠错系统 —— 数据模型。

核心链路：
  Product（商品，含单价/过敏原）
    └─ Batch（批次）
         Label（称重标签：唯一条码 + 净重 + 成交单价快照 + 过敏原快照）
           └─ OrderItem / Order（交易，逐行保存成交时价格）
CorrectionRequest（付款前重贴 / 结账后补退申请，主管审批）
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


# 标签 ↔ 补退申请可能涉及多个标签（核对原标签），用关联表
correction_labels = Table(
    "correction_labels",
    Base.metadata,
    Column("correction_id", Integer, ForeignKey("correction_requests.id"), primary_key=True),
    Column("label_id", Integer, ForeignKey("labels.id"), primary_key=True),
)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sku = Column(String(32), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    unit_price = Column(Float, nullable=False, default=0.0)   # 当前单价（元/kg），可调价
    allergens = Column(String(256), nullable=False, default="")  # 逗号分隔，如 "花生,大豆"
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    batches = relationship("Batch", back_populates="product")
    labels = relationship("Label", back_populates="product")
    price_changes = relationship("PriceChange", back_populates="product")


class PriceChange(Base):
    """调价记录：交易快照之外，再保留价格变更轨迹可查。"""
    __tablename__ = "price_changes"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    reason = Column(String(256), default="")
    changed_by = Column(String(32), default="supervisor-01")
    changed_at = Column(DateTime, default=datetime.now)

    product = relationship("Product", back_populates="price_changes")


class Batch(Base):
    """同一批次用于过敏原追溯：贴错标签时查同批次可能受影响的订单。"""
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True)
    batch_no = Column(String(40), unique=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    produced_at = Column(DateTime, default=datetime.now)
    note = Column(String(256), default="")

    product = relationship("Product", back_populates="batches")
    labels = relationship("Label", back_populates="batch")


class Label(Base):
    """称重标签。打印时把商品名、单价、过敏原全部快照，价格以后调价不影响历史。"""
    __tablename__ = "labels"

    id = Column(Integer, primary_key=True)
    barcode = Column(String(40), unique=True, nullable=False)  # 唯一条码
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)

    net_weight = Column(Float, nullable=False)          # 净重 kg
    unit_price = Column(Float, nullable=False)          # 打印时单价快照
    total_price = Column(Float, nullable=False)         # 打印时金额快照
    allergen_snapshot = Column(String(256), nullable=False, default="")
    product_name = Column(String(64), nullable=False)   # 名称快照

    status = Column(String(16), nullable=False, default="active")
    # active 有效 / void 已注销（重贴）/ consumed 已结账 /
    # disputed 结账后发现贴错（冻结，等待主管处理）

    reprinted_from_id = Column(Integer, ForeignKey("labels.id"), nullable=True)
    voided_at = Column(DateTime, nullable=True)
    void_reason = Column(String(256), default="")
    printed_at = Column(DateTime, default=datetime.now)

    product = relationship("Product", back_populates="labels")
    batch = relationship("Batch", back_populates="labels")
    reprinted_from = relationship("Label", remote_side=[id], backref="reprints")


class Order(Base):
    """交易订单。每行订单项保存成交时的单价/金额，商品之后调价不影响原订单。"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    order_no = Column(String(40), unique=True, nullable=False)
    cashier = Column(String(32), nullable=False, default="cashier-01")
    register_no = Column(String(16), nullable=False, default="R1")  # 收银机号（扫码枪队列）
    status = Column(String(16), nullable=False, default="open")
    # open 未付款（挂单）/ paid 已结账 / refunded 整单已退
    created_at = Column(DateTime, default=datetime.now)
    paid_at = Column(DateTime, nullable=True)

    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    corrections = relationship("CorrectionRequest", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    label_id = Column(Integer, ForeignKey("labels.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    product_name = Column(String(64), nullable=False)     # 成交快照
    unit_price = Column(Float, nullable=False)            # 成交单价快照
    net_weight = Column(Float, nullable=False)
    final_price = Column(Float, nullable=False)           # 实收金额快照
    batch_no = Column(String(40), nullable=False)
    allergens_snapshot = Column(String(256), default="")

    order = relationship("Order", back_populates="items")
    label = relationship("Label")
    product = relationship("Product")


class CorrectionRequest(Base):
    """纠错工单。

    pre_payment_reprint：付款前发现贴错 → 重新称重注销旧码（系统直接执行，留痕）
    post_payment_adjust：结账后发现贴错 → 收银员只能发起，主管核对后
                         决定 charge 补收 / refund 退款，原金额与原因永久保留。
    """
    __tablename__ = "correction_requests"

    id = Column(Integer, primary_key=True)
    req_no = Column(String(40), unique=True, nullable=False)
    kind = Column(String(32), nullable=False)
    # pre_payment_reprint / post_payment_adjust
    status = Column(String(16), nullable=False, default="pending")
    # pending 待主管审批 / approved 批准 / rejected 驳回 / done 已完成收退款
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    old_label_id = Column(Integer, ForeignKey("labels.id"), nullable=True)
    new_label_id = Column(Integer, ForeignKey("labels.id"), nullable=True)

    # 主管核对实物后记录的"实物实际信息"（结账后不能直接换商品，只记核对结果）
    correct_product_name = Column(String(64), nullable=True)
    correct_weight = Column(Float, nullable=True)
    correct_allergens = Column(String(256), nullable=True)

    reason = Column(String(256), nullable=False)
    allergen_concern = Column(Boolean, default=False)  # 涉及过敏原

    requested_by = Column(String(32), nullable=False, default="cashier-01")
    requested_at = Column(DateTime, default=datetime.now)
    reviewed_by = Column(String(32), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(String(256), default="")

    # 结账后差额处理
    original_amount = Column(Float, nullable=True)   # 原交易金额（保留，不抹掉）
    correct_amount = Column(Float, nullable=True)    # 按实物应为金额
    diff_amount = Column(Float, nullable=True)       # 差额：正=补收，负=退款
    adjust_type = Column(String(16), nullable=True)  # charge / refund
    completed_at = Column(DateTime, nullable=True)

    order = relationship("Order", back_populates="corrections")
    old_label = relationship("Label", foreign_keys=[old_label_id])
    new_label = relationship("Label", foreign_keys=[new_label_id])
    related_labels = relationship("Label", secondary=correction_labels)


class Adjustment(Base):
    """差额收/退款台账：每笔补收或退款都与原交易、纠错单挂钩，可逐笔核对。"""
    __tablename__ = "adjustments"

    id = Column(Integer, primary_key=True)
    correction_id = Column(Integer, ForeignKey("correction_requests.id"), nullable=False)
    order_no = Column(String(40), nullable=False)
    direction = Column(String(8), nullable=False)       # charge 补收 / refund 退款
    amount = Column(Float, nullable=False)
    original_amount = Column(Float, nullable=False)
    handled_by = Column(String(32), nullable=False)
    handled_at = Column(DateTime, default=datetime.now)
    note = Column(String(256), default="")
