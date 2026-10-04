"""端到端演示：作废标签重扫拦截 / 调价后原价查询 / 结账后差额处理。

使用独立的演示库 data/demo.db，每次运行重建，不污染正式库。
运行：./run.sh demo   （或 .venv/bin/python scripts/demo.py）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SCALE_DB"] = os.path.join(os.getcwd(), "data", "demo.db")
if os.path.exists(os.environ["SCALE_DB"]):
    os.remove(os.environ["SCALE_DB"])

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402
from backend.seed import run as seed_run  # noqa: E402

seed_run()
api = TestClient(app)

CASHIER, SUPERVISOR = 1, 2
OK = "\033[92m✔\033[0m"
NO = "\033[91m✘\033[0m"


def banner(t):
    print("\n" + "═" * 64)
    print(f"  {t}")
    print("═" * 64)


def call(method, path, *, expect=200, **kw):
    r = getattr(api, method)(path, **kw)
    tag = OK if r.status_code == expect else NO
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code != expect:
        print(f"{tag} {method.upper()} {path} 期望 {expect} 实际 {r.status_code}")
        print("   响应:", body)
        raise SystemExit(1)
    return body


def batch_of(pid):
    return call("get", f"/api/products/{pid}/batches")[0]["id"]


# 商品：1=花生糖(39.80,花生/麸质) 2=曲奇(56.00,乳制品/麸质)
#       3=虾片(78.50,甲壳类)      4=果冻(12.90)
B1, B2, B3, B4 = (batch_of(i) for i in (1, 2, 3, 4))

# ─────────────────────────────────────────────────────────────
banner("场景 1：付款前贴错 → 重新称重注销旧码 → 作废码重扫被拦截")
# ─────────────────────────────────────────────────────────────
wrong = call("post", "/api/device/print-label", json={
    "product_id": 1, "batch_id": B1, "net_weight": 0.80, "user_id": CASHIER})
print("【标签打印机输出】")
print(wrong)
wrong_data = call("get", "/api/labels/recent")[0]
old_barcode = wrong_data["barcode"]
print(f"\n顾客发现净重应为 0.75kg，称重台重新称重并补打（旧码自动注销）…")
new = call("post", "/api/weigh", expect=201, json={
    "product_id": 1, "batch_id": B1, "net_weight": 0.75,
    "user_id": CASHIER, "reprint_of_barcode": old_barcode})
print(f"{OK} 新标签 {new['barcode']}  净重 {new['net_weight']}kg  "
      f"金额 {new['amount']:.2f} 元，旧码 {old_barcode} 已注销")
print(f"{OK} 新标签过敏原随打印快照：{'、'.join(new['allergens'])}")

print("\n收银员扫描已注销的旧码：")
blocked = call("post", "/api/txn/scan", expect=409, json={
    "barcode": old_barcode, "cashier_id": CASHIER})
print(f"{OK} 收银端拦截：{blocked['detail']}")

txn = call("post", "/api/txn/scan", expect=201, json={
    "barcode": new["barcode"], "cashier_id": CASHIER})
print(f"{OK} 扫描新码成功入账：订单 {txn['txn_no']} 合计 {txn['total_amount']:.2f} 元")

r = api.post("/api/txn/scan", json={"barcode": new["barcode"],
                                    "cashier_id": CASHIER, "txn_no": txn["txn_no"]})
print(f"{OK} 同一标签重复扫描同样拦截：{r.json()['detail']}")

paid_a = call("post", "/api/txn/pay", json={
    "txn_no": txn["txn_no"], "cashier_id": CASHIER})
print(f"{OK} 订单结账成功，成交金额冻结：{paid_a['total_amount']:.2f} 元")

# ─────────────────────────────────────────────────────────────
banner("场景 2：商品事后调价，原订单仍按成交价查询、小票仍打原价")
# ─────────────────────────────────────────────────────────────
# 先再造一单含曲奇的已结账订单
ck = call("post", "/api/weigh", expect=201, json={
    "product_id": 2, "batch_id": B2, "net_weight": 0.5, "user_id": CASHIER})
t2 = call("post", "/api/txn/scan", expect=201, json={
    "barcode": ck["barcode"], "cashier_id": CASHIER})
t2 = call("post", "/api/txn/pay", json={
    "txn_no": t2["txn_no"], "cashier_id": CASHIER})
print(f"调价前成交：曲奇 0.5kg × 56.00 = {t2['total_amount']:.2f} 元（订单 {t2['txn_no']}）")

call("post", "/api/products/price-change", expect=201, json={
    "product_id": 2, "new_price": 62.00, "user_id": SUPERVISOR})
print("主管将黄油曲奇单价 56.00 → 62.00 元/kg（调价已留痕）")

q = call("get", f"/api/txn/{t2['txn_no']}")
row = q["items"][0]
print(f"{OK} 查询原订单：单价仍为 {row['unit_price']:.2f}（现价仅参考 {row['current_unit_price']:.2f}），"
      f"金额仍为 {row['amount']:.2f}，订单总额 {q['total_amount']:.2f} 未变")
receipt = call("get", f"/api/txn/{t2['txn_no']}/receipt")
print("\n【小票打印机补打】")
print(receipt)

ck2 = call("post", "/api/weigh", expect=201, json={
    "product_id": 2, "batch_id": B2, "net_weight": 0.5, "user_id": CASHIER})
print(f"{OK} 调价后新称标签按新价：0.5kg × {ck2['unit_price']:.2f} = {ck2['amount']:.2f} 元")

# ─────────────────────────────────────────────────────────────
banner("场景 3：结账后发现贴错 → 收银员只能申请 → 主管核对差额补收/退款")
# ─────────────────────────────────────────────────────────────
def buy(product_id, batch_id, weight):
    lab = call("post", "/api/weigh", expect=201, json={
        "product_id": product_id, "batch_id": batch_id,
        "net_weight": weight, "user_id": CASHIER})
    t = call("post", "/api/txn/scan", expect=201, json={
        "barcode": lab["barcode"], "cashier_id": CASHIER})
    t = call("post", "/api/txn/pay", json={
        "txn_no": t["txn_no"], "cashier_id": CASHIER})
    return t, lab

# 订单X：标签是花生糖，顾客回家后发现实物其实是果冻（应退款）
tx, lx = buy(1, B1, 0.5)
# 订单Y：标签是花生糖，实物是虾片（应补收）
ty, ly = buy(1, B1, 0.3)
print(f"订单X {tx['txn_no']}：标签 花生糖 0.5kg 收 {tx['total_amount']:.2f} 元，实物疑似果冻")
print(f"订单Y {ty['txn_no']}：标签 花生糖 0.3kg 收 {ty['total_amount']:.2f} 元，实物疑似虾片")

cx = call("post", "/api/corrections", expect=201, json={
    "txn_no": tx["txn_no"], "label_barcode": lx["barcode"],
    "cashier_id": CASHIER, "reason": "顾客反馈实物为果冻，标签错贴成花生糖"})
cy = call("post", "/api/corrections", expect=201, json={
    "txn_no": ty["txn_no"], "label_barcode": ly["barcode"],
    "cashier_id": CASHIER, "reason": "顾客反馈实物为虾片，标签错贴成花生糖"})
print(f"{OK} 收银员发起纠错申请 {cx['correction_no']} / {cy['correction_no']}（状态 pending）")

# 收银员尝试自行审批 → 拒绝
r = api.post("/api/corrections/review", json={
    "correction_no": cx["correction_no"], "supervisor_id": CASHIER,
    "correct_product_id": 4, "correct_weight": 0.5, "decision": "refund"})
print(f"{OK} 普通收银员自行审批被拒：{r.status_code} {r.json()['detail']}")

# 主管核对试算（按原成交时点价格；即使此时花生糖调价，也不受影响）
px = call("post", "/api/corrections/preview?txn_no="
                  f"{tx['txn_no']}&label_barcode={lx['barcode']}"
                  "&correct_product_id=4&correct_weight=0.5")
print(f"\n主管核对X：原收 {px['original_amount']:.2f}（{px['original_product']}），"
      f"实物按成交时单价 {px['unit_price_at_txn_time']:.2f} 计算应收 "
      f"{px['correct_amount']:.2f}，差额 {px['diff_amount']:+.2f} → {px['suggested_decision']}")
py = call("post", "/api/corrections/preview?txn_no="
                  f"{ty['txn_no']}&label_barcode={ly['barcode']}"
                  "&correct_product_id=3&correct_weight=0.3")
print(f"主管核对Y：原收 {py['original_amount']:.2f}（{py['original_product']}），"
      f"实物按成交时单价 {py['unit_price_at_txn_time']:.2f} 计算应收 "
      f"{py['correct_amount']:.2f}，差额 {py['diff_amount']:+.2f} → {py['suggested_decision']}")

# 差额方向必须与原交易核对一致：退款单不能按补收核准
r = api.post("/api/corrections/review", json={
    "correction_no": cx["correction_no"], "supervisor_id": SUPERVISOR,
    "correct_product_id": 4, "correct_weight": 0.5, "decision": "charge"})
print(f"{OK} 收退方向与核对结果冲突时拦截：{r.json()['detail']}")

ax = call("post", "/api/corrections/review", json={
    "correction_no": cx["correction_no"], "supervisor_id": SUPERVISOR,
    "correct_product_id": 4, "correct_weight": 0.5,
    "decision": "refund", "supervisor_note": "已核对原标签与实物果冻一致，退款"})
ay = call("post", "/api/corrections/review", json={
    "correction_no": cy["correction_no"], "supervisor_id": SUPERVISOR,
    "correct_product_id": 3, "correct_weight": 0.3,
    "decision": "charge", "supervisor_note": "已核对原标签与实物虾片一致，补收"})
print(f"\n{OK} 核准退款单 {ax['correction_no']}：原金额 {ax['original_amount']:.2f} 保留，"
      f"退给顾客 {abs(ax['diff_amount']):.2f} 元，原因：{ax['reason']}")
print(f"{OK} 核准补收单 {ay['correction_no']}：原金额 {ay['original_amount']:.2f} 保留，"
      f"向顾客补收 {ay['diff_amount']:.2f} 元，原因：{ay['reason']}")

# 原订单金额始终不变
fx = call("get", f"/api/txn/{tx['txn_no']}")
fy = call("get", f"/api/txn/{ty['txn_no']}")
print(f"{OK} 原订单 {fx['txn_no']} 总额仍为 {fx['total_amount']:.2f}；"
      f"{fy['txn_no']} 总额仍为 {fy['total_amount']:.2f}（差额单独立账，不抹掉原交易）")

# 同批次过敏原追溯
tr = call("get", "/api/trace/batch/B20261003-07?allergen=花生")
print(f"\n过敏原追溯：批次 {tr['batch_no']}（{tr['product_name']}，含花生）可能受影响订单 "
      f"{len(tr['affected_orders'])} 笔：")
for o in tr["affected_orders"]:
    print(f"  · {o['txn_no']} [{o['status']}] {o['product_name']} "
          f"{o['net_weight']}kg {o['amount']:.2f} 元 过敏原:{'、'.join(o['allergens'])}")
print(f"  另有未售出/已注销标签 {len(tr['unsold_or_voided_labels'])} 张（含场景1注销的旧码）")

banner("三个场景全部通过")
print("启动界面版：./run.sh   →   http://127.0.0.1:8000")
