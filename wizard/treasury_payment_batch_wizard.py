from odoo import api, fields, models, _
from odoo.exceptions import UserError


class TreasuryPaymentBatchWizard(models.TransientModel):
    _name = 'rfh.treasury.payment.batch.wizard'
    _description = 'Create Treasury Payment Batch from Selected Lines'

    scheduled_date = fields.Date(string='Scheduled Payment Date')
    journal_id = fields.Many2one(
        'account.journal',
        string='Payment Journal',
        domain="[('type', 'in', ['bank', 'cash'])]",
    )
    payment_reference = fields.Char(string='Batch Payment Reference')

    line_ids = fields.Many2many(
        'rfh.treasury.payment.schedule',
        relation='rfh_tps_batch_wizard_rel',
        column1='wizard_id',
        column2='schedule_id',
        string='Selected Payments',
        readonly=True,
    )
    line_count = fields.Integer(compute='_compute_line_count')
    total_amount = fields.Monetary(
        string='Total Amount',
        compute='_compute_total_amount',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )

    @api.depends('line_ids')
    def _compute_line_count(self):
        for wiz in self:
            wiz.line_count = len(wiz.line_ids)

    @api.depends('line_ids.amount')
    def _compute_total_amount(self):
        for wiz in self:
            wiz.total_amount = sum(wiz.line_ids.mapped('amount'))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            lines = self.env['rfh.treasury.payment.schedule'].browse(active_ids)
            invalid = lines.filtered(lambda l: l.state in ('paid', 'cancelled'))
            if invalid:
                raise UserError(
                    _('The following schedules are paid or cancelled and cannot be batched: %s',
                      ', '.join(invalid.mapped('name')))
                )
            res['line_ids'] = [(6, 0, lines.ids)]
        return res

    def action_create_batch(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('No payment schedule lines selected.'))

        already_batched = self.line_ids.filtered(
            lambda l: l.batch_id and l.batch_id.state not in ('cancelled',)
        )
        if already_batched:
            raise UserError(
                _('Some lines are already in an active batch: %s',
                  ', '.join(already_batched.mapped('name')))
            )

        batch = self.env['rfh.treasury.payment.batch'].create({
            'scheduled_date': self.scheduled_date,
            'journal_id': self.journal_id.id if self.journal_id else False,
            'payment_reference': self.payment_reference,
            'notes': _('Created from wizard. Lines: %s', ', '.join(self.line_ids.mapped('name'))),
        })

        self.line_ids.write({
            'batch_id': batch.id,
            'state': 'batched',
        })
        for line in self.line_ids:
            line.message_post(body=_('Added to batch %s.', batch.name))

        batch.message_post(
            body=_('%d payment lines added to this batch.', len(self.line_ids))
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Batch'),
            'res_model': 'rfh.treasury.payment.batch',
            'res_id': batch.id,
            'view_mode': 'form',
            'target': 'current',
        }
