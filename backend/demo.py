"""端到端演示脚本：三大必展场景
  1) 作废标签重扫 —— 旧码注销，收银扫描作废码被拦截，新码正常入单
  2) 商品调价后的原价格查询 —— 订单永久保存成交时价格
  3) 结账后的差额处理 —— 收银员只能发起，主管核对后补收/退款，原金额保留
另含：扫码枪本地模拟、收银回执、同批次过敏原追溯、差额台账。

用法：python demo.py        （使用独立的 data/demo.db，可反复运行）
"""
import json
import os
import sys

# 使用独立演示库，开始前删掉重来 —— 保证脚本可重复执行
os.environ["LABEL_DB"] = os.path.join(os.path.dirname(__file__), "data", "demo.db")
os.environ["RECEIPT_DIR"] = os.path.join(os.path.dirname(__file__), "data", "receipts_demo")
if os.path.exists(os.environ["LABEL_DB"]):
    os.remove(os.environ["LABEL_DB"])

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
import seed  # noqa: E402

seed.run()
cli = TestClient(app)

CASHIER = {"X-Role": "cashier"}
SUPERVISOR = {"X-Role": "supervisor"}


def banner(t):
    print("\n" + "=" * 70)
    print("  " + t)
    print("=" * 70)


def show(label, resp, expect=None):
    flag = ""
    if expect is not None:
        ok = resp.status_code == expect
        flag = ("  ✅" if ok else f"  ❌ 期望 {expect}")
    print(f"\n── {label}  [HTTP {resp.status_code}]{flag}")
    try:
        data = resp.json()
        print(json.dumps(data, ensure_ascii=False, indent=2)[:1600])
    except Exception:
        print(resp.text[:1600])
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None


# ---------------------------------------------------------------------------
banner("场景 1：付款前贴错标签 → 注销旧码重贴 → 作废码重扫被拦截")
# ---------------------------------------------------------------------------
products = cli.get("/api/products").json()
walnut = next(p for p in products if p["name"] == "原味核桃仁")
batches = cli.get("/api/batches").json()
w_batch = next(b for b in batches if b["product_name"] == "原味核桃仁")

lab = show("称重台打印标签：核桃仁 0.400kg @39.60",
           cli.post("/api/labels", json={
               "product_id": walnut["id"], "batch_id": w_batch["id"], "net_weight": 0.400}))
old_code = lab["barcode"]

rep = show("发现商品贴错：注销旧码并重新称重打印",
           cli.post("/api/labels/void-and-reprint",
                    json={"old_barcode": old_code, "reason": "袋装核桃仁与花生酥糖串袋，贴错商品"}))
new_code = rep["new_barcode"]

order = show("收银员开单", cli.post("/api/orders", json={"cashier": "cashier-01"}))
oid = order["id"]

# 走本地扫码枪模拟接口：硬件把条码推进队列，收银端轮询取出
cli.post(f"/api/scan/trigger?register_no=R1", json={"barcode": old_code})
gun = cli.get("/api/scan/poll?register_no=R1").json()
show(f"扫码枪扫到已注销旧码 {old_code} → 收银拦截",
     cli.post(f"/api/orders/{oid}/scan", json={"barcode": gun["barcode"]}), expect=409)

show(f"扫描重贴后的新码 {new_code} → 正常入单",
     cli.post(f"/api/orders/{oid}/scan", json={"barcode": new_code}), expect=200)

# 再扫入一粒同批次核桃仁（为后面的批次追溯制造数据）
seed_labels = cli.get("/api/labels?limit=20").json()
w_active = next(l for l in seed_labels
                if l["product_name"] == "原味核桃仁" and l["status"] == "active"
                and l["barcode"] != new_code)
cli.post(f"/api/orders/{oid}/scan", json={"barcode": w_active["barcode"]})

o1 = show("结账（订单 A）", cli.post(f"/api/orders/{oid}/checkout"))
print("\n──── 本地收银回执（模拟热敏打印）────")
print(cli.get(f"/api/orders/{oid}/receipt").text)

# ---------------------------------------------------------------------------
banner("场景 2：商品调价后，原订单仍按成交价可查")
# ---------------------------------------------------------------------------
peanut = next(p for p in products if p["name"] == "花生酥糖")
lp = next(l for l in seed_labels if l["product_name"] == "花生酥糖" and l["status"] == "active")
print(f"\n标签打印时单价快照：¥{lp['unit_price']:.2f}/kg（条码 {lp['barcode']}）")

order2 = cli.post("/api/orders", json={"cashier": "cashier-01"}).json()
cli.post(f"/api/orders/{order2['id']}/scan", json={"barcode": lp["barcode"]})

# 收银员尝试调价 → 403
show("收银员尝试调价 → 权限拦截",
     cli.put(f"/api/products/{peanut['id']}/price",
             json={"unit_price": 29.9, "reason": "市场价上调"}, headers=CASHIER), expect=403)
# 主管调价
show("主管调价：花生酥糖 25.80 → 29.90",
     cli.put(f"/api/products/{peanut['id']}/price",
             json={"unit_price": 29.9, "reason": "10 月市场价上调"}, headers=SUPERVISOR))

o2 = show("结账（订单 B，按标签成交价结算）",
          cli.post(f"/api/orders/{order2['id']}/checkout"))
