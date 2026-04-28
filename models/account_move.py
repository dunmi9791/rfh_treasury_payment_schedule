from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = ['account.move', 'rfh.treasury.payment.origin.mixin']
    _name = 'account.move'

    treasury_payment_count = fields.Integer(
        string='Treasury Payments',
        compute='_compute_treasury_payment_count',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def _compute_treasury_payment_count(self):
        Schedule = self.env['rfh.treasury.payment.schedule']
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
        return super().action_create_treasury_payment_schedule()

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
