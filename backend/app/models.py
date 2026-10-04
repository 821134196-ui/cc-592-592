"""ORM 模型：商品、批次、称重标签、交易、调价、纠错、过敏原。"""
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


def now() -> datetime:
    return datetime.utcnow()


# 商品 <-> 过敏原 多对多（过敏原是商品的固有属性，随标签快照打印）
product_allergen = Table(
    "product_allergen",
    Base.metadata,
    Column("product_id", Integer, ForeignKey("products.id"), primary_key=True),
    Column("allergen_id", Integer, ForeignKey("allergens.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False)
    display_name = Column(String(64), nullable=False)
    # role: cashier=普通收银员, supervisor=主管
    role = Column(String(16), nullable=False, default="cashier")


class Allergen(Base):
    __tablename__ = "allergens"
    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)  # 花生 / 乳制品 / 麸质 ...


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    sku = Column(String(16), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    unit = Column(String(8), nullable=False, default="kg")  # 计价单位
    unit_price = Column(Float, nullable=False, default=0.0)  # 当前单价（元/kg）
    active = Column(Boolean, nullable=False, default=True)
    allergens = relationship("Allergen", secondary=product_allergen, lazy="joined")
    batches = relationship("Batch", back_populates="product")


class Batch(Base):
    """同批次：用于过敏原问题发生时追溯可能受影响的订单。"""

    __tablename__ = "batches"
    id = Column(Integer, primary_key=True)
    batch_no = Column(String(32), unique=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    produced_at = Column(DateTime, default=now)
    product = relationship("Product", back_populates="batches")
    labels = relationship("WeightLabel", back_populates="batch")


class WeightLabel(Base):
    """称重台打印的散装食品标签，条码全局唯一。"""

    __tablename__ = "weight_labels"
    id = Column(Integer, primary_key=True)
    barcode = Column(String(24), unique=True, nullable=False, index=True)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    net_weight = Column(Float, nullable=False)          # 净重
    unit_price = Column(Float, nullable=False)          # 打印时单价快照
    amount = Column(Float, nullable=False)              # 打印时金额快照
    allergens_snapshot = Column(String(256), default="")  # 打印时过敏原快照
    printed_at = Column(DateTime, default=now)
    printed_by = Column(Integer, ForeignKey("users.id"))

    # active=有效; voided=已注销(作废码); consumed=已被交易项使用
    status = Column(String(16), nullable=False, default="active")
    voided_at = Column(DateTime)
    voided_by = Column(Integer, ForeignKey("users.id"))
    void_reason = Column(String(256))
    reprint_of_id = Column(Integer, ForeignKey("weight_labels.id"))  # 补打来源

    product = relationship("Product")
    batch = relationship("Batch", back_populates="labels")


class Transaction(Base):
    """收银交易（一笔结账）。金额在结账时冻结，之后调价不影响本单。"""

    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    txn_no = Column(String(24), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=now)
    cashier_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # open=扫码进行中; paid=已结账
    status = Column(String(16), nullable=False, default="open")
    total_amount = Column(Float, nullable=False, default=0.0)  # 成交金额快照
    paid_at = Column(DateTime)

    cashier = relationship("User")
    items = relationship(
        "TransactionItem", back_populates="transaction", cascade="all, delete-orphan"
    )


class TransactionItem(Base):
    """交易行：保存成交时的价格快照，且与唯一标签一一对应。"""

    __tablename__ = "transaction_items"
    id = Column(Integer, primary_key=True)
    txn_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    label_id = Column(Integer, ForeignKey("weight_labels.id"), unique=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    product_name = Column(String(64), nullable=False)
    unit_price = Column(Float, nullable=False)       # 成交单价快照
    net_weight = Column(Float, nullable=False)       # 成交净重快照
    amount = Column(Float, nullable=False)           # 成交金额快照
    allergens_snapshot = Column(String(256), default="")

    transaction = relationship("Transaction", back_populates="items")
    label = relationship("WeightLabel")
    batch = relationship("Batch")


class PriceChange(Base):
    """商品调价记录：旧单按旧价，新标签按新价。"""

    __tablename__ = "price_changes"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    changed_at = Column(DateTime, default=now)
    changed_by = Column(Integer, ForeignKey("users.id"))


class Correction(Base):
    """结账后纠错：普通收银员只能发起(pending)，主管核对实物后审批。

    不直接换商品：按原标签与实物差额补收/退款，原金额与原因永久保留。
    """

    __tablename__ = "corrections"
    id = Column(Integer, primary_key=True)
    correction_no = Column(String(24), unique=True, nullable=False, index=True)
    txn_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("transaction_items.id"), nullable=False)
    label_id = Column(Integer, ForeignKey("weight_labels.id"), nullable=False)

    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime, default=now)
    reason = Column(Text, nullable=False)

    # 主管核对实物后填写
    correct_product_id = Column(Integer, ForeignKey("products.id"))
    correct_weight = Column(Float)
    correct_unit_price = Column(Float)   # 按“原成交时点”的价格核算差额
    correct_amount = Column(Float)
    diff_amount = Column(Float)          # 正=补收, 负=退款, 0=纠正不产生差额
    decision = Column(String(16))        # charge=补收, refund=退款, none=无需收退
    reviewed_by = Column(Integer, ForeignKey("users.id"))
    reviewed_at = Column(DateTime)
    supervisor_note = Column(Text)
    # pending=待主管审批; approved=已核准(差额已收退); rejected=驳回
    status = Column(String(16), nullable=False, default="pending")