line = o2["items"][0]
print(f"\n  商品当前单价        ：¥29.90/kg")
print(f"  订单 B 成交单价快照 ：¥{line['unit_price']:.2f}/kg")
print(f"  订单 B 成交金额     ：¥{line['final_price']:.2f}（0.300kg）")
assert line["unit_price"] == 25.8, "成交价被调价污染！"
print("  ✅ 调价不影响历史订单，价格有据可查")
show("价格变更轨迹", cli.get(f"/api/products/{peanut['id']}/price-history"))

# ---------------------------------------------------------------------------
banner("场景 3：结账后发现贴错 → 收银员发起 → 主管核对 → 补收 / 退款")
# ---------------------------------------------------------------------------
bun = next(p for p in products if p["name"] == "奶香小馒头")
rice = next(p for p in products if p["name"] == "散装大米")
lb = next(l for l in seed_labels if l["product_name"] == "奶香小馒头" and l["status"] == "active")

# 订单 C：按小馒头标签结账 10.00 元，实物实为核桃仁
order3 = cli.post("/api/orders", json={"cashier": "cashier-01"}).json()
cli.post(f"/api/orders/{order3['id']}/scan", json={"barcode": lb["barcode"]})
cli.post(f"/api/orders/{order3['id']}/checkout")

corr = show("收银员发起纠错：标签是小馒头 ¥10.00，实物为核桃仁 0.500kg（涉及坚果过敏原）",
            cli.post("/api/corrections", json={
                "order_id": order3["id"], "old_label_id": lb["id"],
                "correct_product_id": walnut["id"], "correct_weight": 0.500,
                "reason": "顾客反映袋内为核桃仁，贴成小馒头标签",
                "allergen_concern": True, "requested_by": "cashier-01"},
                    headers=CASHIER))
cid = corr["id"]
print(f"\n  原收金额 ¥{corr['original_amount']:.2f} → 应收 ¥{corr['correct_amount']:.2f}"
      f"，应补收 ¥{corr['diff_amount']:.2f}（{corr['adjust_type']}）")

show("普通收银员尝试审批 → 403 拦截",
     cli.post(f"/api/corrections/{cid}/review",
              json={"approve": True, "review_note": "我自己批了", "reviewer": "cashier-01"},
              headers=CASHIER), expect=403)

show("主管核对原标签与实物后批准",
     cli.post(f"/api/corrections/{cid}/review",
              json={"approve": True, "review_note": "已核对：实物确为核桃仁 0.50kg，同意补收",
                    "reviewer": "supervisor-01"}, headers=SUPERVISOR))
show("主管办理补收款（与原交易核对一致后入账）",
     cli.post(f"/api/corrections/{cid}/complete",
              json={"reviewer": "supervisor-01"}, headers=SUPERVISOR))

# 订单 D：花生酥糖 7.74 元，实物为大米 0.5kg → 退款 4.54
order4 = o2  # 订单 B 的花生酥糖行
lp_row = cli.get("/api/orders", params={"status": "paid"}).json()
o4 = next(o for o in lp_row if o["order_no"] == order4["order_no"])
corr2 = show("收银员发起第二笔纠错：花生酥糖 ¥7.74，实物为大米 0.500kg（花生过敏原风险）",
             cli.post("/api/corrections", json={
                 "order_id": o4["id"], "old_label_id": lp["id"],
                 "correct_product_id": rice["id"], "correct_weight": 0.500,
                 "reason": "袋装实为大米，标签误贴花生酥糖，需追回顾客告知过敏原",
                 "allergen_concern": True}, headers=CASHIER))
print(f"\n  原收 ¥{corr2['original_amount']:.2f} → 应收 ¥{corr2['correct_amount']:.2f}"
      f"，应退款 ¥{abs(corr2['diff_amount']):.2f}")
cli.post(f"/api/corrections/{corr2['id']}/review",
         json={"approve": True, "review_note": "监控核实串袋，同意退款",
               "reviewer": "supervisor-01"}, headers=SUPERVISOR)
show("主管办理退款",
     cli.post(f"/api/corrections/{corr2['id']}/complete",
              json={"reviewer": "supervisor-01"}, headers=SUPERVISOR))

# ---------------------------------------------------------------------------
banner("核对结果：原订单金额保留不动 + 过敏原同批次追溯 + 差额台账")
# ---------------------------------------------------------------------------
oc = cli.get(f"/api/orders/{order3['id']}").json()
ob = cli.get(f"/api/orders/{o4['id']}").json()
print(f"\n  订单 C 原金额仍为 ¥{oc['total']:.2f}（补收 ¥9.80 另记台账，不覆盖原交易）")
print(f"  订单 B 原金额仍为 ¥{ob['total']:.2f}（退款 ¥4.54 另记台账）")
assert oc["total"] == 10.0 and ob["total"] == 7.74

show("同批次追溯 B20261001-01（核桃仁批次可能受过敏原事件影响的订单）",
     cli.get("/api/trace/batch/B20261001-01"))
show("按过敏原“花生”追溯已售订单", cli.get("/api/trace/allergen/花生"))
show("差额收/退款台账（逐笔与原交易挂钩）", cli.get("/api/adjustments"))

print("\n演示完成。可执行 ./run.sh 启动服务，浏览器打开 http://127.0.0.1:8000/static/index.html")
