from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'treasury')
class TestTreasuryPaymentSchedule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Journals
        cls.bank_journal = cls.env['account.journal'].search([
            ('type', '=', 'bank'), ('company_id', '=', cls.company.id)
        ], limit=1)
        if not cls.bank_journal:
            cls.bank_journal = cls.env['account.journal'].create({
                'name': 'Test Bank', 'type': 'bank', 'code': 'TBNK',
            })

        # Partner / vendor
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Test Vendor Treasury', 'supplier_rank': 1,
        })

        # Product for PO
        cls.product = cls.env['product.product'].create({
            'name': 'Test Part', 'type': 'consu',
            'standard_price': 1000.0,
        })

        # Security groups
        cls.group_manager = cls.env.ref(
            'rfh_treasury_payment_schedule.group_treasury_manager'
        )
        cls.group_user = cls.env.ref(
            'rfh_treasury_payment_schedule.group_treasury_user'
        )
        cls.group_auditor = cls.env.ref(
            'rfh_treasury_payment_schedule.group_treasury_auditor'
        )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_po(self, amount=5000.0):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 1,
                'price_unit': amount,
                'name': self.product.name,
                'date_planned': '2026-05-01 00:00:00',
            })],
        })
        po.button_confirm()
        return po

    def _make_vendor_bill(self, amount=3000.0):
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': '2026-04-28',
            'invoice_date_due': '2026-05-28',
            'invoice_line_ids': [(0, 0, {
                'name': 'Service Fee',
                'quantity': 1,
                'price_unit': amount,
            })],
        })
        move.action_post()
        return move

    def _make_schedule(self, **kw):
        vals = {
            'source_model': 'manual',
            'source_res_id': 0,
            'source_reference': 'Manual',
            'partner_id': self.vendor.id,
            'amount': 2000.0,
            'currency_id': self.company.currency_id.id,
            'company_id': self.company.id,
            'description': 'Test manual payment',
        }
        vals.update(kw)
        return self.env['rfh.treasury.payment.schedule'].create(vals)

    # ------------------------------------------------------------------
    # Tests: Purchase Order
    # ------------------------------------------------------------------

    def test_01_po_queue(self):
        """PO can be queued for treasury payment after confirmation."""
        po = self._make_po()
        self.assertEqual(po.state, 'purchase')
        po.action_create_treasury_payment_schedule()
        self.assertEqual(po.treasury_payment_state, 'queued')
        self.assertEqual(po.treasury_payment_count, 1)

    def test_02_po_duplicate_blocked(self):
        """Duplicate active schedule for same PO is blocked."""
        po = self._make_po()
        po.action_create_treasury_payment_schedule()
        with self.assertRaises(UserError):
            po.action_create_treasury_payment_schedule()

    def test_03_priority_change(self):
        """Priority can be changed and sequence updates accordingly."""
        sched = self._make_schedule()
        sched.priority = 'urgent'
        self.assertEqual(sched.priority_sequence, 2)
        self.assertEqual(sched.priority_color, 1)
        sched.priority = 'critical'
        self.assertEqual(sched.priority_sequence, 1)
        self.assertEqual(sched.priority_color, 6)

    def test_04_full_po_workflow(self):
        """PO: queue -> schedule -> approve -> batch -> batch paid."""
        po = self._make_po(amount=10000.0)
        po.action_create_treasury_payment_schedule()

        sched = self.env['rfh.treasury.payment.schedule'].search([
            ('source_model', '=', 'purchase.order'),
            ('source_res_id', '=', po.id),
        ], limit=1)
        self.assertEqual(sched.state, 'queued')

        sched.action_schedule()
        self.assertEqual(sched.state, 'scheduled')

        sched.action_approve()
        self.assertEqual(sched.state, 'approved')

        # Create batch
        batch = self.env['rfh.treasury.payment.batch'].create({
            'journal_id': self.bank_journal.id,
            'payment_date': '2026-05-10',
        })
        sched.write({'batch_id': batch.id, 'state': 'batched'})
        batch.action_submit()
        batch.action_approve()
        batch.action_mark_paid()

        self.assertEqual(batch.state, 'paid')
        self.assertEqual(sched.state, 'paid')
        self.assertEqual(po.treasury_payment_state, 'paid')

    def test_05_po_chatter_on_payment(self):
        """PO receives chatter message when treasury marks payment paid."""
        po = self._make_po(amount=500.0)
        po.action_create_treasury_payment_schedule()
        sched = self.env['rfh.treasury.payment.schedule'].search([
            ('source_model', '=', 'purchase.order'),
            ('source_res_id', '=', po.id),
        ], limit=1)
        sched.action_schedule()
        sched.action_approve()
        sched.write({'payment_date': '2026-05-01', 'journal_id': self.bank_journal.id})
        sched.action_mark_paid()

        messages = po.message_ids.mapped('body')
        self.assertTrue(
            any('Treasury payment' in m for m in messages),
            'PO should have a treasury payment chatter message.'
        )

    # ------------------------------------------------------------------
    # Tests: Vendor Bill
    # ------------------------------------------------------------------

    def test_06_vendor_bill_queue(self):
        """Posted vendor bill can be queued for treasury payment."""
        move = self._make_vendor_bill(amount=7500.0)
        move.action_create_treasury_payment_schedule()
        self.assertEqual(move.treasury_payment_count, 1)

    def test_07_vendor_bill_not_queued_if_draft(self):
        """Draft vendor bill cannot be queued."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
        })
        with self.assertRaises(UserError):
            move.action_create_treasury_payment_schedule()

    def test_08_vendor_bill_payment_creates_account_payment(self):
        """Marking vendor bill schedule paid should create account.payment."""
        move = self._make_vendor_bill(amount=4000.0)
        move.action_create_treasury_payment_schedule()
        sched = self.env['rfh.treasury.payment.schedule'].search([
            ('source_model', '=', 'account.move'),
            ('source_res_id', '=', move.id),
        ], limit=1)
        sched.action_schedule()
        sched.action_approve()
        sched.write({'payment_date': '2026-05-01', 'journal_id': self.bank_journal.id})
        sched.action_mark_paid()

        self.assertTrue(
            sched.account_payment_id,
            'An account.payment should have been created for the vendor bill.'
        )
        self.assertEqual(sched.account_payment_id.state, 'posted')

    # ------------------------------------------------------------------
    # Tests: Batch wizard
    # ------------------------------------------------------------------

    def test_09_batch_wizard(self):
        """Batch wizard creates a batch and moves lines to batched state."""
        s1 = self._make_schedule(amount=1000.0)
        s2 = self._make_schedule(amount=2000.0)
        s1.action_queue()
        s2.action_queue()

        wizard = self.env['rfh.treasury.payment.batch.wizard'].with_context(
            active_ids=[s1.id, s2.id],
            active_model='rfh.treasury.payment.schedule',
        ).create({
            'scheduled_date': '2026-05-15',
            'journal_id': self.bank_journal.id,
            'payment_reference': 'BATCH-TEST-001',
        })
        wizard.action_create_batch()

        self.assertEqual(s1.state, 'batched')
        self.assertEqual(s2.state, 'batched')
        self.assertEqual(s1.batch_id, s2.batch_id)

    def test_10_batch_wizard_blocks_paid(self):
        """Batch wizard refuses to batch paid or cancelled schedules."""
        s1 = self._make_schedule(amount=500.0)
        s1.action_queue()
        s1.action_schedule()
        s1.action_approve()
        s1.write({'payment_date': '2026-05-01', 'journal_id': self.bank_journal.id})
        s1.action_mark_paid()
        self.assertEqual(s1.state, 'paid')

        with self.assertRaises(UserError):
            self.env['rfh.treasury.payment.batch.wizard'].with_context(
                active_ids=[s1.id],
                active_model='rfh.treasury.payment.schedule',
            ).default_get(['line_ids'])

    # ------------------------------------------------------------------
    # Tests: Constraints
    # ------------------------------------------------------------------

    def test_11_amount_must_be_positive(self):
        """Amount <= 0 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self._make_schedule(amount=0.0)

    def test_12_amount_paid_cannot_exceed_amount(self):
        """amount_paid > amount raises ValidationError."""
        sched = self._make_schedule(amount=1000.0)
        with self.assertRaises(ValidationError):
            sched.amount_paid = 9999.0
            sched._check_amount_paid()

    def test_13_paid_schedule_cannot_be_deleted(self):
        """Paid schedule cannot be deleted."""
        sched = self._make_schedule()
        sched.action_queue()
        sched.action_schedule()
        sched.action_approve()
        sched.write({'payment_date': '2026-05-01', 'journal_id': self.bank_journal.id})
        sched.action_mark_paid()
        with self.assertRaises(UserError):
            sched.unlink()

    def test_14_payment_date_required_to_mark_paid(self):
        """payment_date is required before marking paid."""
        sched = self._make_schedule()
        sched.action_queue()
        sched.action_schedule()
        sched.action_approve()
        sched.journal_id = self.bank_journal.id
        # payment_date NOT set
        with self.assertRaises(UserError):
            sched.action_mark_paid()

    def test_15_journal_required_to_mark_paid(self):
        """journal_id is required before marking paid."""
        sched = self._make_schedule()
        sched.action_queue()
        sched.action_schedule()
        sched.action_approve()
        sched.payment_date = '2026-05-01'
        # journal NOT set
        with self.assertRaises(UserError):
            sched.action_mark_paid()

    # ------------------------------------------------------------------
    # Tests: State transitions
    # ------------------------------------------------------------------

    def test_16_cancel_and_reset(self):
        """Queued schedule can be cancelled then reset to draft."""
        sched = self._make_schedule()
        sched.action_queue()
        self.assertEqual(sched.state, 'queued')
        sched.action_cancel()
        self.assertEqual(sched.state, 'cancelled')
        sched.action_reset_to_draft()
        self.assertEqual(sched.state, 'draft')

    def test_17_balance_computed(self):
        """Balance = amount - amount_paid."""
        sched = self._make_schedule(amount=5000.0)
        sched.amount_paid = 2000.0
        sched._compute_balance()
        self.assertEqual(sched.balance, 3000.0)

    # ------------------------------------------------------------------
    # Tests: Security (basic smoke test — real ACL tests need separate users)
    # ------------------------------------------------------------------

    def test_18_sequence_generated(self):
        """Created schedule gets a TPS/... reference from sequence."""
        sched = self._make_schedule()
        self.assertTrue(sched.name.startswith('TPS/'))

    def test_19_batch_sequence_generated(self):
        """Created batch gets a TPB/... reference from sequence."""
        batch = self.env['rfh.treasury.payment.batch'].create({})
        self.assertTrue(batch.name.startswith('TPB/'))
