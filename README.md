# 散装食品称重标签错贴纠正系统

超市散装食品（坚果、糕点、杂粮等）称重标签贴错的**发现—拦截—纠正—追溯**全流程系统。
后端 Python + FastAPI + SQLAlchemy + SQLite，前端 Vue 3（本地 vendor，无需联网构建），
扫码枪与收银回执由本地模拟接口提供，**一条命令启动**。

## 快速开始

```bash
./run.sh                 # 自动装依赖(首次) + 初始化演示数据 + 启动
# RESET=1 ./run.sh       # 清空数据重来
```

- 前端：<http://127.0.0.1:8000/static/index.html>
- 接口文档：<http://127.0.0.1:8000/docs>
- 端到端演示（命令行展示三大场景）：

```bash
cd backend && python3 demo.py
```

## 业务规则如何落地

| 要求 | 实现 |
|---|---|
| 标签唯一条码，显示商品/净重/批次/价格/过敏原 | `POST /api/labels`，条码 `LB+uuid`，打印时把商品名、单价、过敏原**快照**写入标签 |
| 付款前贴错：重新称重、注销旧码 | `POST /api/labels/void-and-reprint`：旧码置 `void` 并留注销原因/时间，新码 `reprinted_from` 指向旧码，生成留痕工单 |
| 收银扫到作废码必须拦截 | `POST /api/orders/{id}/scan`：`void`/`consumed`/`disputed`/重复扫描一律 **409** 拦截 |
| 交易保存成交价，之后调价不影响原订单 | 结账行 `OrderItem` 存成交单价/金额/过敏原快照；调价只改商品当前价并写 `PriceChange` 轨迹 |
| 已结账不能直接换货 | 标签状态机 `active→consumed`，纠错不改原订单任何金额与商品 |
| 收银员只能发起，主管核对后补收/退款 | 收银员 `POST /api/corrections`（pending）；审批与完成接口强制请求头 `X-Role: supervisor`，否则 403 |
| 保留原金额与纠错原因 | `CorrectionRequest.original_amount / correct_amount / diff_amount / reason` 永久保留；原订单金额不动，差额单独入 `Adjustment` 台账 |
| 过敏原同批次追溯 | `GET /api/trace/batch/{批次号}`、`GET /api/trace/allergen/{关键词}` 查同批次/含过敏原的已售订单 |
| 差额收退款与原交易核对 | 完成接口校验订单已付款且标签原收金额与台账一致，否则拒绝收退款 |
| 扫码 / 回执本地模拟 | `POST /api/scan/trigger` + `GET /api/scan/poll`（按收银机号的内存队列）；回执写入 `backend/data/receipts/`（文本 + JSONL） |

## 三大演示场景

1. **作废标签重扫**：称重台打印 → 注销重贴 → 扫码枪扫旧码，收银台 409 拦截「⛔ 作废标签禁止扫描」→ 新码正常入单结账。
2. **调价后原价查询**：花生酥糖 25.80 → 29.90，已结账订单仍显示成交单价 25.80、金额 7.74；「订单原价」页对比成交单价与当前单价，并可查调价轨迹。
3. **结账后差额处理**：小馒头 ¥10.00 实为核桃仁 → 收银员发起（补收 ¥9.80，收银员自行审批被 403）→ 主管批准并与原交易核对后补收；另一笔花生酥糖实为大米 → 退款 ¥4.54。原订单金额保持不变，差额进入台账，过敏原事件可按批次/成分追回订单。

## 页面（顶栏切换身份：收银员 / 主管）

- **⚖️ 称重台**：选商品批次称重打印标签（贴纸样式，含条码/过敏原）、注销旧码重贴
- **🧾 收银台**：模拟扫码枪触发/轮询、扫标签入单（作废码红条拦截）、结账、回执
- **📝 纠错审批**：收银员选已结账订单与贴错标签行发起；主管批准/驳回、办理补收退款
- **📚 订单原价**：成交单价快照 vs 当前价、调价轨迹
- **🥜 商品批次**：商品/过敏原/单价维护、主管调价、批次管理
- **🔬 追溯台账**：同批次/过敏原订单追溯、差额收退款台账

## 目录结构

```
run.sh                  一条命令启动
backend/
  app/main.py           全部 API（称重/扫码/收银/纠错/追溯）
  app/models.py         Product/Batch/Label/Order/OrderItem/CorrectionRequest/Adjustment/PriceChange
  app/devices.py        扫码枪队列 + 回执本地打印模拟
  app/schemas.py        请求/响应模型
  seed.py               演示数据
  demo.py               三大场景端到端脚本（独立 demo.db，可反复运行）
frontend/
  index.html            Vue 3 单页应用（六个工作台）
  styles.css
  vendor/vue.global.prod.js
```

## 主要 API

```
POST /api/labels                         称重打印
POST /api/labels/void-and-reprint        注销旧码重贴
POST /api/scan/trigger|GET /api/scan/poll   扫码枪模拟
POST /api/orders .../scan .../checkout .../receipt
PUT  /api/products/{id}/price            主管调价（X-Role: supervisor）
POST /api/corrections                    收银员发起纠错
POST /api/corrections/{id}/review        主管审批
POST /api/corrections/{id}/complete      主管办理补收/退款（核对原交易）
GET  /api/trace/batch/{no} | /api/trace/allergen/{kw}
GET  /api/adjustments                    差额台账
```
