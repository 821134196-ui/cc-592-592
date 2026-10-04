"""初始化演示数据：商品、批次、几张称重标签。"""
from app.database import Base, SessionLocal, engine
from app import models


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(models.Product).count() > 0:
            print("数据库已有数据，跳过初始化。")
            return

        products = [
            models.Product(sku="SP001", name="原味核桃仁", unit_price=39.60, allergens="坚果"),
            models.Product(sku="SP002", name="花生酥糖", unit_price=25.80, allergens="花生"),
            models.Product(sku="SP003", name="奶香小馒头", unit_price=12.50, allergens="小麦,乳制品"),
            models.Product(sku="SP004", name="散装大米", unit_price=6.40, allergens=""),
        ]
        db.add_all(products)
        db.flush()

        b1 = models.Batch(batch_no="B20261001-01", product_id=products[0].id, note="国庆档")
        b2 = models.Batch(batch_no="B20261002-07", product_id=products[1].id, note="新到")
        b3 = models.Batch(batch_no="B20261003-03", product_id=products[2].id, note="")
        db.add_all([b1, b2, b3])
        db.flush()

        def mk(p, b, w):
            return models.Label(
                barcode="LB" + __import__("uuid").uuid4().hex[:16].upper(),
                product_id=p.id, batch_id=b.id, net_weight=w,
                unit_price=p.unit_price, total_price=round(w * p.unit_price, 2),
                allergen_snapshot=p.allergens, product_name=p.name,
            )

        db.add_all([
            mk(products[0], b1, 0.500),
            mk(products[1], b2, 0.300),
            mk(products[2], b3, 0.800),
        ])
        db.commit()
        print("演示数据已初始化：4 个商品、3 个批次、3 张标签。")
    finally:
        db.close()


if __name__ == "__main__":
    run()
