from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = ['account.move', 'rfh.treasury.payment.origin.mixin']
    _name = 'account.move'

    treasury_payment_count = fields.Integer(
        string='Treasury Payments',
        compute='_compute_treasury_payment_count',
    )
    rfh_unapproved_po_names = fields.Char(
        string='Unapproved Purchase Orders',
        compute='_compute_rfh_unapproved_po_names',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def _compute_treasury_payment_count(self):
        # sudo: the count is shown on the bill form. Accounting users who are
        # not in a Treasury group must still be able to open the bill.
        Schedule = self.env['rfh.treasury.payment.schedule'].sudo()
        for move in self:
            move.treasury_payment_count = Schedule.search_count([
                ('source_model', '=', 'account.move'),
                ('source_res_id', '=', move.id),
            ])

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------

    def _prepare_treasury_payment_schedule_vals(self):
        self.ensure_one()
        if self.move_type not in ('in_invoice', 'in_refund'):
            raise UserError(_('Only vendor bills and credit notes can be queued for Treasury payment.'))
        if self.state != 'posted':
            raise UserError(_('Only posted vendor bills can be queued for Treasury payment.'))
        if self.payment_state in ('paid', 'reversed'):
            raise UserError(_('This vendor bill is already paid or reversed.'))
        self._rfh_assert_linked_purchase_orders_approved()
        return {
            'source_model': 'account.move',
            'source_res_id': self.id,
            'source_reference': self.name,
            'partner_id': self.partner_id.id,
            'amount': self.amount_residual,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'due_date': self.invoice_date_due or fields.Date.today(),
            'description': _('Payment for Vendor Bill %s', self.name),
            'state': 'queued',
        }

    def action_create_treasury_payment_schedule(self):
        self.ensure_one()
        if self.move_type not in ('in_invoice', 'in_refund'):
            raise UserError(_('Only vendor bills can be queued for Treasury payment.'))
        self._rfh_assert_linked_purchase_orders_approved()
        return super().action_create_treasury_payment_schedule()

    def _rfh_linked_purchase_orders(self):
        """Purchase orders whose lines are billed on this move.

        Read with sudo so a Treasury user who cannot open Purchase still
        cannot pay a bill that hangs off an unapproved order.
        """
        self.ensure_one()
        move = self.sudo()
        lines = move.invoice_line_ids if 'invoice_line_ids' in move._fields else move.line_ids
        return lines.purchase_line_id.order_id

    def _rfh_unapproved_linked_purchase_orders(self):
        """Purchase orders on this vendor bill that are not approved for payment."""
        self.ensure_one()
        if self.move_type not in ('in_invoice', 'in_refund'):
            return self.env['purchase.order']
        return self._rfh_linked_purchase_orders().filtered(
            lambda order: order.rfh_approval_state != 'approved'
        )

    def _rfh_assert_linked_purchase_orders_approved(self):
        self.ensure_one()
        blocked = self._rfh_unapproved_linked_purchase_orders()
        if blocked:
            raise UserError(_(
                'Vendor bill %(bill)s is linked to purchase order %(po)s, which is not approved. '
                'Treasury cannot queue, approve, or pay it until the Procurement Director approves the purchase order.',
                bill=self.display_name,
                po=', '.join(blocked.mapped('name')),
            ))

    def _rfh_assert_direct_payment_allowed(self):
        """Block paying a vendor bill outside Treasury while its PO is unapproved.

        Covers Register Payment (``account.payment.register``) and the pay
        actions on ``account.move``. Treasury mark-paid creates
        ``account.payment`` itself after its own approval check, so this
        method is not used on that path. Customer invoices and bills with
        no purchase-order link are left alone. Sudo does not skip the check.
        """
        details = []
        for move in self:
            blocked = move._rfh_unapproved_linked_purchase_orders()
            if blocked:
                details.append('%s (purchase order %s)' % (
                    move.display_name,
                    ', '.join(blocked.mapped('name')),
                ))
        if details:
            raise UserError(_(
                'Cannot pay this vendor bill because the linked purchase order is not approved: %s. '
                'The Procurement Director must approve the purchase order before the bill can be paid.',
                '; '.join(details),
            ))

    def action_register_payment(self):
        self._rfh_assert_direct_payment_allowed()
        return super().action_register_payment()

    def action_force_register_payment(self):
        self._rfh_assert_direct_payment_allowed()
        return super().action_force_register_payment()

    def js_assign_outstanding_line(self, line_id):
        self._rfh_assert_direct_payment_allowed()
        return super().js_assign_outstanding_line(line_id)

    @api.depends('invoice_line_ids.purchase_line_id.order_id.rfh_approval_state')
    def _compute_rfh_unapproved_po_names(self):
        for move in self:
            if move.move_type not in ('in_invoice', 'in_refund'):
                move.rfh_unapproved_po_names = False
                continue
            blocked = move._rfh_unapproved_linked_purchase_orders()
            move.rfh_unapproved_po_names = ', '.join(blocked.mapped('name')) if blocked else False

    def action_treasury_payment_completed(self, payment_schedule):
        self.ensure_one()
        self.message_post(
            body=_('Treasury payment completed: %s', payment_schedule.name)
        )

    # ------------------------------------------------------------------
    # Smart button
    # ------------------------------------------------------------------

    def action_view_treasury_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Treasury Payments'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [
                ('source_model', '=', 'account.move'),
                ('source_res_id', '=', self.id),
            ],
            'context': {
                'default_source_model': 'account.move',
                'default_source_res_id': self.id,
            },
        }
