"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------- 商品 / 批次 ----------
class ProductIn(BaseModel):
    sku: str
    name: str
    unit_price: float = Field(ge=0)
    allergens: str = ""


class ProductPriceIn(BaseModel):
    unit_price: float = Field(ge=0)
    reason: str = ""


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    unit_price: float
    allergens: str
    active: bool

    class Config:
        from_attributes = True


class BatchIn(BaseModel):
    batch_no: str
    product_id: int
    note: str = ""


class BatchOut(BaseModel):
    id: int
    batch_no: str
    product_id: int
    note: str
    product_name: str = ""

    class Config:
        from_attributes = True


# ---------- 称重标签 ----------
class LabelIn(BaseModel):
    product_id: int
    batch_id: int
    net_weight: float = Field(gt=0)


class ReprintIn(BaseModel):
    old_barcode: str
    reason: str = "付款前发现标签贴错，重新称重"


class LabelOut(BaseModel):
    id: int
    barcode: str
    product_id: int
    product_name: str
    batch_no: str = ""
    net_weight: float
    unit_price: float
    total_price: float
    allergen_snapshot: str
    status: str
    printed_at: Optional[datetime] = None
    voided_at: Optional[datetime] = None
    void_reason: str = ""
    reprinted_from_barcode: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- 扫码 / 收银 ----------
class ScanRequest(BaseModel):
    """本地扫码枪模拟：也支持直接推条码。"""
    barcode: str


class CheckoutIn(BaseModel):
    cashier: str = "cashier-01"


class OrderCreate(BaseModel):
    cashier: str = "cashier-01"
    register_no: str = "R1"


class ScanBody(BaseModel):
    barcode: str


class OrderItemOut(BaseModel):
    id: int
    label_id: int
    product_name: str
    batch_no: str
    net_weight: float
    unit_price: float
    final_price: float
    allergens_snapshot: str

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    order_no: str
    cashier: str
    status: str
    created_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    items: List[OrderItemOut] = []
    total: float = 0.0


# ---------- 纠错 ----------
class CorrectionCreate(BaseModel):
    """普通收银员发起：结账后发现贴错。"""
    order_id: int
    old_label_id: int
    correct_product_id: int
    correct_weight: float = Field(gt=0)
    reason: str
    allergen_concern: bool = False
    requested_by: str = "cashier-01"


class CorrectionReview(BaseModel):
    """主管审批：批准（生成补收/退款）或驳回。"""
    approve: bool
    review_note: str = ""
    reviewer: str = "supervisor-01"


class CorrectionComplete(BaseModel):
    """主管完成补收款 / 退款，与原交易核对。"""
    reviewer: str = "supervisor-01"


class CorrectionOut(BaseModel):
    id: int
    req_no: str
    kind: str
    status: str
    order_no: Optional[str] = None
    old_barcode: Optional[str] = None
    new_barcode: Optional[str] = None
    reason: str
    allergen_concern: bool
    requested_by: str
    requested_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_note: str = ""
    original_amount: Optional[float] = None
    correct_amount: Optional[float] = None
    diff_amount: Optional[float] = None
    adjust_type: Optional[str] = None


class TraceOrderOut(BaseModel):
    """同批次过敏原追溯命中的订单。"""
    order_no: str
    status: str
    paid_at: Optional[datetime] = None
    product_name: str
    batch_no: str
    final_price: float
    allergens_snapshot: str
