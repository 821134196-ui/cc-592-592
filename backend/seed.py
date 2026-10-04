"""初始化数据库并写入演示种子数据（幂等：存在数据则跳过）。

运行：.venv/bin/python -m backend.seed
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from .app.database import Base, SessionLocal, engine
from .app import models

Base.metadata.create_all(bind=engine)


def run():
    db = SessionLocal()
    try:
        if db.query(models.User).count() > 0:
            print("数据库已有数据，跳过种子初始化。")
            return

        # 人员：一名普通收银员、一名主管
        cashier = models.User(
            username="cashier01", display_name="收银员 小王", role="cashier")
        supervisor = models.User(
            username="mgr01", display_name="主管 李姐", role="supervisor")
        db.add_all([cashier, supervisor])
        db.flush()

        # 过敏原
        peanut, dairy, gluten, shell = (models.Allergen(name=n) for n in
                                        ("花生", "乳制品", "麸质", "甲壳类"))
        db.add_all([peanut, dairy, gluten, shell])
        db.flush()

        # 散装商品（单价 元/kg）
        p_peanut = models.Product(
            sku="SP001", name="散装花生糖", unit_price=39.80,
            allergens=[peanut, gluten])
        p_cookie = models.Product(
            sku="SP002", name="黄油曲奇(散称)", unit_price=56.00,
            allergens=[dairy, gluten])
        p_shrimp = models.Product(
            sku="SP003", name="现烤虾片(散称)", unit_price=78.50,
            allergens=[shell])
        p_jelly = models.Product(
            sku="SP004", name="果冻(散称)", unit_price=12.90, allergens=[])
        db.add_all([p_peanut, p_cookie, p_shrimp, p_jelly])
        db.flush()

        # 批次
        b1 = models.Batch(batch_no="B20261003-07", product_id=p_peanut.id)
        b2 = models.Batch(batch_no="B20261003-08", product_id=p_cookie.id)
        b3 = models.Batch(batch_no="B20261004-01", product_id=p_shrimp.id)
        b4 = models.Batch(batch_no="B20261004-02", product_id=p_jelly.id)
        db.add_all([b1, b2, b3, b4])
        db.commit()

        print("种子数据完成：")
        print("  用户 cashier01(收银员) / mgr01(主管)，无需密码，页面顶部切换")
        for p in (p_peanut, p_cookie, p_shrimp, p_jelly):
            print(f"  商品 {p.sku} {p.name} {p.unit_price} 元/kg")
    finally:
        db.close()


if __name__ == "__main__":
    run()
