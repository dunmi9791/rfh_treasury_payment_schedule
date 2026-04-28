from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = ['purchase.order', 'rfh.treasury.payment.origin.mixin']
    _name = 'purchase.order'

    treasury_payment_state = fields.Selection([
        ('not_queued', 'Not Queued'),
        ('queued', 'Queued'),
        ('scheduled', 'Scheduled'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'),
    ], string='Treasury Status', default='not_queued', tracking=True)

    treasury_payment_count = fields.Integer(
        string='Treasury Payments',
        compute='_compute_treasury_payment_count',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def _compute_treasury_payment_count(self):
        Schedule = self.env['rfh.treasury.payment.schedule']
        for po in self:
            po.treasury_payment_count = Schedule.search_count([
                ('source_model', '=', 'purchase.order'),
                ('source_res_id', '=', po.id),
            ])

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------

    def _prepare_treasury_payment_schedule_vals(self):
        self.ensure_one()
        if self.state not in ('purchase', 'done'):
            raise UserError(
                _('Only confirmed or done purchase orders can be queued for Treasury payment.')
            )
        due = self.date_planned.date() if self.date_planned else fields.Date.today()
        return {
            'source_model': 'purchase.order',
            'source_res_id': self.id,
            'source_reference': self.name,
            'partner_id': self.partner_id.id,
            'amount': self.amount_total,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'due_date': due,
            'description': _('Payment for Purchase Order %s', self.name),
            'state': 'queued',
        }

    def action_create_treasury_payment_schedule(self):
        self.ensure_one()
        if self.state not in ('purchase', 'done'):
            raise UserError(
                _('Only confirmed purchase orders can be queued for Treasury payment.')
            )
        result = super().action_create_treasury_payment_schedule()
        self.treasury_payment_state = 'queued'
        return result

    def action_treasury_payment_completed(self, payment_schedule):
        self.ensure_one()
        self.message_post(
            body=_('Treasury payment completed: %s', payment_schedule.name)
        )
        self.treasury_payment_state = 'paid'

    # ------------------------------------------------------------------
    # Smart button action
    # ------------------------------------------------------------------

    def action_view_treasury_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Treasury Payments'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [
                ('source_model', '=', 'purchase.order'),
                ('source_res_id', '=', self.id),
            ],
            'context': {
                'default_source_model': 'purchase.order',
                'default_source_res_id': self.id,
            },
        }
