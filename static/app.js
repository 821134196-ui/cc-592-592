/* 散装食品称重标签错贴纠正系统 — Vue3 单页前端 */
const { createApp } = Vue;

createApp({
  data() {
    return {
      users: [], userId: null,
      tab: 'weigh',
      tabs: [
        { k: 'weigh', label: '称重台' },
        { k: 'pos', label: '收银台' },
        { k: 'orders', label: '订单 / 过敏原追溯' },
        { k: 'price', label: '商品调价' },
        { k: 'correction', label: '纠错审批' },
      ],
      toasts: [], toastSeq: 1,

      products: [],
      batchesOfProduct: [],
      allBatches: [],
      labels: [],
      weigh: { product_id: null, batch_id: null, net_weight: '', reprint_of_barcode: null },
      lastLabelPaper: '',

      scanCode: '',
      currentTxn: null,
      receiptText: '',

      txns: [], txnQuery: '', selectedTxnNo: null, selectedTxn: null,
      trace: { batch_no: '', allergen: '' }, traceResult: null,

      priceDraft: {}, historyMap: {},

      corrections: [], corrFilter: 'pending',
      correcting: null, correctionReason: '',
      reviewing: null,
      review: { correct_product_id: null, correct_weight: null, decision: 'charge', supervisor_note: '' },
      preview: null,
    };
  },
  computed: {
    user() { return this.users.find(u => u.id === this.userId) || null; },
    isSupervisor() { return this.user && this.user.role === 'supervisor'; },
    canWeigh() {
      return this.weigh.product_id && this.weigh.batch_id &&
             parseFloat(this.weigh.net_weight) > 0;
    },
  },
  async mounted() {
    await this.loadUsers();
    this.userId = Number(localStorage.getItem('scale_uid')) || (this.users[0] && this.users[0].id);
    await Promise.all([this.loadProducts(), this.loadLabels(), this.loadAllBatches(),
                       this.loadTxns(), this.loadCorrections()]);
  },
  methods: {
    // ---------- 通用 ----------
    async api(method, url, body) {
      const opt = { method, headers: {} };
      if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
      const r = await fetch(url, opt);
      const ct = r.headers.get('content-type') || '';
      const data = ct.includes('application/json') ? await r.json() : await r.text();
      if (!r.ok) throw new Error(typeof data === 'string' ? data : (data.detail || `请求失败 ${r.status}`));
      return data;
    },
    toast(msg, type = 'info') {
      const id = this.toastSeq++;
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 4200);
    },
    fmt(t) { return t ? t.replace('T', ' ').slice(0, 19) : ''; },
    statusText(s) { return { active: '有效', voided: '已作废', consumed: '已售出' }[s] || s; },
    corrStatusText(s) { return { pending: '待审批', approved: '已核准', rejected: '已驳回' }[s] || s; },
    corrStatusClass(s) { return s; },

    async loadUsers() { this.users = await this.api('get', '/api/users'); },
    onUserChange() { localStorage.setItem('scale_uid', this.userId); this.toast('已切换操作人员', 'info'); },

    async loadProducts() {
      this.products = await this.api('get', '/api/products');
      this.products.forEach(p => { if (this.priceDraft[p.id] === undefined) this.priceDraft[p.id] = ''; });
    },
    async loadBatches() {
      this.weigh.batch_id = null;
      if (!this.weigh.product_id) { this.batchesOfProduct = []; return; }
      this.batchesOfProduct = await this.api('get', `/api/products/${this.weigh.product_id}/batches`);
    },
    async loadAllBatches() { this.allBatches = await this.api('get', '/api/batches'); },
    async loadLabels() { this.labels = await this.api('get', '/api/labels/recent'); },

    // ---------- 称重台 ----------
    async doWeigh(useDevice) {
      const payload = {
        product_id: this.weigh.product_id,
        batch_id: this.weigh.batch_id,
        net_weight: parseFloat(this.weigh.net_weight),
        user_id: this.userId,
        reprint_of_barcode: this.weigh.reprint_of_barcode,
      };
      try {
        // 称重与打印是同一动作（本地模拟打印机接口同时完成称重落库和出纸）
        this.lastLabelPaper = await this.api('post', '/api/device/print-label', payload);
        this.toast('标签已打印，唯一条码生效', 'ok');
        this.weigh.net_weight = '';
        this.weigh.reprint_of_barcode = null;
        await this.loadLabels();
        await this.loadAllBatches();
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async setReprint(l) {
      this.tab = 'weigh';
      this.weigh.product_id = l.product_id;
      this.weigh.net_weight = '';
      this.weigh.reprint_of_barcode = l.barcode;
      await this.loadBatches();
      this.weigh.batch_id = l.batch_id;
      this.toast(`已进入补打模式：重新称重打印后，旧码 ${l.barcode} 将立即作废，扫描必被拦截`, 'info');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    async doVoid(l) {
      if (!confirm(`确认注销标签 ${l.barcode}？注销后收银扫描将被拦截。`)) return;
      try {
        await this.api('post', '/api/labels/void', { barcode: l.barcode, user_id: this.userId, reason: '称重有误，付款前注销' });
        this.toast(`标签 ${l.barcode} 已作废`, 'ok');
        await this.loadLabels();
      } catch (e) { this.toast(e.message, 'err'); }
    },

    // ---------- 收银台 ----------
    async doScan() {
      const code = (this.scanCode || '').trim();
      if (!code) return;
      try {
        const txn = await this.api('post', '/api/txn/scan', {
          txn_no: this.currentTxn && this.currentTxn.status === 'open' ? this.currentTxn.txn_no : null,
          barcode: code, cashier_id: this.userId,
        });
        this.currentTxn = txn;
        this.scanCode = '';
        this.toast(`扫码入账成功：订单 ${txn.txn_no}`, 'ok');
        await this.loadLabels();
        await this.loadTxns();
      } catch (e) {
        this.toast('⛔ ' + e.message, 'err');   // 作废码/重复码：强制拦截红条
        this.scanCode = '';
        await this.loadLabels();
      }
    },
    async doRemove(it) {
      try {
        this.currentTxn = await this.api('post',
          `/api/txn/remove-item?txn_no=${this.currentTxn.txn_no}&item_id=${it.id}&cashier_id=${this.userId}`);
        this.toast('已移除该行，标签恢复为有效', 'info');
        await this.loadLabels();
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async doPay() {
      try {
        this.currentTxn = await this.api('post', '/api/txn/pay',
          { txn_no: this.currentTxn.txn_no, cashier_id: this.userId });
        this.toast(`结账成功，成交金额 ${this.currentTxn.total_amount.toFixed(2)} 元已冻结`, 'ok');
        await this.showReceipt(this.currentTxn.txn_no);
        await this.loadTxns();
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async showReceipt(no) {
      this.tab = 'pos';
      this.receiptText = await this.api('get', `/api/txn/${no}/receipt`);
    },

    // ---------- 订单 / 追溯 ----------
    async loadTxns() { this.txns = await this.api('get', '/api/txn'); },
    async queryTxn() {
      const no = (this.txnQuery || '').trim();
      if (!no) return this.loadTxns();
      try {
        this.selectedTxn = await this.api('get', `/api/txn/${no}`);
        this.selectedTxnNo = no;
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async selectTxn(no) {
      this.selectedTxnNo = no;
      this.selectedTxn = await this.api('get', `/api/txn/${no}`);
    },
    async doTrace() {
      try {
        const q = this.trace.allergen.trim() ? `?allergen=${encodeURIComponent(this.trace.allergen.trim())}` : '';
        this.traceResult = await this.api('get', `/api/trace/batch/${this.trace.batch_no}${q}`);
        this.toast(`找到可能受影响订单 ${this.traceResult.affected_orders.length} 笔`, 'ok');
      } catch (e) { this.toast(e.message, 'err'); }
    },

    // ---------- 调价 ----------
    async doPriceChange(p) {
      const np = parseFloat(this.priceDraft[p.id]);
      if (!(np >= 0)) return this.toast('请输入有效的新单价', 'err');
      if (!confirm(`确认将「${p.name}」单价 ${p.unit_price.toFixed(2)} 调整为 ${np.toFixed(2)}？\n旧订单金额不受影响，仅新标签按新价。`)) return;
      try {
        await this.api('post', '/api/products/price-change', { product_id: p.id, new_price: np, user_id: this.userId });
        this.toast('调价成功，已留痕；旧订单仍按成交价', 'ok');
        this.priceDraft[p.id] = '';
        await this.loadProducts();
        await this.loadHistory(p);
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async loadHistory(p) {
      this.historyMap = { ...this.historyMap, [p.id]: await this.api('get', `/api/products/${p.id}/price-history`) };
    },

    // ---------- 纠错 ----------
    async loadCorrections() {
      const q = this.corrFilter ? `?status=${this.corrFilter}` : '';
      this.corrections = await this.api('get', `/api/corrections${q}`);
    },
    openCorrection(it) {
      this.correcting = {
        txn_no: this.selectedTxn.txn_no,
        label_barcode: it.label_barcode,
        product_name: it.product_name,
        net_weight: it.net_weight,
        amount: it.amount,
      };
      this.correctionReason = '';
    },
    async doCreateCorrection() {
      if (this.correctionReason.trim().length < 2) return this.toast('请填写错贴情况说明', 'err');
      try {
        const c = await this.api('post', '/api/corrections', {
          txn_no: this.correcting.txn_no,
          label_barcode: this.correcting.label_barcode,
          cashier_id: this.userId,
          reason: this.correctionReason.trim(),
        });
        this.toast(`纠错申请 ${c.correction_no} 已提交，等待主管核对审批`, 'ok');
        this.correcting = null;
        this.corrFilter = 'pending';
        this.tab = 'correction';
        await this.loadCorrections();
      } catch (e) { this.toast(e.message, 'err'); }
    },
    openReview(c) {
      if (!this.isSupervisor) return this.toast('只有主管可以审批纠错单', 'err');
      this.reviewing = c;
      this.preview = null;
      this.review = { correct_product_id: null, correct_weight: null, decision: 'charge', supervisor_note: '' };
    },
    async doPreview() {
      if (!this.review.correct_product_id || !(parseFloat(this.review.correct_weight) > 0))
        return this.toast('请选择实物商品并填写净重', 'err');
      try {
        this.preview = await this.api('post',
          `/api/corrections/preview?txn_no=${this.reviewing.txn_no}` +
          `&label_barcode=${this.reviewing.label_barcode}` +
          `&correct_product_id=${this.review.correct_product_id}` +
          `&correct_weight=${parseFloat(this.review.correct_weight)}`);
        this.review.decision = this.preview.suggested_decision;
      } catch (e) { this.toast(e.message, 'err'); }
    },
    async doApprove() {
      try {
        const c = await this.api('post', '/api/corrections/review', {
          correction_no: this.reviewing.correction_no,
          supervisor_id: this.userId,
          correct_product_id: this.review.correct_product_id,
          correct_weight: parseFloat(this.review.correct_weight),
          decision: this.review.decision,
          supervisor_note: this.review.supervisor_note.trim(),
        });
        const verb = c.decision === 'charge' ? `补收 ${c.diff_amount.toFixed(2)}` :
                     c.decision === 'refund' ? `退款 ${Math.abs(c.diff_amount).toFixed(2)}` : '无差额';
        this.toast(`已核准：原金额 ${c.original_amount.toFixed(2)} 保留，${verb} 元`, 'ok');
        this.reviewing = null;
        await this.loadCorrections();
        await this.loadTxns();
      } catch (e) { this.toast('⛔ ' + e.message, 'err'); }
    },
    async doReject() {
      const note = this.review.supervisor_note.trim() || '主管驳回';
      try {
        await this.api('post',
          `/api/corrections/reject?correction_no=${this.reviewing.correction_no}` +
          `&supervisor_id=${this.userId}&note=${encodeURIComponent(note)}`);
        this.toast('已驳回该纠错申请', 'info');
        this.reviewing = null;
        await this.loadCorrections();
      } catch (e) { this.toast(e.message, 'err'); }
    },
  },
  watch: {
    corrFilter() { this.loadCorrections(); },
  },
}).mount('#app');
