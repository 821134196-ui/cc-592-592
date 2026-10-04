"""Pydantic 请求 / 响应模式。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------- 基础 ----------
class AllergenOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    unit: str
    unit_price: float
    active: bool
    allergens: List[AllergenOut] = []

    class Config:
        from_attributes = True


class BatchOut(BaseModel):
    id: int
    batch_no: str
    product_id: int

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str

    class Config:
        from_attributes = True


# ---------- 称重 ----------
class WeighRequest(BaseModel):
    product_id: int
    batch_id: int
    net_weight: float = Field(gt=0)
    user_id: int
    reprint_of_barcode: Optional[str] = None  # 补打（付款前注销旧码后打新码）


class VoidRequest(BaseModel):
    barcode: str
    user_id: int
    reason: str = ""


class LabelOut(BaseModel):
    id: int
    barcode: str
    product_id: int
    batch_id: int
    product_name: str
    batch_no: str
    net_weight: float
    unit_price: float
    amount: float
    allergens: List[str]
    status: str
    printed_at: datetime
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    reprint_of_barcode: Optional[str] = None


# ---------- 收银 ----------
class ScanRequest(BaseModel):
    txn_no: Optional[str] = None  # 不传则新开交易
    barcode: str
    cashier_id: int


class TxnItemOut(BaseModel):
    id: int
    label_barcode: str
    product_name: str
    batch_no: str
    unit_price: float
    net_weight: float
    amount: float
    allergens: List[str]


class TxnOut(BaseModel):
    txn_no: str
    status: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    cashier_name: str
    total_amount: float
    items: List[TxnItemOut]


class PayRequest(BaseModel):
    txn_no: str
    cashier_id: int


# ---------- 调价 ----------
class PriceChangeRequest(BaseModel):
    product_id: int
    new_price: float = Field(ge=0)
    user_id: int


class PriceChangeOut(BaseModel):
    product_id: int
    product_name: str
    old_price: float
    new_price: float
    changed_at: datetime

    class Config:
        from_attributes = True


# ---------- 纠错 ----------
class CorrectionCreate(BaseModel):
    txn_no: str
    label_barcode: str
    cashier_id: int
    reason: str = Field(min_length=2)


class CorrectionReview(BaseModel):
    correction_no: str
    supervisor_id: int
    correct_product_id: int
    correct_weight: float = Field(gt=0)
    decision: str  # charge / refund / none
    supervisor_note: str = ""


class CorrectionOut(BaseModel):
    correction_no: str
    txn_no: str
    label_barcode: str
    product_name: str
    batch_no: str
    original_amount: float
    reason: str
    status: str
    requested_by_name: str
    requested_at: datetime
    correct_product_name: Optional[str] = None
    correct_weight: Optional[float] = None
    correct_unit_price: Optional[float] = None
    correct_amount: Optional[float] = None
    diff_amount: Optional[float] = None
    decision: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    supervisor_note: Optional[str] = None
