from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'treasury', 'po_approval')
class TestPurchaseOrderApproval(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        if not cls.company.chart_template:
            cls.env['account.chart.template'].try_loading(
                'generic_coa', cls.company, install_demo=False,
            )
        cls.company.po_double_validation = 'one_step'
        cls.company.po_lock = 'edit'

        cls.bank_journal = cls.env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', cls.company.id),
        ], limit=1)
        if not cls.bank_journal:
            cls.bank_journal = cls.env['account.journal'].create({
                'name': 'PO Approval Bank',
                'type': 'bank',
                'code': 'PABK',
            })

        cls.vendor = cls.env['res.partner'].create({
            'name': 'Approval Test Vendor',
            'supplier_rank': 1,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Approval Test Service',
            'type': 'service',
            'purchase_ok': True,
            'standard_price': 1000.0,
        })
        cls.other_product = cls.env['product.product'].create({
            'name': 'Approval Test Extra',
            'type': 'service',
            'purchase_ok': True,
            'standard_price': 50.0,
        })

        cls.group_director = cls.env.ref(
            'rfh_treasury_payment_schedule.group_procurement_director'
        )
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')
        cls.group_treasury_manager = cls.env.ref(
            'rfh_treasury_payment_schedule.group_treasury_manager'
        )
        cls.buyer = cls._make_user('po.buyer', 'PO Buyer', cls.group_purchase)
        cls.director = cls._make_user('po.director', 'Procurement Director', cls.group_director)
        cls.other_director = cls._make_user(
            'po.other.director', 'Other Director', cls.group_director,
        )
        cls.finance = cls._make_user(
            'po.finance',
            'Finance User',
            cls.group_purchase | cls.group_treasury_manager,
        )
        cls.treasury_only = cls._make_user(
            'po.treasury',
            'Treasury Only',
            cls.group_treasury_manager,
        )

    @classmethod
    def _make_user(cls, login, name, groups):
        return cls.env['res.users'].create({
            'name': name,
            'login': login,
            'email': '%s@example.com' % login,
            'groups_id': [(6, 0, groups.ids)],
            'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
        })

    def _make_draft_po(self, amount=5000.0):
        return self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 1,
                'price_unit': amount,
                'name': self.product.name,
                'date_planned': '2026-05-01 00:00:00',
            })],
        })

    def _make_confirmed_po(self, amount=5000.0):
        po = self._make_draft_po(amount)
        po.button_confirm()
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(po.rfh_approval_state, 'to_approve')
        return po

    def _approve(self, po, user=None):
        po.with_user(user or self.director).action_rfh_approve()
        return po

    def _schedule(self, record, model):
        return self.env['rfh.treasury.payment.schedule'].search([
            ('source_model', '=', model),
            ('source_res_id', '=', record.id),
        ], limit=1)

    def _change_price(self, po):
        po.order_line.write({'price_unit': po.order_line.price_unit + 25.0})

    def _bill_from_po(self, po):
        po.action_create_invoice()
        bill = po.invoice_ids[:1]
        self.assertTrue(bill, 'A vendor bill should be created from the purchase order.')
        self.assertTrue(bill.invoice_line_ids.purchase_line_id)
        if not bill.invoice_date:
            bill.invoice_date = '2026-05-01'
        if bill.state == 'draft':
            bill.action_post()
        return bill

    def _assert_not_approved(self, callable_):
        with self.assertRaises(UserError) as caught:
            callable_()
        self.assertIn('not approved', str(caught.exception).lower())

    def _assert_direct_payment_blocked(self, callable_, po):
        with self.assertRaises(UserError) as caught:
            callable_()
        message = str(caught.exception)
        self.assertIn(po.name, message)
        self.assertIn('not approved', message.lower())

    def _register_payment(self, bill, user=None):
        register = self.env['account.payment.register']
        if user:
            register = register.with_user(user)
        wizard = register.with_context(
            active_model='account.move',
            active_ids=bill.ids,
        ).create({})
        return wizard._create_payments()

    def _grant_invoicing(self, user):
        user.write({
            'groups_id': [(4, self.env.ref('account.group_account_invoice').id)],
        })
        self.env.registry.clear_cache()

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def test_confirm_does_not_require_approval(self):
        """Approval gates payment. Buyers can still confirm the order."""
        po = self._make_draft_po()
        po.button_confirm()
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(po.rfh_approval_state, 'to_approve')
        self.assertFalse(po.rfh_approval_user_id)
        self.assertFalse(po.rfh_approval_date)

    def test_buyer_and_finance_cannot_approve_or_reject(self):
        po = self._make_confirmed_po()
        for user in (self.buyer, self.finance, self.treasury_only):
            with self.assertRaises(AccessError):
                po.with_user(user).action_rfh_approve()
            with self.assertRaises(AccessError):
                po.with_user(user).action_rfh_reject()
            with self.assertRaises(AccessError):
                po.with_user(user)._rfh_apply_rejection('Not allowed')
        self.assertEqual(po.rfh_approval_state, 'to_approve')

    def test_director_approve_records_user_and_date(self):
        po = self._make_confirmed_po()
        self.assertTrue(po.with_user(self.director).rfh_can_approve)
        self.assertFalse(po.with_user(self.buyer).rfh_can_approve)
        self._approve(po)
        self.assertEqual(po.rfh_approval_state, 'approved')
        self.assertEqual(po.rfh_approval_user_id, self.director)
        self.assertTrue(po.rfh_approval_date)
        self.assertFalse(po.rfh_approval_grandfathered)
        self.assertTrue(po.message_ids.filtered(
            lambda message: 'approved by' in (message.body or '').lower()
        ))

    def test_reject_records_reason_user_and_date_and_can_be_requested_again(self):
        po = self._make_confirmed_po()
        po.with_user(self.buyer).action_rfh_request_approval()
        with self.assertRaises(UserError):
            po.with_user(self.director)._rfh_apply_rejection('   ')
        po.with_user(self.director)._rfh_apply_rejection('Over budget')
        self.assertEqual(po.rfh_approval_state, 'rejected')
        self.assertEqual(po.rfh_approval_user_id, self.director)
        self.assertTrue(po.rfh_approval_date)
        self.assertEqual(po.rfh_approval_note, 'Over budget')

        po.with_user(self.buyer).action_rfh_request_approval()
        self.assertEqual(po.rfh_approval_state, 'requested')
        self.assertFalse(po.rfh_approval_user_id)
        self.assertFalse(po.rfh_approval_date)

    def test_reject_wizard_only_the_designated_director(self):
        po = self._make_confirmed_po()
        po.with_user(self.buyer).action_rfh_request_approval()
        self.company.rfh_po_approver_id = self.director

        wizard = self.env['rfh.po.approval.reject.wizard'].with_user(self.other_director).create({
            'order_id': po.id,
            'reason': 'I am the wrong director',
        })
        with self.assertRaises(AccessError):
            wizard.action_reject()
        self.assertEqual(po.rfh_approval_state, 'requested')

        wizard = self.env['rfh.po.approval.reject.wizard'].with_user(self.director).create({
            'order_id': po.id,
            'reason': 'Price is too high',
        })
        wizard.action_reject()
        self.assertEqual(po.rfh_approval_state, 'rejected')
        self.assertEqual(po.rfh_approval_user_id, self.director)
        self.assertEqual(po.rfh_approval_note, 'Price is too high')

    def test_designated_user_is_the_only_approver_even_for_superuser(self):
        po = self._make_confirmed_po()
        self.company.rfh_po_approver_id = self.director
        with self.assertRaises(AccessError):
            po.with_user(self.other_director).action_rfh_approve()
        with self.assertRaises(AccessError):
            po.sudo().action_rfh_approve()
        self._approve(po)
        self.assertEqual(po.rfh_approval_user_id, self.director)

    def test_request_sends_email_and_activity_to_every_director(self):
        po = self._make_confirmed_po()
        po.with_user(self.buyer).action_rfh_request_approval()
        self.assertEqual(po.rfh_approval_state, 'requested')

        mails = self.env['mail.mail'].search([
            ('model', '=', 'purchase.order'),
            ('res_id', '=', po.id),
        ])
        recipients = set()
        for mail in mails:
            recipients.update(
                address.strip()
                for address in (mail.email_to or '').split(',')
                if address.strip()
            )
        self.assertIn(self.director.email, recipients)
        self.assertIn(self.other_director.email, recipients)
        self.assertTrue(any(po.name in (mail.subject or '') for mail in mails))

        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', po.id),
        ])
        self.assertEqual(
            set(activities.mapped('user_id').ids),
            set(self.director.ids + self.other_director.ids),
        )

        self._approve(po)
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', po.id),
        ]))

    def test_request_emails_only_the_designated_director(self):
        self.company.rfh_po_approver_id = self.director
        po = self._make_confirmed_po()
        po.with_user(self.buyer).action_rfh_request_approval()
        mails = self.env['mail.mail'].search([
            ('model', '=', 'purchase.order'),
            ('res_id', '=', po.id),
        ])
        self.assertEqual(len(mails), 1)
        self.assertIn(self.director.email, mails.email_to)
        self.assertNotIn(self.other_director.email, mails.email_to or '')
        activity = self.env['mail.activity'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', po.id),
        ])
        self.assertEqual(activity.user_id, self.director)

    def test_setting_assigns_the_director_group(self):
        self.assertFalse(self.buyer.has_group(
            'rfh_treasury_payment_schedule.group_procurement_director'
        ))
        self.env['res.config.settings'].create({
            'rfh_po_approver_id': self.buyer.id,
        })
        self.assertEqual(self.company.rfh_po_approver_id, self.buyer)
        self.env.registry.clear_cache()
        self.assertTrue(self.buyer.has_group(
            'rfh_treasury_payment_schedule.group_procurement_director'
        ))

    def test_direct_write_of_approval_fields_is_blocked(self):
        po = self._make_confirmed_po()
        with self.assertRaises(AccessError):
            po.with_user(self.director).write({'rfh_approval_state': 'approved'})
        with self.assertRaises(AccessError):
            po.write({
                'rfh_approval_state': 'approved',
                'rfh_approval_grandfathered': True,
            })
        self.assertEqual(po.rfh_approval_state, 'to_approve')

    # ------------------------------------------------------------------
    # Payment gates: purchase orders
    # ------------------------------------------------------------------

    def test_unapproved_po_cannot_be_queued(self):
        po = self._make_confirmed_po()
        self._assert_not_approved(po.action_create_treasury_payment_schedule)
        self._assert_not_approved(
            lambda: po.with_user(self.finance).action_create_treasury_payment_schedule()
        )
        self._assert_not_approved(
            lambda: po.sudo().action_create_treasury_payment_schedule()
        )
        with self.assertRaises(UserError):
            self.env['rfh.treasury.payment.schedule'].create({
                'source_model': 'purchase.order',
                'source_res_id': po.id,
                'source_reference': po.name,
                'partner_id': self.vendor.id,
                'amount': po.amount_total,
                'currency_id': po.currency_id.id,
                'company_id': po.company_id.id,
                'state': 'queued',
            })
        draft = self.env['rfh.treasury.payment.schedule'].create({
            'source_model': 'purchase.order',
            'source_res_id': po.id,
            'source_reference': po.name,
            'partner_id': self.vendor.id,
            'amount': po.amount_total,
            'currency_id': po.currency_id.id,
            'company_id': po.company_id.id,
            'state': 'draft',
        })
        self._assert_not_approved(draft.action_queue)

    def test_revoked_po_blocks_treasury_approve_and_pay(self):
        po = self._make_confirmed_po()
        self._approve(po)
        po.action_create_treasury_payment_schedule()
        sched = self._schedule(po, 'purchase.order')
        sched.action_schedule()
        self._change_price(po)
        self.assertEqual(po.rfh_approval_state, 'to_approve')
        self._assert_not_approved(sched.action_approve)
        sched.write({
            'payment_date': '2026-06-01',
            'journal_id': self.bank_journal.id,
        })
        self._assert_not_approved(sched.action_mark_paid)
        self._assert_not_approved(
            lambda: sched.with_user(self.treasury_only).action_mark_paid()
        )
        self.assertNotEqual(sched.state, 'paid')

    def test_revoked_po_blocks_batch_approve_and_pay(self):
        po = self._make_confirmed_po()
        self._approve(po)
        po.action_create_treasury_payment_schedule()
        sched = self._schedule(po, 'purchase.order')
        sched.action_schedule()
        batch = self.env['rfh.treasury.payment.batch'].create({
            'journal_id': self.bank_journal.id,
            'payment_date': '2026-06-01',
        })
        sched.write({'batch_id': batch.id, 'state': 'batched'})
        batch.action_submit()
        self._change_price(po)
        self._assert_not_approved(batch.action_approve)

        po2 = self._make_confirmed_po(amount=2500.0)
        self._approve(po2)
        po2.action_create_treasury_payment_schedule()
        sched2 = self._schedule(po2, 'purchase.order')
        sched2.action_schedule()
        batch2 = self.env['rfh.treasury.payment.batch'].create({
            'journal_id': self.bank_journal.id,
            'payment_date': '2026-06-02',
        })
        sched2.write({'batch_id': batch2.id, 'state': 'batched'})
        batch2.action_submit()
        batch2.action_approve()
        self._change_price(po2)
        self._assert_not_approved(batch2.action_mark_paid)
        self.assertNotEqual(sched2.state, 'paid')
        self.assertNotEqual(po2.treasury_payment_state, 'paid')

    # ------------------------------------------------------------------
    # Payment gates: vendor bills linked to a PO
    # ------------------------------------------------------------------

    def test_unapproved_linked_bill_cannot_be_queued(self):
        po = self._make_confirmed_po()
        bill = self._bill_from_po(po)
        self.assertEqual(po.rfh_approval_state, 'to_approve')
        self._assert_not_approved(bill.action_create_treasury_payment_schedule)
        self._approve(po)
        bill.action_create_treasury_payment_schedule()
        self.assertEqual(bill.treasury_payment_count, 1)

    def test_unlinked_bill_can_still_be_queued(self):
        account = self.env['account.account'].search([
            ('account_type', '=', 'expense'),
            ('company_ids', 'in', self.company.id),
        ], limit=1)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': '2026-05-01',
            'invoice_line_ids': [(0, 0, {
                'name': 'Unlinked service',
                'quantity': 1,
                'price_unit': 800.0,
                'account_id': account.id,
            })],
        })
        bill.action_post()
        bill.action_create_treasury_payment_schedule()
        self.assertEqual(bill.treasury_payment_count, 1)

    def test_revoked_po_blocks_bill_approve_and_pay(self):
        po = self._make_confirmed_po()
        self._approve(po)
        bill = self._bill_from_po(po)
        bill.action_create_treasury_payment_schedule()
        sched = self._schedule(bill, 'account.move')
        sched.action_schedule()
        self._change_price(po)
        self._assert_not_approved(sched.action_approve)
        sched.write({
            'payment_date': '2026-06-01',
            'journal_id': self.bank_journal.id,
        })
        self._assert_not_approved(sched.action_mark_paid)
        self.assertFalse(sched.account_payment_id)

    def test_approved_po_and_linked_bill_can_be_paid(self):
        po = self._make_confirmed_po(amount=1800.0)
        self._approve(po)
        po.action_create_treasury_payment_schedule()
        po_sched = self._schedule(po, 'purchase.order')
        po_sched.action_schedule()
        po_sched.action_approve()
        po_sched.write({
            'payment_date': '2026-06-01',
            'journal_id': self.bank_journal.id,
        })
        po_sched.action_mark_paid()
        self.assertEqual(po_sched.state, 'paid')
        self.assertEqual(po.treasury_payment_state, 'paid')

        bill = self._bill_from_po(po)
        bill.action_create_treasury_payment_schedule()
        bill_sched = self._schedule(bill, 'account.move')
        bill_sched.action_schedule()
        bill_sched.action_approve()
        bill_sched.write({
            'payment_date': '2026-06-02',
            'journal_id': self.bank_journal.id,
        })
        bill_sched.action_mark_paid()
        self.assertEqual(bill_sched.state, 'paid')
        self.assertTrue(bill_sched.account_payment_id)
        self.assertIn(bill_sched.account_payment_id.state, ('in_process', 'paid', 'posted'))

    # ------------------------------------------------------------------
    # Direct payment: Register Payment and bill pay actions
    # ------------------------------------------------------------------

    def test_unapproved_po_blocks_register_payment_and_bill_pay_actions(self):
        po = self._make_confirmed_po()
        bill = self._bill_from_po(po)
        self._grant_invoicing(self.finance)
        payable = bill.line_ids.filtered(
            lambda line: line.account_id.account_type == 'liability_payable'
        )
        self.assertTrue(payable)
        payment_count = self.env['account.payment'].search_count([])

        for pay in (
            lambda: bill.action_register_payment(),
            lambda: bill.action_force_register_payment(),
            lambda: bill.sudo().action_register_payment(),
            lambda: bill.with_user(self.finance).action_register_payment(),
            lambda: payable.action_register_payment(),
            lambda: self._register_payment(bill),
            lambda: self._register_payment(bill, user=self.finance),
            lambda: bill.js_assign_outstanding_line(payable[:1].id),
        ):
            self._assert_direct_payment_blocked(pay, po)

        self.assertEqual(bill.payment_state, 'not_paid')
        self.assertEqual(self.env['account.payment'].search_count([]), payment_count)

    def test_unlinked_bill_can_register_payment(self):
        account = self.env['account.account'].search([
            ('account_type', '=', 'expense'),
            ('company_ids', 'in', self.company.id),
        ], limit=1)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': '2026-05-01',
            'invoice_line_ids': [(0, 0, {
                'name': 'Unlinked service',
                'quantity': 1,
                'price_unit': 800.0,
                'account_id': account.id,
            })],
        })
        bill.action_post()
        payments = self._register_payment(bill)
        self.assertTrue(payments)
        self.assertIn(bill.payment_state, ('paid', 'in_payment', 'partial'))

    def test_approved_linked_bill_can_register_payment(self):
        po = self._make_confirmed_po(amount=900.0)
        self._approve(po)
        bill = self._bill_from_po(po)
        action = bill.action_register_payment()
        self.assertEqual(action['res_model'], 'account.payment.register')
        payments = self._register_payment(bill)
        self.assertTrue(payments)
        self.assertIn(bill.payment_state, ('paid', 'in_payment', 'partial'))

    def test_revoked_po_blocks_register_payment_until_approved_again(self):
        po = self._make_confirmed_po(amount=750.0)
        self._approve(po)
        bill = self._bill_from_po(po)
        self._change_price(po)
        self._assert_direct_payment_blocked(lambda: self._register_payment(bill), po)
        self.assertEqual(bill.payment_state, 'not_paid')
        self._approve(po)
        payments = self._register_payment(bill)
        self.assertTrue(payments)
        self.assertIn(bill.payment_state, ('paid', 'in_payment', 'partial'))

    def test_grandfathered_po_bill_can_register_payment(self):
        po = self._make_confirmed_po(amount=640.0)
        po._rfh_grandfather_confirmed_orders()
        self.assertTrue(po.rfh_approval_grandfathered)
        bill = self._bill_from_po(po)
        payments = self._register_payment(bill)
        self.assertTrue(payments)
        self.assertIn(bill.payment_state, ('paid', 'in_payment', 'partial'))

    def test_treasury_mark_paid_still_works_when_register_payment_is_blocked(self):
        """The schedule path stays the supported way to pay, and still pays once approved."""
        po = self._make_confirmed_po(amount=480.0)
        bill = self._bill_from_po(po)
        self._assert_direct_payment_blocked(lambda: self._register_payment(bill), po)
        self._assert_not_approved(bill.action_create_treasury_payment_schedule)
        self._approve(po)
        bill.action_create_treasury_payment_schedule()
        sched = self._schedule(bill, 'account.move')
        sched.action_schedule()
        sched.action_approve()
        sched.write({
            'payment_date': '2026-06-03',
            'journal_id': self.bank_journal.id,
        })
        sched.action_mark_paid()
        self.assertEqual(sched.state, 'paid')
        self.assertTrue(sched.account_payment_id)

    # ------------------------------------------------------------------
    # Upgrade / grandfather
    # ------------------------------------------------------------------

    def test_upgrade_grandfathers_confirmed_orders_only(self):
        draft = self._make_draft_po()
        confirmed = self._make_confirmed_po()
        rejected = self._make_confirmed_po(amount=900.0)
        rejected.with_user(self.director)._rfh_apply_rejection('Do not buy this')
        already = self._make_confirmed_po(amount=700.0)
        self._approve(already)

        (draft | confirmed | rejected | already)._rfh_grandfather_confirmed_orders()

        self.assertEqual(draft.rfh_approval_state, 'to_approve')
        self.assertFalse(draft.rfh_approval_grandfathered)
        self.assertEqual(confirmed.rfh_approval_state, 'approved')
        self.assertTrue(confirmed.rfh_approval_grandfathered)
        self.assertFalse(confirmed.rfh_approval_user_id)
        self.assertTrue(confirmed.rfh_approval_date)
        self.assertIn('Grandfathered', confirmed.rfh_approval_note or '')
        self.assertEqual(rejected.rfh_approval_state, 'rejected')
        self.assertFalse(rejected.rfh_approval_grandfathered)
        self.assertEqual(already.rfh_approval_user_id, self.director)
        self.assertFalse(already.rfh_approval_grandfathered)

        stamped = confirmed.rfh_approval_date
        self.assertFalse(confirmed._rfh_grandfather_confirmed_orders())
        self.assertEqual(confirmed.rfh_approval_date, stamped)

    def test_grandfathered_order_can_be_queued_and_paid(self):
        po = self._make_confirmed_po()
        po._rfh_grandfather_confirmed_orders()
        po.action_create_treasury_payment_schedule()
        sched = self._schedule(po, 'purchase.order')
        sched.action_schedule()
        sched.action_approve()
        sched.write({
            'payment_date': '2026-06-01',
            'journal_id': self.bank_journal.id,
        })
        sched.action_mark_paid()
        self.assertEqual(sched.state, 'paid')
        self.assertEqual(po.treasury_payment_state, 'paid')

    def test_commercial_change_clears_grandfather_and_blocks_payment(self):
        po = self._make_confirmed_po()
        po._rfh_grandfather_confirmed_orders()
        po.action_create_treasury_payment_schedule()
        sched = self._schedule(po, 'purchase.order')
        sched.action_schedule()
        self._change_price(po)
        self.assertEqual(po.rfh_approval_state, 'to_approve')
        self.assertFalse(po.rfh_approval_grandfathered)
        self.assertFalse(po.rfh_approval_user_id)
        self._assert_not_approved(sched.action_approve)
        self._assert_not_approved(po.action_create_treasury_payment_schedule)

    def test_line_changes_that_clear_or_keep_approval(self):
        po = self._make_confirmed_po()
        self._approve(po)
        po.order_line.date_planned = '2026-07-01 00:00:00'
        self.assertEqual(po.rfh_approval_state, 'approved')

        po.order_line.product_qty = 3
        self.assertEqual(po.rfh_approval_state, 'to_approve')

        self._approve(po)
        po.order_line.discount = 5
        self.assertEqual(po.rfh_approval_state, 'to_approve')

        self._approve(po)
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.other_product.id,
            'product_qty': 1,
            'price_unit': 40.0,
            'name': self.other_product.name,
            'date_planned': '2026-07-02 00:00:00',
        })
        self.assertEqual(po.rfh_approval_state, 'to_approve')

        draft = self._make_draft_po(amount=300.0)
        self._approve(draft)
        extra = self.env['purchase.order.line'].create({
            'order_id': draft.id,
            'product_id': self.other_product.id,
            'product_qty': 1,
            'price_unit': 15.0,
            'name': self.other_product.name,
            'date_planned': '2026-07-03 00:00:00',
        })
        self.assertEqual(draft.rfh_approval_state, 'to_approve')
        self._approve(draft)
        extra.unlink()
        self.assertEqual(draft.rfh_approval_state, 'to_approve')

    def test_copy_of_a_grandfathered_order_needs_approval(self):
        po = self._make_confirmed_po()
        po._rfh_grandfather_confirmed_orders()
        copied = po.copy()
        self.assertEqual(copied.rfh_approval_state, 'to_approve')
        self.assertFalse(copied.rfh_approval_grandfathered)
        self.assertFalse(copied.rfh_approval_user_id)
        self.assertFalse(copied.rfh_approval_date)
